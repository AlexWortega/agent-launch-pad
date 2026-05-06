from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import yaml

from .adapters.registry import ADAPTERS, all_benches
from .infra.docker_pool import image_exists
from .infra.model_dispatcher import ModelDispatcher, ModelKey
from .infra.orchestrator import Cell, CellResult, Orchestrator
from .infra.trace_schema import read_jsonl, to_sharegpt
from .runners.hermes_agent import HermesAgentRunner
from .runners.pi_mono import PiMonoRunner
from .runners.shell_env import ShellEnvRunner
from .verifier.parser import parse as parse_verifier


CONFIG_PATH = Path(__file__).parent / "config.yaml"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="agentbench: run pi-mono/hermes-agent across benches × models")
    p.add_argument("--tasks-per-bench", type=int, default=1)
    p.add_argument("--agents", default="pi-mono,hermes-agent")
    p.add_argument("--models", default="openai/gpt-oss-120b:free", help="comma-separated model ids; use 'all' for all in chain")
    p.add_argument("--benches", default="all", help="comma-separated bench names or 'all'")
    p.add_argument("--max-parallel", type=int, default=None)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--runtime", default=None, help="docker runtime, e.g. 'kata' for Kata Containers")
    p.add_argument("--task-ids", default=None, help="comma-separated task ids to filter (after adapter loads)")
    return p.parse_args()


def resolve_models(spec: str, dispatcher: ModelDispatcher) -> list[str]:
    if spec == "all":
        return [mk.model for mk in dispatcher.primary]
    return [s.strip() for s in spec.split(",") if s.strip()]


def resolve_benches(spec: str) -> list[str]:
    if spec == "all":
        return all_benches()
    return [s.strip() for s in spec.split(",") if s.strip()]


async def main() -> int:
    args = parse_args()
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    runs_root = Path(cfg["paths"]["runs_root"])
    max_parallel = args.max_parallel or cfg["orchestrator"]["max_parallel"]
    base_url = cfg["openrouter"]["base_url"]

    dispatcher = ModelDispatcher.from_config(CONFIG_PATH)
    orch = Orchestrator(dispatcher, max_parallel=max_parallel, runs_root=runs_root)

    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    runner_map = {"pi-mono": PiMonoRunner(), "hermes-agent": HermesAgentRunner()}
    for a in agents:
        if a not in runner_map:
            print(f"[run] unknown agent: {a}")
            return 2
        if not image_exists(runner_map[a].image_tag):
            print(f"[run] image missing: {runner_map[a].image_tag}; building ...")
            ok, log = runner_map[a].build()
            if not ok:
                print(f"[run] BUILD FAILED for {a}\n{log[-2000:]}")
                return 3

    benches = resolve_benches(args.benches)
    models = resolve_models(args.models, dispatcher)
    print(f"[run] grid: {len(agents)} agents × {len(benches)} benches × {len(models)} models × {args.tasks_per_bench} tasks/bench")
    print(f"[run] runs_dir: {orch.run_dir}")

    cells: list[Cell] = []
    task_cache: dict[str, list] = {}
    task_id_filter = set(s.strip() for s in args.task_ids.split(",") if s.strip()) if args.task_ids else None
    for bench in benches:
        if bench not in ADAPTERS:
            print(f"[run] skipping unknown bench: {bench}")
            continue
        adapter = ADAPTERS[bench]()
        # When filtering by id we want to scan the full corpus so the filter can match anything.
        load_n = 200 if task_id_filter else args.tasks_per_bench
        tasks = adapter.load_tasks(load_n)
        if task_id_filter:
            tasks = [t for t in tasks if t.id in task_id_filter]
        tasks = tasks[:args.tasks_per_bench] if not task_id_filter else tasks
        task_cache[bench] = tasks
        for t in tasks:
            for agent in agents:
                for model in models:
                    cells.append(Cell(agent=agent, bench=bench, model=model, task_id=t.id, extra={"prompt": t.prompt[:120]}))

    async def executor(cell: Cell, mk: ModelKey, cdir: Path) -> CellResult:
        adapter = ADAPTERS[cell.bench]()
        # Find the matching cached task
        task = next((t for t in task_cache[cell.bench] if t.id == cell.task_id), None)
        if task is None:
            return CellResult(cell=cell, ok=False, error="task not in cache", latency_s=0.0, artifacts_dir=str(cdir))
        # Pick runner: shell-env tasks use the per-task image; prompt-only uses agent image.
        if task.mode == "shell-env":
            runner = ShellEnvRunner(cell.agent)
        else:
            runner = runner_map[cell.agent]
        t0 = time.time()
        # Per-model base_url override (e.g. local vLLM, llama.cpp server) wins over global default.
        effective_base_url = mk.base_url or base_url
        outcome = await runner.run(task, mk, cdir, timeout_s=args.timeout, base_url=effective_base_url, runtime=args.runtime)
        latency = time.time() - t0
        # Grading: shell-env uses verifier artifacts (reward.txt/ctrf.json).
        # prompt-only falls back to adapter heuristics.
        verifier_grade = None
        if task.mode == "shell-env":
            verifier_grade = parse_verifier(cdir)
        try:
            if verifier_grade is not None and verifier_grade.pass_ is not None:
                grade = type("G", (), {
                    "pass_": verifier_grade.pass_,
                    "score": verifier_grade.score,
                    "reason": f"verifier: {verifier_grade.reason}",
                    "extra": {"n_passed": verifier_grade.n_passed, "n_failed": verifier_grade.n_failed},
                })()
            else:
                grade = adapter.grade(task, Path(outcome.trace_path))
        except Exception as e:
            grade = type("G", (), {"pass_": None, "score": None, "reason": f"grader crash: {e!r}", "extra": {}})()
        # If container died mid-flight (timeout/crash), files inside cdir may be root-owned.
        # Reclaim ownership so we can write result.json + sharegpt.
        try:
            import os, subprocess
            subprocess.run(["sudo", "-n", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(cdir)],
                           capture_output=True, timeout=5)
        except Exception:
            pass
        # Build sharegpt + result.json side artifacts
        try:
            ev = read_jsonl(Path(outcome.trace_path))
            sg = to_sharegpt(ev)
            (cdir / "trajectory.sharegpt.json").write_text(json.dumps({"conversations": sg}, ensure_ascii=False, indent=2))
        except Exception as e:
            (cdir / "sharegpt.error").write_text(str(e))
        result_payload = {
            "ok": outcome.ok,
            "error": outcome.error,
            "latency_s": latency,
            "model": mk.model,
            "agent": cell.agent,
            "bench": cell.bench,
            "task_id": cell.task_id,
            "mode": task.mode,
            "grade_pass": grade.pass_,
            "grade_score": grade.score,
            "grade_reason": grade.reason,
        }
        if verifier_grade is not None:
            result_payload["verifier_raw_reward"] = verifier_grade.raw_reward
            result_payload["verifier_n_passed"] = verifier_grade.n_passed
            result_payload["verifier_n_failed"] = verifier_grade.n_failed
        (cdir / "result.json").write_text(json.dumps(result_payload, indent=2))
        (cdir / "stdout.log").write_text(outcome.raw_stdout)
        (cdir / "stderr.log").write_text(outcome.raw_stderr)
        return CellResult(cell=cell, ok=outcome.ok, error=outcome.error, latency_s=latency, artifacts_dir=str(cdir))

    results = await orch.run_grid(cells, executor)
    n_ok = sum(1 for r in results if r.ok)
    print(f"\n[run] {n_ok}/{len(results)} cells succeeded; manifest -> {orch.run_dir / 'manifest.json'}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
