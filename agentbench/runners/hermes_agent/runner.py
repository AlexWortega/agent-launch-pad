from __future__ import annotations

import json
import re
from pathlib import Path

from .._base import AgentRunner, RunOutcome, Task
from ...infra.docker_pool import build_image, run_container
from ...infra.model_dispatcher import ModelKey
from ...infra.trace_schema import Event, write_jsonl


_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_TOOL_RESP_RE = re.compile(r"<tool_response>(.*?)</tool_response>", re.DOTALL)


class HermesAgentRunner(AgentRunner):
    name = "hermes-agent"
    image_tag = "agentbench/hermes-agent:latest"
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
        }
        binds = {str(work_dir.resolve()): "/work"}
        import hashlib
        suffix = hashlib.md5(f"{mk.model}|{mk.key_alias}".encode()).hexdigest()[:8]
        container_name = f"hermes-{task.bench}-{task.id}-{suffix}"[:60].replace("/", "_").replace(":", "_")

        rc, stdout, stderr = await run_container(
            self.image_tag,
            name=container_name,
            env=env,
            binds=binds,
            runtime=runtime,
            timeout_s=timeout_s,
        )

        events: list[Event] = [Event(type="meta", agent=self.name, model=mk.model, bench=task.bench, task_id=task.id)]

        # Prefer the structured trajectory; sample is single JSON, trajectory_samples.jsonl is per-line.
        traj_json = work_dir / "hermes_trajectory.json"
        traj_jsonl = work_dir / "hermes_trajectory.jsonl"
        raw_err = work_dir / "trace.raw.jsonl"
        if traj_json.exists():
            try:
                raw = json.loads(traj_json.read_text(encoding="utf-8", errors="replace"))
                events.extend(_map_hermes_record(raw))
            except json.JSONDecodeError:
                pass
        elif traj_jsonl.exists():
            for line in traj_jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.extend(_map_hermes_record(raw))
        elif raw_err.exists():
            for line in raw_err.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(Event(type="error", **json.loads(line)))
                except json.JSONDecodeError:
                    pass
        else:
            events.append(Event(type="error", kind="crash", detail=f"no hermes trajectory; rc={rc}"))

        unified_path = work_dir / "trace.jsonl"
        write_jsonl(unified_path, events)
        ok = rc == 0 and any(e.type in ("message", "tool_call") for e in events)
        err: str | None = None
        if rc != 0:
            err = (stderr or "")[-1000:] or f"rc={rc}"
        return RunOutcome(ok=ok, error=err, trace_path=str(unified_path), raw_stdout=stdout[-2000:], raw_stderr=stderr[-2000:])


def _map_hermes_record(raw: dict) -> list[Event]:
    """Hermes trajectory format: {'conversations': [{'from': 'human|gpt|tool', 'value': '...'}, ...]} per row,
    OR per-line sharegpt entries {'from': '...', 'value': '...'}.
    Tool calls are wrapped in <tool_call>...</tool_call> XML inside 'value'.
    """
    out: list[Event] = []
    convs = raw.get("conversations") or raw.get("conversations_sharegpt") or [raw]
    role_map = {"system": "system", "human": "user", "gpt": "assistant", "tool": "tool"}
    for entry in convs:
        if not isinstance(entry, dict):
            continue
        frm = entry.get("from") or entry.get("role")
        val = entry.get("value") or entry.get("content") or ""
        if not frm:
            continue
        if "<tool_call>" in val:
            for m in _TOOL_CALL_RE.finditer(val):
                payload = m.group(1).strip()
                try:
                    p = json.loads(payload)
                except json.JSONDecodeError:
                    p = {"raw": payload}
                out.append(Event(type="tool_call", name=p.get("name"), args=p.get("arguments") or p.get("args") or {}, id=p.get("id")))
            stripped = _TOOL_CALL_RE.sub("", val).strip()
            if stripped:
                out.append(Event(type="message", role=role_map.get(frm, frm), content=stripped))
        elif "<tool_response>" in val:
            for m in _TOOL_RESP_RE.finditer(val):
                payload = m.group(1).strip()
                try:
                    p = json.loads(payload)
                except json.JSONDecodeError:
                    p = {"stdout": payload}
                out.append(Event(type="tool_result", id=p.get("id"), ok=p.get("ok", True), stdout=p.get("stdout") or p.get("content"), stderr=p.get("stderr")))
        else:
            out.append(Event(type="message", role=role_map.get(frm, frm), content=val))
    return out
