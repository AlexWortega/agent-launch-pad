"""Parse verifier outputs (reward.txt + ctrf.json) into a GradeResult.

terminal-bench convention:
- /work/reward.txt        : "1\n" if pass, "0\n" if fail
- /work/ctrf.json         : pytest-json-ctrf report (per-test pass/fail)
- /work/verifier.log      : stdout/stderr of bash /tests/test.sh
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class VerifierGrade:
    pass_: bool | None
    score: float | None
    reason: str
    raw_reward: str | None = None
    n_tests: int | None = None
    n_passed: int | None = None
    n_failed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _read(p: Path, max_chars: int = 4000) -> str | None:
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except Exception:
        return None


def parse(work_dir: Path) -> VerifierGrade:
    work_dir = Path(work_dir)
    reward_txt = _read(work_dir / "reward.txt")
    ctrf_raw = _read(work_dir / "ctrf.json", max_chars=64_000)
    log = _read(work_dir / "verifier.log")

    raw_reward = reward_txt.strip() if reward_txt else None

    # 1) Trust reward.txt first — terminal-bench's authoritative signal
    if raw_reward in ("1", "1.0"):
        return VerifierGrade(pass_=True, score=1.0, reason="reward.txt=1", raw_reward=raw_reward)
    if raw_reward in ("0", "0.0"):
        # Even on fail, try to surface partial pytest counts from CTRF
        n_pass, n_fail = _ctrf_counts(ctrf_raw)
        return VerifierGrade(
            pass_=False, score=0.0,
            reason=f"reward.txt=0; pytest passed={n_pass} failed={n_fail}",
            raw_reward=raw_reward,
            n_tests=(n_pass or 0) + (n_fail or 0),
            n_passed=n_pass, n_failed=n_fail,
        )
    # Fractional reward (other benches may emit this)
    if raw_reward:
        try:
            f = float(raw_reward)
            return VerifierGrade(
                pass_=(f >= 0.5), score=f, reason=f"reward.txt={raw_reward}",
                raw_reward=raw_reward,
            )
        except ValueError:
            pass

    # 2) Fall back to CTRF if reward.txt missing
    if ctrf_raw:
        n_pass, n_fail = _ctrf_counts(ctrf_raw)
        if n_pass is not None and n_fail is not None:
            total = n_pass + n_fail
            score = n_pass / total if total else None
            return VerifierGrade(
                pass_=(n_fail == 0 and n_pass > 0),
                score=score,
                reason=f"ctrf: passed={n_pass} failed={n_fail}",
                n_tests=total, n_passed=n_pass, n_failed=n_fail,
            )

    # 3) Verifier ran but produced nothing parseable
    if log:
        return VerifierGrade(
            pass_=None, score=None,
            reason=f"verifier ran, no reward.txt/ctrf.json; log tail: {log[-200:]!r}",
        )

    # 4) Verifier didn't run at all
    return VerifierGrade(pass_=None, score=None, reason="no verifier artifacts found")


def _ctrf_counts(ctrf_raw: str | None) -> tuple[int | None, int | None]:
    if not ctrf_raw:
        return None, None
    try:
        d = json.loads(ctrf_raw)
    except json.JSONDecodeError:
        return None, None
    summary = (d.get("results") or {}).get("summary") or {}
    return summary.get("passed"), summary.get("failed")
