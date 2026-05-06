from __future__ import annotations

import json
import os
from pathlib import Path

from .._base import AgentRunner, RunOutcome, Task
from ...infra.docker_pool import build_image, run_container
from ...infra.model_dispatcher import ModelKey
from ...infra.trace_schema import Event, write_jsonl


class PiMonoRunner(AgentRunner):
    name = "pi-mono"
    image_tag = "agentbench/pi-mono:latest"
    context_dir = Path(__file__).parent

    def build(self) -> tuple[bool, str]:
        res = build_image(self.context_dir, self.image_tag)
        return res.ok, res.log

    async def run(self, task: Task, mk: ModelKey, work_dir: Path, *, timeout_s: float = 600.0, base_url: str = "https://openrouter.ai/api/v1", runtime: str | None = None) -> RunOutcome:
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "task.json").write_text(json.dumps({"id": task.id, "prompt": task.prompt, "bench": task.bench}))

        env = {
            "MODEL_ID": mk.model,
            "OPENROUTER_API_KEY": mk.api_key,
            "OPENROUTER_BASE_URL": base_url,
            "PI_OFFLINE": "1",
        }
        binds = {str(work_dir.resolve()): "/work"}
        import hashlib
        suffix = hashlib.md5(f"{mk.model}|{mk.key_alias}".encode()).hexdigest()[:8]
        container_name = f"pi-{task.bench}-{task.id}-{suffix}"[:60].replace("/", "_").replace(":", "_")

        rc, stdout, stderr = await run_container(
            self.image_tag,
            name=container_name,
            env=env,
            binds=binds,
            runtime=runtime,
            timeout_s=timeout_s,
        )

        raw_path = work_dir / "trace.raw.jsonl"
        unified_path = work_dir / "trace.jsonl"
        events: list[Event] = []
        events.append(Event(type="meta", agent=self.name, model=mk.model, bench=task.bench, task_id=task.id))
        if raw_path.exists():
            for line in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.extend(_map_pi_event(raw))
        else:
            events.append(Event(type="error", kind="crash", detail=f"no raw trace; rc={rc}"))

        write_jsonl(unified_path, events)
        ok = rc == 0 and any(e.type in ("message", "tool_call") for e in events)
        err: str | None = None
        if rc != 0:
            err = (stderr or "")[-1000:] or f"rc={rc}"
        return RunOutcome(ok=ok, error=err, trace_path=str(unified_path), raw_stdout=stdout[-2000:], raw_stderr=stderr[-2000:])


def _map_pi_event(raw: dict) -> list[Event]:
    """Map pi-coding-agent --mode json events to unified schema.

    Observed event types (pi v3 session schema):
      session, agent_start, turn_start, message_start, message_update, message_end,
      turn_end (carries `toolResults`), agent_end, error.
    For message_end the `message.content` is a list of {type, text|thinking|...} parts.
    For tool_use parts: {type:"tool_use", id, name, input}; results live in turn_end.toolResults.
    We only emit on terminal events (message_end / turn_end / error) to avoid duplication
    from message_update streams.
    """
    t = raw.get("type") or "unknown"
    out: list[Event] = []
    if t == "message_end":
        msg = raw.get("message") or {}
        role = msg.get("role", "assistant")
        parts = msg.get("content") or []
        text_chunks: list[str] = []
        thinking_chunks: list[str] = []
        for p in parts:
            if not isinstance(p, dict):
                continue
            pt = p.get("type")
            if pt == "text":
                text_chunks.append(p.get("text") or "")
            elif pt == "thinking":
                thinking_chunks.append(p.get("thinking") or "")
            elif pt == "tool_use":
                out.append(Event(
                    type="tool_call",
                    name=p.get("name"),
                    args=p.get("input"),
                    id=p.get("id"),
                ))
        if text_chunks or thinking_chunks:
            out.insert(0, Event(
                type="message",
                role=role,
                content="\n".join(text_chunks) or None,
                reasoning="\n".join(thinking_chunks) or None,
            ))
        return out
    if t == "turn_end":
        for tr in raw.get("toolResults") or []:
            if not isinstance(tr, dict):
                continue
            out.append(Event(
                type="tool_result",
                id=tr.get("toolUseId") or tr.get("id"),
                ok=not tr.get("isError", False),
                stdout=tr.get("content") if isinstance(tr.get("content"), str) else json.dumps(tr.get("content"), ensure_ascii=False),
            ))
        return out
    if t == "error":
        return [Event(type="error", kind=raw.get("kind") or "crash", detail=raw.get("detail") or raw.get("message"))]
    return []
