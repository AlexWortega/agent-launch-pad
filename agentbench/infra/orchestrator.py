from __future__ import annotations

import asyncio
import csv
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from .model_dispatcher import ModelDispatcher, ModelKey


@dataclass
class Cell:
    agent: str
    bench: str
    model: str
    task_id: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CellResult:
    cell: Cell
    ok: bool
    error: str | None
    latency_s: float
    artifacts_dir: str


class Orchestrator:
    def __init__(self, dispatcher: ModelDispatcher, max_parallel: int = 16, runs_root: Path | str = "runs"):
        self.dispatcher = dispatcher
        self.sem = asyncio.Semaphore(max_parallel)
        self.runs_root = Path(runs_root)
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.run_ts = time.strftime("%Y-%m-%d__%H-%M-%S", time.gmtime())
        self.run_dir = self.runs_root / self.run_ts
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def cell_dir(self, cell: Cell) -> Path:
        safe_model = cell.model.replace("/", "__").replace(":", "_")
        d = self.run_dir / cell.agent / cell.bench / safe_model / cell.task_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def run_grid(
        self,
        cells: list[Cell],
        executor: Callable[[Cell, ModelKey, Path], Awaitable[CellResult]],
    ) -> list[CellResult]:
        results: list[CellResult] = []

        async def _wrap(cell: Cell) -> CellResult:
            async with self.sem:
                t0 = time.time()
                cdir = self.cell_dir(cell)
                # First attempt: requested model on whichever key is fresh.
                mk = await self.dispatcher.acquire(cell.model)
                try:
                    res = await executor(cell, mk, cdir)
                except Exception as e:
                    res = CellResult(cell=cell, ok=False, error=f"executor crashed: {e!r}", latency_s=time.time() - t0, artifacts_dir=str(cdir))
                # Retry once on quota error: mark exhausted, reacquire (may give a different key
                # for the same model_id, since fallback_on_quota lists the same models on a second key).
                if not res.ok and res.error and ModelDispatcher.is_quota_error(res.error):
                    await self.dispatcher.mark_quota_exhausted(mk)
                    mk2 = await self.dispatcher.acquire(cell.model)
                    if mk2 is not mk and mk2.cooldown_until <= time.time():
                        try:
                            res = await executor(cell, mk2, cdir)
                        except Exception as e:
                            res = CellResult(cell=cell, ok=False, error=f"executor crashed (retry): {e!r}", latency_s=time.time() - t0, artifacts_dir=str(cdir))
                        if not res.ok and res.error and ModelDispatcher.is_quota_error(res.error):
                            await self.dispatcher.mark_quota_exhausted(mk2)
                return res

        tasks = [asyncio.create_task(_wrap(c)) for c in cells]
        for fut in asyncio.as_completed(tasks):
            r = await fut
            results.append(r)
            self._append_summary(r)
        self._write_manifest(cells, results)
        return results

    def _append_summary(self, r: CellResult) -> None:
        path = self.run_dir / "summary.csv"
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["agent", "bench", "model", "task_id", "ok", "latency_s", "error", "artifacts_dir"])
            w.writerow([r.cell.agent, r.cell.bench, r.cell.model, r.cell.task_id, r.ok, f"{r.latency_s:.2f}", r.error or "", r.artifacts_dir])

    def _write_manifest(self, cells: list[Cell], results: list[CellResult]) -> None:
        path = self.run_dir / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "ts": self.run_ts,
                    "n_cells": len(cells),
                    "n_ok": sum(1 for r in results if r.ok),
                    "results": [
                        {
                            "agent": r.cell.agent,
                            "bench": r.cell.bench,
                            "model": r.cell.model,
                            "task_id": r.cell.task_id,
                            "ok": r.ok,
                            "error": r.error,
                            "latency_s": r.latency_s,
                            "artifacts_dir": r.artifacts_dir,
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
