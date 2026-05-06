from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..runners._base import Task


@dataclass
class GradeResult:
    pass_: bool | None
    score: float | None = None
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def read_trace(trace_path: Path) -> list[dict]:
    if not trace_path.exists():
        return []
    out = []
    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def last_assistant_text(events: list[dict]) -> str:
    for ev in reversed(events):
        if ev.get("type") == "message" and ev.get("role") == "assistant" and ev.get("content"):
            return ev["content"]
    return ""


def all_tool_results_text(events: list[dict]) -> str:
    chunks: list[str] = []
    for ev in events:
        if ev.get("type") != "tool_result":
            continue
        s = ev.get("stdout")
        if isinstance(s, str):
            chunks.append(s)
        elif s is not None:
            chunks.append(json.dumps(s))
    return "\n".join(chunks)


def grade_final_answer(task: Task, events: list[dict]) -> GradeResult:
    """Match task.grader_payload['final_answer'] against last assistant message."""
    payload = task.grader_payload or {}
    truth = payload.get("final_answer")
    if not truth:
        return GradeResult(pass_=None, reason="no final_answer in grader_payload")
    final = last_assistant_text(events).strip()
    if not final:
        return GradeResult(pass_=False, score=0.0, reason="empty assistant final message")
    mode = payload.get("match", "exact")
    truth_norm = truth.strip().lower()
    final_norm = final.strip().lower()
    candidates = [truth_norm, *(s.lower() for s in payload.get("alts", []))]
    if mode == "exact":
        ok = any(final_norm == c for c in candidates)
    else:
        ok = any(c in final_norm for c in candidates)
    return GradeResult(pass_=ok, score=1.0 if ok else 0.0, reason=f"match={mode} truth={truth!r} got={final[:120]!r}")


def grade_tool_evidence(task: Task, events: list[dict], expects: list[str]) -> GradeResult:
    """Heuristic: PASS if every expected substring appears in any tool_result stdout."""
    if not expects:
        return GradeResult(pass_=None, reason="no expected substrings")
    blob = all_tool_results_text(events).lower()
    missing = [s for s in expects if s.lower() not in blob]
    if not missing:
        return GradeResult(pass_=True, score=1.0, reason=f"all {len(expects)} markers found in tool output")
    return GradeResult(
        pass_=False,
        score=(len(expects) - len(missing)) / max(1, len(expects)),
        reason=f"missing markers: {missing[:5]}",
    )


class BenchAdapter:
    name: str = "base"
    mode: str = "prompt-only"  # "prompt-only" | "shell-env"
    hub_dataset: str = ""

    def load_tasks(self, n: int = 1) -> list[Task]:
        raise NotImplementedError

    def grade(self, task: Task, trace_path: Path, env_state: dict | None = None) -> GradeResult:
        events = read_trace(trace_path)
        if not events:
            return GradeResult(pass_=False, score=0.0, reason="empty trace")
        return self._grade_impl(task, events)

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        return GradeResult(pass_=None, reason="no grader for this bench")
