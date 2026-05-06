"""Aggregate Stage C grid results into per-model / per-task / per-agent stats.

Usage:
    python3 -m agentbench.aggregate <run_dir>

Outputs (under <run_dir>):
    stats.csv           — flat per-cell summary (one row per cell, with grade)
    per_model.md        — table: model × bench accuracy
    per_task.md         — table: task × pass-rate (across models/agents)
    per_agent.md        — pi-mono vs hermes-agent delta
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean


def load_results(run_dir: Path) -> list[dict]:
    out: list[dict] = []
    for p in sorted(run_dir.rglob("result.json")):
        try:
            d = json.loads(p.read_text())
            d["_path"] = str(p)
            out.append(d)
        except json.JSONDecodeError:
            continue
    return out


def write_stats_csv(rows: list[dict], path: Path) -> None:
    fieldnames = ["agent", "bench", "model", "task_id", "mode", "ok", "grade_pass", "grade_score",
                  "verifier_n_passed", "verifier_n_failed", "verifier_raw_reward", "latency_s", "error"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fieldnames})


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def per_model_table(rows: list[dict]) -> str:
    by_model: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_model[r.get("model", "?")].append(r)
    body = []
    for model in sorted(by_model.keys()):
        cells = by_model[model]
        n = len(cells)
        n_ok = sum(1 for c in cells if c.get("ok"))
        n_pass = sum(1 for c in cells if c.get("grade_pass") is True)
        n_fail = sum(1 for c in cells if c.get("grade_pass") is False)
        n_unk = n - n_pass - n_fail
        avg_lat = mean(c.get("latency_s", 0) for c in cells) if cells else 0
        pass_rate = (n_pass / n * 100) if n else 0
        body.append([
            f"`{model}`",
            f"{n}",
            f"{n_ok}",
            f"{n_pass}",
            f"{n_fail}",
            f"{n_unk}",
            f"{pass_rate:.1f}%",
            f"{avg_lat:.0f}s",
        ])
    return _md_table(
        ["model", "cells", "ok", "pass", "fail", "?", "pass-rate", "avg-latency"],
        body,
    )


def per_task_table(rows: list[dict]) -> str:
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_task[r.get("task_id", "?")].append(r)
    body = []
    for task in sorted(by_task.keys()):
        cells = by_task[task]
        n = len(cells)
        n_pass = sum(1 for c in cells if c.get("grade_pass") is True)
        n_fail = sum(1 for c in cells if c.get("grade_pass") is False)
        rate = (n_pass / n * 100) if n else 0
        body.append([task, str(n), str(n_pass), str(n_fail), f"{rate:.0f}%"])
    return _md_table(["task", "cells", "pass", "fail", "pass-rate"], body)


def per_agent_table(rows: list[dict]) -> str:
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_agent[r.get("agent", "?")].append(r)
    body = []
    for agent in sorted(by_agent.keys()):
        cells = by_agent[agent]
        n = len(cells)
        n_pass = sum(1 for c in cells if c.get("grade_pass") is True)
        rate = (n_pass / n * 100) if n else 0
        avg_lat = mean(c.get("latency_s", 0) for c in cells) if cells else 0
        body.append([agent, str(n), str(n_pass), f"{rate:.1f}%", f"{avg_lat:.0f}s"])
    return _md_table(["agent", "cells", "pass", "pass-rate", "avg-latency"], body)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("run_dir", type=Path)
    args = p.parse_args()

    rows = load_results(args.run_dir)
    if not rows:
        print(f"no result.json under {args.run_dir}")
        return 1

    write_stats_csv(rows, args.run_dir / "stats.csv")

    md_model = per_model_table(rows)
    md_task = per_task_table(rows)
    md_agent = per_agent_table(rows)

    (args.run_dir / "per_model.md").write_text(f"# Per-model results\n\n{md_model}\n")
    (args.run_dir / "per_task.md").write_text(f"# Per-task results\n\n{md_task}\n")
    (args.run_dir / "per_agent.md").write_text(f"# Per-agent results\n\n{md_agent}\n")

    n_pass = sum(1 for r in rows if r.get("grade_pass") is True)
    n_fail = sum(1 for r in rows if r.get("grade_pass") is False)
    print(f"aggregated {len(rows)} cells -> {args.run_dir}")
    print(f"  pass={n_pass}  fail={n_fail}  unknown={len(rows) - n_pass - n_fail}")
    print(f"  per_model.md, per_task.md, per_agent.md, stats.csv written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
