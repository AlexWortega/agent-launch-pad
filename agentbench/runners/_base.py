from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..infra.model_dispatcher import ModelKey


@dataclass
class Task:
    id: str
    bench: str
    prompt: str
    mode: str = "prompt-only"                       # "prompt-only" | "shell-env"
    env_image: str | None = None                    # for shell-env: per-task image (agentbench/<bench>/<slug>:latest)
    files: dict[str, str] | None = None
    grader_payload: dict[str, Any] | None = None
    cwd: str = "/app"                               # shell-env: agent's working directory inside the env
    workspace_files: dict[str, str] = field(default_factory=dict)   # path-in-container -> host source path
    verifier_cmd: list[str] | None = None           # shell-env: command run after agent finishes (e.g. ["bash", "/tests/test.sh"])
    reward_path: str | None = None                  # shell-env: path inside container to reward.txt (1=pass / 0=fail)
    agent_timeout_s: int = 600
    verifier_timeout_s: int = 600


@dataclass
class RunOutcome:
    ok: bool
    error: str | None
    trace_path: str
    raw_stdout: str
    raw_stderr: str


class AgentRunner:
    name: str = "base"
    image_tag: str = "agentbench/base:latest"
    context_dir: Path = Path(__file__).parent

    def build(self) -> tuple[bool, str]:
        raise NotImplementedError

    async def run(self, task: Task, mk: ModelKey, work_dir: Path, *, timeout_s: float = 600.0, base_url: str = "https://openrouter.ai/api/v1", runtime: str | None = None) -> RunOutcome:
        raise NotImplementedError
