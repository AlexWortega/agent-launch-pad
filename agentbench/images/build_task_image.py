"""Build a per-task agentbench image: TASK_ENV_IMAGE + agents + tests + entrypoint.

Usage (CLI):
    python -m agentbench.images.build_task_image --bench terminal-bench-2 \
        --task-dir /home/alexw/.cache/harbor/tasks/packages/terminal-bench/<task>/<sha> \
        --tag agentbench/terminal-bench-2/<slug>:latest

Programmatic:
    build(bench, task_dir, tag) -> BuildResult
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

THIS_DIR = Path(__file__).parent
TEMPLATE_DOCKERFILE = THIS_DIR / "Dockerfile.task-template"
TEMPLATE_ENTRYPOINT = THIS_DIR / "agentbench-entrypoint.sh"


@dataclass
class BuildResult:
    tag: str
    ok: bool
    log_tail: str
    env_image: str | None = None


def _read_task_meta(task_dir: Path) -> dict:
    """task.toml has [environment] docker_image and various timeouts."""
    toml_path = task_dir / "task.toml"
    with toml_path.open("rb") as f:
        return tomllib.load(f)


def build(bench: str, task_dir: Path, tag: str, *, no_cache: bool = False) -> BuildResult:
    task_dir = Path(task_dir).resolve()
    if not task_dir.is_dir():
        return BuildResult(tag=tag, ok=False, log_tail=f"task_dir not found: {task_dir}")

    meta = _read_task_meta(task_dir)
    env_image = meta.get("environment", {}).get("docker_image")
    if not env_image:
        return BuildResult(tag=tag, ok=False, log_tail="task.toml missing [environment].docker_image")

    # Stage build context: copy tests/, instruction.md, entrypoint, and Dockerfile
    import tempfile
    with tempfile.TemporaryDirectory(prefix="agentbench-build-") as ctx_str:
        ctx = Path(ctx_str)
        # 1. tests/
        src_tests = task_dir / "tests"
        if src_tests.is_dir():
            shutil.copytree(src_tests, ctx / "tests")
        else:
            (ctx / "tests").mkdir()
            (ctx / "tests" / "test.sh").write_text("#!/bin/sh\necho 'no tests dir; placeholder'\nexit 0\n")
        # 2. instruction.md
        instr_src = task_dir / "instruction.md"
        if instr_src.exists():
            shutil.copy(instr_src, ctx / "instruction.md")
        else:
            (ctx / "instruction.md").write_text("# missing instruction.md")
        # 3. entrypoint + Dockerfile
        shutil.copy(TEMPLATE_ENTRYPOINT, ctx / "agentbench-entrypoint.sh")
        shutil.copy(TEMPLATE_DOCKERFILE, ctx / "Dockerfile")

        # Build
        cmd = [
            "docker", "buildx", "build",
            "--load",
            "--build-arg", f"TASK_ENV_IMAGE={env_image}",
            "-t", tag,
            "-f", str(ctx / "Dockerfile"),
            str(ctx),
        ]
        if no_cache:
            cmd.insert(3, "--no-cache")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        log = (proc.stdout or "") + (proc.stderr or "")
        return BuildResult(tag=tag, ok=proc.returncode == 0, log_tail=log[-3000:], env_image=env_image)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bench", required=True)
    p.add_argument("--task-dir", required=True, type=Path)
    p.add_argument("--tag", required=True)
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args()

    res = build(args.bench, args.task_dir, args.tag, no_cache=args.no_cache)
    if not res.ok:
        print(f"[build_task_image] FAILED for {args.tag}\n--- log tail ---\n{res.log_tail}", file=sys.stderr)
        sys.exit(1)
    print(f"[build_task_image] OK: {args.tag}  (env_image={res.env_image})")


if __name__ == "__main__":
    main()
