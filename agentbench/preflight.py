from __future__ import annotations

import asyncio
import csv
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from .adapters.registry import ADAPTERS
from .infra.docker_pool import build_image, image_exists
from .infra.model_dispatcher import ModelDispatcher
from .infra.orchestrator import Cell, CellResult, Orchestrator
from .infra.trace_schema import read_jsonl, validate_trace
from .runners.hermes_agent import HermesAgentRunner
from .runners.pi_mono import PiMonoRunner


CONFIG_PATH = Path(__file__).parent / "config.yaml"


def check_docker() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "docker not in PATH"
    proc = subprocess.run(["docker", "ps"], capture_output=True, text=True)
    if proc.returncode != 0:
        return False, f"docker ps failed: {proc.stderr}"
    return True, "ok"


def build_runners(force: bool = False) -> dict[str, tuple[bool, str]]:
    runners = {"pi-mono": PiMonoRunner(), "hermes-agent": HermesAgentRunner()}
    out: dict[str, tuple[bool, str]] = {}
    for name, r in runners.items():
        if not force and image_exists(r.image_tag):
            out[name] = (True, "image already present")
            continue
        print(f"[preflight] building {name} ({r.image_tag}) ...")
        ok, log = r.build()
        out[name] = (ok, log[-2000:] if not ok else "built")
        if not ok:
            print(f"[preflight] BUILD FAILED for {name}\n{log[-3000:]}")
    return out


async def run_smoke_cell(agent_name: str, bench_name: str, dispatcher: ModelDispatcher, run_dir: Path) -> tuple[bool, str, float, str]:
    runner = {"pi-mono": PiMonoRunner(), "hermes-agent": HermesAgentRunner()}[agent_name]
    adapter = ADAPTERS[bench_name]()
    tasks = adapter.load_tasks(1)
    if not tasks:
        return False, "no task from adapter", 0.0, ""
    task = tasks[0]
    # Override prompt to a hello-world for fastest preflight
    task.prompt = "Reply with the single word OK and nothing else."
    task.id = f"preflight-{task.id}"
    work_dir = run_dir / agent_name / bench_name / task.id
    work_dir.mkdir(parents=True, exist_ok=True)
    mk = await dispatcher.acquire("openai/gpt-oss-120b:free")
    t0 = time.time()
    outcome = await runner.run(task, mk, work_dir, timeout_s=180.0)
    latency = time.time() - t0
    trace = Path(outcome.trace_path)
    trace_ok, trace_msg = (False, "no trace file") if not trace.exists() else validate_trace(read_jsonl(trace))
    err = outcome.error or ("" if trace_ok else trace_msg)
    return outcome.ok and trace_ok, err, latency, str(work_dir)


async def main() -> int:
    print("[preflight] checking docker ...")
    ok, msg = check_docker()
    print(f"[preflight] docker: {ok} ({msg})")
    if not ok:
        return 2

    print("[preflight] building runner images ...")
    builds = build_runners()
    if not all(b[0] for b in builds.values()):
        print("[preflight] aborting: some images failed to build")
        for name, (ok, msg) in builds.items():
            print(f"  {name}: ok={ok}")
        return 3

    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    runs_root = Path(cfg["paths"]["runs_root"])
    run_dir = runs_root / "preflight" / time.strftime("%Y-%m-%d__%H-%M-%S", time.gmtime())
    run_dir.mkdir(parents=True, exist_ok=True)

    dispatcher = ModelDispatcher.from_config(CONFIG_PATH)

    rows: list[dict] = []
    sem = asyncio.Semaphore(cfg["orchestrator"]["max_parallel"])

    async def _cell(agent: str, bench: str) -> dict:
        async with sem:
            try:
                ok, err, latency, art = await run_smoke_cell(agent, bench, dispatcher, run_dir)
            except Exception as e:
                ok, err, latency, art = False, f"crash: {e!r}", 0.0, ""
            row = {"agent": agent, "bench": bench, "ok": ok, "latency_s": f"{latency:.1f}", "error": (err or "")[:300], "artifacts": art}
            print(f"[preflight] {agent:<14} | {bench:<22} | ok={ok} ({latency:.1f}s) {row['error'][:120]}")
            return row

    cells = [(a, b) for a in ("pi-mono", "hermes-agent") for b in ADAPTERS.keys()]
    rows = await asyncio.gather(*[_cell(a, b) for a, b in cells])

    out_csv = run_dir / "preflight.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["agent", "bench", "ok", "latency_s", "error", "artifacts"])
        w.writeheader()
        w.writerows(rows)
    n_ok = sum(1 for r in rows if r["ok"])
    print(f"\n[preflight] result: {n_ok}/{len(rows)} cells passed -> {out_csv}")
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
