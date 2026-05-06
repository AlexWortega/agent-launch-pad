"""Shell-env preflight: verify each per-task image boots, agent dial-tones, verifier runs.

Sends a tiny prompt 'Reply OK and exit' to each agent in each per-task image, with
runtime=kata. Counts cells where:
  1. Container started (no Docker errors)
  2. Agent emitted at least one event
  3. Verifier ran (verifier.log non-empty)
"""
from __future__ import annotations

import asyncio
import csv
import time
from pathlib import Path

from .adapters.terminal_bench import TerminalBenchAdapter
from .infra.docker_pool import image_exists
from .infra.model_dispatcher import ModelDispatcher
from .infra.trace_schema import read_jsonl, validate_trace
from .runners.shell_env import ShellEnvRunner


CONFIG_PATH = Path(__file__).parent / "config.yaml"
RUNTIME = "kata"


async def _cell(agent: str, env_image: str, task_id: str, dispatcher: ModelDispatcher, root: Path) -> dict:
    runner = ShellEnvRunner(agent)
    mk = await dispatcher.acquire("openai/gpt-oss-120b:free")
    work_dir = root / agent / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    # Use the real adapter task but override prompt to a tiny one
    adapter = TerminalBenchAdapter()
    tasks = adapter.load_tasks(50)
    task = next((t for t in tasks if t.id == task_id), None)
    if task is None:
        return {"agent": agent, "task_id": task_id, "ok": False, "trace_ok": False, "verif_ok": False, "latency_s": 0.0, "error": "task not found"}
    task.prompt = "Reply with the single word OK and exit immediately. Do not perform any tools or file operations."
    t0 = time.time()
    out = await runner.run(task, mk, work_dir, timeout_s=120, runtime=RUNTIME)
    latency = time.time() - t0
    trace_ok, _ = validate_trace(read_jsonl(Path(out.trace_path))) if Path(out.trace_path).exists() else (False, "no trace")
    verif_ok = (work_dir / "verifier.log").exists() and (work_dir / "verifier.log").stat().st_size > 0
    return {"agent": agent, "task_id": task_id, "ok": out.ok, "trace_ok": trace_ok, "verif_ok": verif_ok, "latency_s": latency, "error": (out.error or "")[:200]}


async def main() -> int:
    dispatcher = ModelDispatcher.from_config(CONFIG_PATH)
    adapter = TerminalBenchAdapter()
    tasks = adapter.load_tasks(50)
    if not tasks:
        print("[preflight] no shell-env tasks found (no per-task images built yet)")
        return 1

    root = Path(__file__).parent / "runs" / "preflight_shell_env" / time.strftime("%Y-%m-%d__%H-%M-%S", time.gmtime())
    root.mkdir(parents=True, exist_ok=True)

    cells = [(a, t.id) for a in ("pi-mono", "hermes-agent") for t in tasks]
    sem = asyncio.Semaphore(16)

    async def _wrap(agent, task_id):
        async with sem:
            try:
                row = await _cell(agent, "", task_id, dispatcher, root)
            except Exception as e:
                row = {"agent": agent, "task_id": task_id, "ok": False, "trace_ok": False, "verif_ok": False, "latency_s": 0.0, "error": f"crash: {e!r}"[:200]}
            print(f"[preflight] {row['agent']:<14} | {row['task_id']:<22} ok={row['ok']!s:<5} trace={row['trace_ok']} verif={row['verif_ok']} ({row['latency_s']:.1f}s) {row['error'][:80]}")
            return row

    rows = await asyncio.gather(*[_wrap(a, t) for a, t in cells])
    csv_path = root / "preflight.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["agent", "task_id", "ok", "trace_ok", "verif_ok", "latency_s", "error"])
        w.writeheader()
        w.writerows(rows)
    n_pass = sum(1 for r in rows if r["ok"] and r["trace_ok"] and r["verif_ok"])
    print(f"\n[preflight] {n_pass}/{len(rows)} cells fully green -> {csv_path}")
    return 0 if n_pass == len(rows) else 1


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
