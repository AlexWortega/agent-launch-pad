"""Re-grade an existing run by re-running adapters' grade() against saved trace.jsonl files."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .adapters.registry import ADAPTERS
from .verifier.parser import parse as parse_verifier


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run_dir", type=Path)
    args = p.parse_args()

    run_dir: Path = args.run_dir
    rows: list[dict] = []
    for result_path in sorted(run_dir.rglob("result.json")):
        cell = result_path.parent
        try:
            r = json.loads(result_path.read_text())
        except json.JSONDecodeError:
            continue
        bench = r.get("bench")
        adapter_cls = ADAPTERS.get(bench)
        if adapter_cls is None:
            continue
        adapter = adapter_cls()
        # Find the matching task by id
        tasks = adapter.load_tasks(50)
        task = next((t for t in tasks if t.id == r.get("task_id")), None)
        if task is None:
            tasks = adapter.load_tasks(1)
            task = tasks[0] if tasks else None
        if task is None:
            continue
        trace = cell / "trace.jsonl"
        # Prefer verifier artifacts (reward.txt / ctrf.json) for shell-env tasks
        v = parse_verifier(cell)
        if v.pass_ is not None:
            grade = type("G", (), {"pass_": v.pass_, "score": v.score, "reason": f"verifier: {v.reason}"})()
        else:
            grade = adapter.grade(task, trace)
        r["grade_pass"] = grade.pass_
        r["grade_score"] = grade.score
        r["grade_reason"] = grade.reason
        result_path.write_text(json.dumps(r, indent=2, ensure_ascii=False))
        rows.append({
            "agent": r.get("agent"),
            "bench": bench,
            "model": r.get("model"),
            "task_id": r.get("task_id"),
            "ok": r.get("ok"),
            "grade_pass": grade.pass_,
            "grade_score": grade.score,
            "grade_reason": (grade.reason or "")[:200],
            "latency_s": r.get("latency_s"),
        })

    out = run_dir / "summary_graded.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["agent", "bench", "model", "task_id", "ok", "grade_pass", "grade_score", "grade_reason", "latency_s"])
        w.writeheader()
        w.writerows(rows)
    n_pass = sum(1 for r in rows if r["grade_pass"] is True)
    n_fail = sum(1 for r in rows if r["grade_pass"] is False)
    n_null = sum(1 for r in rows if r["grade_pass"] is None)
    print(f"regraded {len(rows)} cells -> {out}")
    print(f"  pass={n_pass}  fail={n_fail}  unknown={n_null}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
