"""terminal-bench-2 adapter, shell-env mode.

Loads tasks from the local Harbor cache at
    /home/alexw/.cache/harbor/tasks/packages/terminal-bench/<name>/<sha>/
Each task folder contains task.toml, instruction.md, tests/, environment/, solution/.

The expectation is that per-task images are built upstream
(agentbench.images.build_task_image), tagged
    agentbench/terminal-bench-2/<task_name>:latest

If an image is missing, the orchestrator returns an error for that cell.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from ._base import BenchAdapter, GradeResult, grade_tool_evidence
from ..runners._base import Task


def _image_exists(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0


HARBOR_CACHE = Path("/home/alexw/.cache/harbor/tasks/packages/terminal-bench")
IMAGE_NAMESPACE = "agentbench/terminal-bench-2"


def _task_dirs() -> list[Path]:
    """Each task has one or more sha-named subdirs; pick the lexicographically last (most recent)."""
    out: list[Path] = []
    if not HARBOR_CACHE.exists():
        return out
    for entry in sorted(HARBOR_CACHE.iterdir()):
        if not entry.is_dir():
            continue
        shas = sorted([d for d in entry.iterdir() if d.is_dir()])
        if shas:
            out.append(shas[-1])
    return out


def _slugify(task_name: str) -> str:
    return task_name.replace("/", "-").replace(":", "_").lower()


class TerminalBenchAdapter(BenchAdapter):
    name = "terminal-bench-2"
    mode = "shell-env"
    hub_dataset = "terminal-bench/terminal-bench-2"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        # Fallback for when verifier didn't produce reward.txt — heuristic on tool output
        return grade_tool_evidence(task, events, expects=[])

    def load_tasks(self, n: int = 1) -> list[Task]:
        """Only return tasks whose per-task image is already built locally.

        For smoke we control which images exist via build_smoke_set.py. Tasks
        without a built image are skipped silently — this avoids picking the
        first-alphabetical task and trying to docker pull it from a nonexistent
        registry.
        """
        dirs = _task_dirs()
        if not dirs:
            return []
        tasks: list[Task] = []
        for d in dirs:
            if len(tasks) >= n:
                break
            try:
                meta = tomllib.loads((d / "task.toml").read_text())
            except (FileNotFoundError, tomllib.TOMLDecodeError):
                continue
            instr_path = d / "instruction.md"
            if not instr_path.exists():
                continue
            full_name = meta.get("task", {}).get("name", "")
            short = full_name.split("/", 1)[-1] if "/" in full_name else d.parent.name
            slug = _slugify(short)
            tag = f"{IMAGE_NAMESPACE}/{slug}:latest"
            if not _image_exists(tag):
                continue
            agent_to = int(meta.get("agent", {}).get("timeout_sec", 600))
            verif_to = int(meta.get("verifier", {}).get("timeout_sec", 600))
            tasks.append(Task(
                id=slug,
                bench=self.name,
                prompt=instr_path.read_text(encoding="utf-8"),
                mode="shell-env",
                env_image=tag,
                cwd="/app",
                verifier_cmd=["bash", "/tests/test.sh"],
                reward_path="/logs/verifier/reward.txt",
                agent_timeout_s=min(agent_to, 900),
                verifier_timeout_s=min(verif_to, 900),
            ))
        return tasks
