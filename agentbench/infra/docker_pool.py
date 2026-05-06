from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BuildResult:
    image: str
    ok: bool
    log: str


def build_image(context: Path, image: str, dockerfile: str = "Dockerfile") -> BuildResult:
    cmd = ["docker", "build", "-t", image, "-f", str(context / dockerfile), str(context)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log = (proc.stdout or "") + (proc.stderr or "")
    return BuildResult(image=image, ok=proc.returncode == 0, log=log)


def image_exists(image: str) -> bool:
    proc = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True)
    return proc.returncode == 0


async def run_container(
    image: str,
    *,
    name: str,
    env: dict[str, str] | None = None,
    binds: dict[str, str] | None = None,
    network: str | None = None,
    runtime: str | None = None,
    extra_args: list[str] | None = None,
    cmd: list[str] | None = None,
    timeout_s: float = 600.0,
) -> tuple[int, str, str]:
    args = ["docker", "run", "--rm", "--name", name]
    if runtime:
        args += ["--runtime", runtime]
        # Kata's virtio-console needs a TTY allocated, otherwise some processes
        # (e.g. pi-coding-agent) block on stdin. Harmless under runc too,
        # but only added when a non-default runtime is requested.
        args += ["-t"]
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    for host, ctr in (binds or {}).items():
        args += ["-v", f"{host}:{ctr}"]
    if network:
        args += ["--network", network]
    if extra_args:
        args += extra_args
    args.append(image)
    if cmd:
        args += cmd

    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        await asyncio.create_subprocess_exec("docker", "rm", "-f", name)
        return 124, "", f"timeout after {timeout_s}s"
    return proc.returncode, stdout_b.decode("utf-8", "replace"), stderr_b.decode("utf-8", "replace")


def force_remove(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)


def prune_recent(older_than: str = "10m") -> None:
    subprocess.run(["docker", "system", "prune", "-f", "--filter", f"until={older_than}"], capture_output=True, text=True)
