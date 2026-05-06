"""Runner for shell-env tasks: spawns task's per-task image with agent inside.

Unlike PiMonoRunner / HermesAgentRunner (which run a generic agent image),
ShellEnvRunner uses task.env_image — a pre-baked image that already has:
  - the task's environment (data, tools)
  - both agents (pi-coding-agent, hermes-agent) installed
  - tests/ + instruction.md COPYed in
  - /agentbench-entrypoint.sh that runs agent → verifier → exfiltrate
"""
from __future__ import annotations

import json
from pathlib import Path

from .._base import AgentRunner, RunOutcome, Task
from ...infra.docker_pool import run_container
from ...infra.model_dispatcher import ModelKey
from ...infra.trace_schema import Event, write_jsonl


class ShellEnvRunner(AgentRunner):
    """One runner instance per agent kind (pi-mono / hermes-agent), but image
    is taken from task.env_image rather than self.image_tag."""

    def __init__(self, agent_name: str):
        if agent_name not in ("pi-mono", "hermes-agent"):
            raise ValueError(f"unsupported agent: {agent_name}")
        self.name = agent_name
        # Map our agent names to entrypoint AGENT env values
        self._agent_env = "pi" if agent_name == "pi-mono" else "hermes"

    def build(self) -> tuple[bool, str]:
        # Per-task images are built upstream by build_task_image.py
        return True, "shell-env: per-task images built externally"

    async def run(
        self,
        task: Task,
        mk: ModelKey,
        work_dir: Path,
        *,
        timeout_s: float = 600.0,
        base_url: str = "https://openrouter.ai/api/v1",
        runtime: str | None = None,
    ) -> RunOutcome:
        if task.mode != "shell-env":
            raise ValueError(f"ShellEnvRunner requires task.mode='shell-env', got {task.mode!r}")
        if not task.env_image:
            raise ValueError(f"task.env_image required for shell-env tasks; task={task.id}")

        work_dir.mkdir(parents=True, exist_ok=True)

        env = {
            "AGENT": self._agent_env,
            "MODEL_ID": mk.model,
            "OPENROUTER_API_KEY": mk.api_key,
            "OPENROUTER_BASE_URL": base_url,
            "WORK": "/work",
        }
        binds = {str(work_dir.resolve()): "/work"}
        # Inject any per-task workspace files (e.g. data not baked into image)
        for ctr_path, host_path in (task.workspace_files or {}).items():
            host = Path(host_path).resolve()
            binds[str(host)] = ctr_path

        # Effective timeout: agent_timeout + verifier_timeout + 60s buffer for boot
        eff_timeout = min(
            timeout_s,
            float(task.agent_timeout_s + task.verifier_timeout_s + 60),
        )

        # Include a hash of model+key so 7 models × N tasks don't collide on names.
        import hashlib
        suffix = hashlib.md5(f"{mk.model}|{mk.key_alias}".encode()).hexdigest()[:8]
        container_name = f"{self._agent_env}-{task.bench}-{task.id}-{suffix}"[:60].replace("/", "_").replace(":", "_")

        rc, stdout, stderr = await run_container(
            task.env_image,
            name=container_name,
            env=env,
            binds=binds,
            runtime=runtime,
            timeout_s=eff_timeout,
        )

        # Reclaim ownership of work_dir from container's root user (in case entrypoint's
        # final chmod was preempted by timeout/crash).
        try:
            import os, subprocess
            subprocess.run(["sudo", "-n", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(work_dir.resolve())],
                           capture_output=True, timeout=5)
        except Exception:
            pass

        # Build unified trace
        events: list[Event] = [Event(type="meta", agent=self.name, model=mk.model, bench=task.bench, task_id=task.id)]

        # pi raw events
        raw_pi = work_dir / "trace.raw.jsonl"
        if self._agent_env == "pi" and raw_pi.exists():
            from ..pi_mono.runner import _map_pi_event
            for line in raw_pi.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.extend(_map_pi_event(raw))

        # hermes trajectory
        hermes_json = work_dir / "hermes_trajectory.json"
        if self._agent_env == "hermes" and hermes_json.exists():
            from ..hermes_agent.runner import _map_hermes_record
            try:
                raw = json.loads(hermes_json.read_text(encoding="utf-8", errors="replace"))
                events.extend(_map_hermes_record(raw))
            except json.JSONDecodeError:
                pass

        # If neither produced anything, surface stderr as error event
        if len(events) == 1:
            stderr_path = work_dir / "agent.stderr"
            tail = stderr_path.read_text(errors="replace")[-500:] if stderr_path.exists() else (stderr or "")[-500:]
            events.append(Event(type="error", kind="crash", detail=f"no agent trace; rc={rc}; stderr-tail={tail!r}"))

        unified = work_dir / "trace.jsonl"
        # Replace the entrypoint's pre-written meta-only trace.jsonl with the full thing
        write_jsonl(unified, events)

        ok = rc == 0 and any(e.type in ("message", "tool_call") for e in events)
        err: str | None = None
        if rc != 0:
            err = (stderr or "")[-1000:] or f"rc={rc}"

        return RunOutcome(
            ok=ok,
            error=err,
            trace_path=str(unified),
            raw_stdout=stdout[-2000:],
            raw_stderr=stderr[-2000:],
        )
