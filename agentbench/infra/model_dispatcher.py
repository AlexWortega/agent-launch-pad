from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ModelKey:
    model: str
    key_alias: str
    api_key: str
    cooldown_until: float = 0.0


@dataclass
class ModelDispatcher:
    primary: list[ModelKey] = field(default_factory=list)
    fallback: list[ModelKey] = field(default_factory=list)
    base_url: str = "https://openrouter.ai/api/v1"
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _rr_counter: dict[str, int] = field(default_factory=dict)   # round-robin per-model

    @classmethod
    def from_config(cls, path: str | Path) -> "ModelDispatcher":
        cfg = yaml.safe_load(Path(path).read_text())
        keys = cfg["keys"]
        chain = [ModelKey(model=c["model"], key_alias=c["key"], api_key=keys[c["key"]]) for c in cfg["chain"]]
        fallback = [ModelKey(model=c["model"], key_alias=c["key"], api_key=keys[c["key"]]) for c in cfg["fallback_on_quota"]]
        return cls(primary=chain, fallback=fallback, base_url=cfg["openrouter"]["base_url"])

    async def acquire(self, requested_model: str | None = None) -> ModelKey:
        """Pick a key for `requested_model`. Among eligible (non-cooldown) keys for that
        model, round-robin across them so no single key gets hammered first.
        Falls back to fallback pool's same-model entries, then any model, then warmest cooldown.
        """
        async with self._lock:
            now = time.time()
            pool = self.primary + self.fallback
            if requested_model:
                eligible = [mk for mk in pool if mk.model == requested_model and mk.cooldown_until <= now]
                if eligible:
                    idx = self._rr_counter.get(requested_model, 0)
                    pick = eligible[idx % len(eligible)]
                    self._rr_counter[requested_model] = idx + 1
                    return pick
            # No eligible same-model key; fall through to any non-cooldown
            for mk in pool:
                if mk.cooldown_until <= now:
                    return mk
            soonest = min(pool, key=lambda x: x.cooldown_until)
            return soonest

    async def mark_quota_exhausted(self, mk: ModelKey, cooldown_s: float = 3600.0) -> None:
        async with self._lock:
            mk.cooldown_until = time.time() + cooldown_s

    @staticmethod
    def is_quota_error(err_text: str, status: int | None = None) -> bool:
        if status in (401, 402, 429):
            return True
        if not err_text:
            return False
        s = err_text.lower()
        triggers = ["rate limit", "quota", "insufficient credits", "user not found", "limit_remaining\":0", "429", "402"]
        return any(t in s for t in triggers)
