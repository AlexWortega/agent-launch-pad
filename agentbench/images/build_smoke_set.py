"""Build a smoke-set of per-task images for the shell-env grid.

For Stage B smoke we focus on terminal-bench-2 (uniform structure, real verifier).
Other shell-tool benches stay prompt-only at this stage.

Default smoke set: 3 terminal-bench tasks chosen for diversity (data, code, fs).
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .build_task_image import build, BuildResult


HARBOR_TB = Path("/home/alexw/.cache/harbor/tasks/packages/terminal-bench")

# Curated smoke set — pick tasks with simple env images and short instruction.md
DEFAULT_SMOKE_SET = [
    "cancel-async-tasks",
    "fix-git",
    "gcode-to-text",
]


def resolve_task_dir(task_name: str) -> Path | None:
    parent = HARBOR_TB / task_name
    if not parent.is_dir():
        return None
    shas = sorted([d for d in parent.iterdir() if d.is_dir()])
    return shas[-1] if shas else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tasks", nargs="*", default=None, help="terminal-bench task names (omit + use --all for full corpus)")
    p.add_argument("--all", action="store_true", help="build images for every terminal-bench task in HARBOR_TB")
    p.add_argument("--parallel", type=int, default=2, help="concurrent buildx builds")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--skip-existing", action="store_true", default=True, help="skip tasks whose image is already built")
    args = p.parse_args()

    if args.all:
        task_names = sorted([d.name for d in HARBOR_TB.iterdir() if d.is_dir()])
    elif args.tasks:
        task_names = args.tasks
    else:
        task_names = DEFAULT_SMOKE_SET

    targets: list[tuple[str, Path, str]] = []
    for tname in task_names:
        td = resolve_task_dir(tname)
        if td is None:
            print(f"[smoke] skip {tname}: not found in harbor cache", file=sys.stderr)
            continue
        slug = tname.replace("/", "-").replace(":", "_").lower()
        tag = f"agentbench/terminal-bench-2/{slug}:latest"
        if args.skip_existing:
            import subprocess
            if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0:
                print(f"[smoke] skip {tname}: image already exists ({tag})")
                continue
        targets.append((tname, td, tag))

    print(f"[smoke] building {len(targets)} task images, parallel={args.parallel}")
    failures: list[tuple[str, BuildResult]] = []
    successes: list[tuple[str, BuildResult]] = []

    with ThreadPoolExecutor(max_workers=args.parallel) as ex:
        futs = {ex.submit(build, "terminal-bench-2", td, tag, no_cache=args.no_cache): tname for tname, td, tag in targets}
        for fut in as_completed(futs):
            tname = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                print(f"[smoke] {tname}: CRASH {e!r}")
                continue
            if res.ok:
                print(f"[smoke] {tname}: OK -> {res.tag}")
                successes.append((tname, res))
            else:
                print(f"[smoke] {tname}: FAIL\n--- log tail ---\n{res.log_tail}")
                failures.append((tname, res))

    print(f"\n[smoke] result: {len(successes)} ok, {len(failures)} failed")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
