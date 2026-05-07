"""scienceagentbench adapter, shell-env mode (shared-base + bind-mount).

All 102 SAB tasks share the same conda+pytorch+deepchem env (~18 GB) — only
the dataset directory and instruction.md differ. Instead of building 102 ~19 GB
images we build ONE base image and bind-mount per-task data at runtime:

  /instruction.md                        ← {task_dir}/instruction.md
  /tests/                                ← {task_dir}/tests/
  /testbed/benchmark/datasets/<NAME>/    ← {task_dir}/environment/datasets/

The dataset path's <NAME> is parsed out of the task's environment/Dockerfile
(line `COPY datasets/ /testbed/benchmark/datasets/<NAME>/`).

Tests use:
  - WORKDIR /testbed (NOT /app like terminal-bench)
  - Tests at /tests/ (bind-mount)
  - Reward written to /testbed/metadata.json (or reward.txt) — verifier.parser
    handles both.
"""
from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

from ._base import BenchAdapter, GradeResult
from ..runners._base import Task


HARBOR_CACHE = Path("/home/alexw/.cache/harbor/tasks/packages/scienceagentbench")
SHARED_BASE_IMAGE = "agentbench/scienceagentbench-base:latest"
_COPY_RE = re.compile(r"^COPY\s+datasets/\s+(/testbed/benchmark/datasets/[^\s/]+/?)\s*$", re.M)


def _image_exists(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0


def _extract_dataset_path(task_dir: Path) -> str | None:
    """Read the task's environment/Dockerfile and extract the COPY destination."""
    df = task_dir / "environment" / "Dockerfile"
    if not df.is_file():
        return None
    try:
        m = _COPY_RE.search(df.read_text())
    except Exception:
        return None
    if not m:
        return None
    p = m.group(1).rstrip("/")
    return p


def _task_dirs() -> list[Path]:
    out: list[Path] = []
    if not HARBOR_CACHE.exists():
        return out
    for entry in sorted(HARBOR_CACHE.iterdir(), key=lambda p: int(p.name.split("_")[-1]) if p.name.split("_")[-1].isdigit() else 0):
        if not entry.is_dir():
            continue
        shas = sorted([d for d in entry.iterdir() if d.is_dir()])
        if shas:
            out.append(shas[-1])
    return out


class ScienceAgentBenchAdapter(BenchAdapter):
    name = "scienceagentbench"
    mode = "shell-env"
    hub_dataset = "scienceagentbench/scienceagentbench"

    def _grade_impl(self, task: Task, events: list[dict]) -> GradeResult:
        # Only used as fallback if verifier didn't produce reward.txt — return None signal.
        return GradeResult(pass_=None, reason="no fallback heuristic for sab")

    def load_tasks(self, n: int = 1) -> list[Task]:
        # Need the shared base image to be built. If missing, return [] — caller skips.
        if not _image_exists(SHARED_BASE_IMAGE):
            return []
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
            tests_dir = d / "tests"
            datasets_dir = d / "environment" / "datasets"
            ds_dest_path = _extract_dataset_path(d)
            if not (instr_path.exists() and tests_dir.is_dir() and datasets_dir.is_dir() and ds_dest_path):
                continue
            full_name = meta.get("task", {}).get("name", "")
            short = full_name.split("/", 1)[-1] if "/" in full_name else d.parent.name
            slug = short.lower()
            agent_to = int(meta.get("agent", {}).get("timeout_sec", 600))
            verif_to = int(meta.get("verifier", {}).get("timeout_sec", 600))
            # task.toml lists 16 GiB by default but observed peak across 162 cells is ~260 MiB.
            # 4 GiB gives 16× headroom while letting us pack more parallel VMs into host RAM.
            mem_mb = min(int(meta.get("environment", {}).get("memory_mb", 4096)), 4096)
            cpus = int(meta.get("environment", {}).get("cpus", 2))
            tasks.append(Task(
                id=slug,
                bench=self.name,
                prompt=instr_path.read_text(encoding="utf-8"),
                mode="shell-env",
                env_image=SHARED_BASE_IMAGE,
                cwd="/testbed",
                verifier_cwd="/testbed",
                verifier_cmd=["bash", "/tests/test.sh"],
                reward_path="/testbed/metadata.json",
                # 20% of cells were eating the full 1800s timeout without solving;
                # cap at 900s so long tails don't starve other cells.
                agent_timeout_s=min(agent_to, 900),
                verifier_timeout_s=min(verif_to, 600),
                memory_mb=mem_mb,
                cpus=cpus,
                # Bind-mount per-task data over the shared base.
                workspace_files={
                    "/instruction.md":    str(instr_path),
                    "/tests":             str(tests_dir),
                    ds_dest_path:         str(datasets_dir),
                },
            ))
        return tasks
