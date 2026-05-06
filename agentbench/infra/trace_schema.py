from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int((time.time() % 1) * 1000):03d}Z"


@dataclass
class Event:
    type: str
    ts: str = field(default_factory=_now)
    role: str | None = None
    content: str | None = None
    reasoning: str | None = None
    name: str | None = None
    args: dict[str, Any] | None = None
    id: str | None = None
    ok: bool | None = None
    stdout: str | None = None
    stderr: str | None = None
    kind: str | None = None
    detail: str | None = None
    agent: str | None = None
    model: str | None = None
    bench: str | None = None
    task_id: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def write_jsonl(path: Path, events: Iterable[Event]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def validate_trace(events: list[dict[str, Any]]) -> tuple[bool, str]:
    if not events:
        return False, "empty trace"
    has_meta = any(e.get("type") == "meta" for e in events)
    has_msg_or_call = any(e.get("type") in ("message", "tool_call") for e in events)
    if not has_meta:
        return False, "missing meta event"
    if not has_msg_or_call:
        return False, "no message or tool_call events"
    return True, "ok"


def to_sharegpt(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for ev in events:
        t = ev.get("type")
        if t == "message":
            role = ev.get("role")
            mapping = {"system": "system", "user": "human", "assistant": "gpt", "tool": "tool"}
            out.append({"from": mapping.get(role, role or "gpt"), "value": ev.get("content") or ""})
        elif t == "tool_call":
            payload = json.dumps({"name": ev.get("name"), "args": ev.get("args") or {}}, ensure_ascii=False)
            out.append({"from": "gpt", "value": f"<tool_call>{payload}</tool_call>"})
        elif t == "tool_result":
            payload = json.dumps({"id": ev.get("id"), "ok": ev.get("ok"), "stdout": ev.get("stdout"), "stderr": ev.get("stderr")}, ensure_ascii=False)
            out.append({"from": "tool", "value": f"<tool_response>{payload}</tool_response>"})
    return out
