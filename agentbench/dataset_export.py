"""Export grid results into a distill-ready dataset.

Filter: cells where grade_pass=True (or --include-fails for full corpus).

Outputs:
    distill_corpus.jsonl  — one row per cell with conversations + meta
    distill_corpus.parquet — same data via pandas (if installed)

Each row schema:
    {
      "agent": "pi-mono" | "hermes-agent",
      "bench": "terminal-bench-2",
      "task_id": "...",
      "model": "...",
      "grade_pass": true,
      "grade_score": 1.0,
      "latency_s": 73.0,
      "verifier_n_passed": 6,
      "verifier_n_failed": 0,
      "verifier_raw_reward": "1",
      "instruction": "<task prompt>",
      "conversations": [{"from": "...", "value": "..."}, ...],   # sharegpt
    }
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def export(run_dir: Path, *, include_fails: bool = False) -> tuple[Path, Path | None]:
    rows: list[dict] = []
    for result_path in sorted(run_dir.rglob("result.json")):
        try:
            r = json.loads(result_path.read_text())
        except json.JSONDecodeError:
            continue
        cell = result_path.parent
        if not include_fails and r.get("grade_pass") is not True:
            continue

        sharegpt_path = cell / "trajectory.sharegpt.json"
        if not sharegpt_path.exists():
            continue
        try:
            sg = json.loads(sharegpt_path.read_text())
        except json.JSONDecodeError:
            continue
        conversations = sg.get("conversations") if isinstance(sg, dict) else sg
        if not conversations:
            continue

        # Recover instruction prompt — for shell-env it lives in the entrypoint's task.json
        instruction = ""
        task_json = cell / "task.json"
        if task_json.exists():
            try:
                instruction = json.loads(task_json.read_text()).get("prompt", "")
            except json.JSONDecodeError:
                pass
        if not instruction:
            # Fallback: scan trace.jsonl for the user message
            trace = cell / "trace.jsonl"
            if trace.exists():
                for line in trace.read_text().splitlines():
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "message" and ev.get("role") == "user":
                        instruction = ev.get("content") or ""
                        break

        rows.append({
            "agent": r.get("agent"),
            "bench": r.get("bench"),
            "task_id": r.get("task_id"),
            "model": r.get("model"),
            "grade_pass": r.get("grade_pass"),
            "grade_score": r.get("grade_score"),
            "latency_s": r.get("latency_s"),
            "verifier_n_passed": r.get("verifier_n_passed"),
            "verifier_n_failed": r.get("verifier_n_failed"),
            "verifier_raw_reward": r.get("verifier_raw_reward"),
            "instruction": instruction,
            "conversations": conversations,
        })

    jsonl_path = run_dir / ("distill_corpus.jsonl" if not include_fails else "all_corpus.jsonl")
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    parquet_path: Path | None = None
    try:
        import pandas as pd
        df = pd.DataFrame(rows)
        # conversations is a list-of-dicts column; pandas + pyarrow handle this fine
        parquet_path = jsonl_path.with_suffix(".parquet")
        df.to_parquet(parquet_path, engine="pyarrow", index=False)
    except ImportError:
        print("pandas not available; skip parquet")
    except Exception as e:
        print(f"parquet write failed: {e!r}")

    print(f"exported {len(rows)} trajectories -> {jsonl_path}")
    if parquet_path:
        print(f"                              -> {parquet_path}")
    return jsonl_path, parquet_path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run_dir", type=Path)
    p.add_argument("--include-fails", action="store_true", help="export ALL trajectories (not just grade_pass=True)")
    args = p.parse_args()
    export(args.run_dir, include_fails=args.include_fails)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
