"""Gemini free-tier budget: a daily request counter, a requests/tokens-per-minute limiter and a token log.

Free tier, checked 2026-11-16: 5 requests/minute, 250K input tokens/minute, 20 requests/day, reset at
midnight Pacific. The counter lives in the VOD cache dir, so it survives restarts: when the day's limit is
reached the pipeline stops, and rerunning the same command the next day resumes from the cached passes.
Every attempt counts, including failed ones (a 503 may or may not count against quota; assume it does).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo

    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # no tz database: fixed PST is at most an hour off during daylight time
    PACIFIC = timezone(timedelta(hours=-8))


class DailyLimitReached(RuntimeError):
    pass


def pacific_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, PACIFIC).strftime("%Y-%m-%d")


def seconds_until_reset(ts: float) -> float:
    now = datetime.fromtimestamp(ts, PACIFIC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0.0, (midnight - now).total_seconds())


class Budget:
    def __init__(self, path: Path, daily_limit: int = 20, rpm: int = 5, tpm: int = 250_000,
                 now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.path = Path(path)
        self.daily_limit, self.rpm, self.tpm = daily_limit, rpm, tpm
        self.now, self.sleep = now, sleep

    # ------------------------------------------------------------------ state
    def _load(self) -> dict:
        st = {"day": pacific_day(self.now()), "requests": 0, "recent": []}
        if self.path.exists():
            try:
                st.update(json.loads(self.path.read_text()))
            except ValueError:
                log.warning("Budget file %s unreadable; starting a new count", self.path)
        if st["day"] != pacific_day(self.now()):
            st["day"], st["requests"] = pacific_day(self.now()), 0
        return st

    def _save(self, st: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(st))

    def used_today(self) -> int:
        return self._load()["requests"]

    def remaining(self) -> int:
        return max(0, self.daily_limit - self.used_today())

    # ------------------------------------------------------------------ gate
    def acquire(self, est_tokens: int) -> None:
        """Blocks until one more request fits the per-minute limits, then counts it.
        Raises DailyLimitReached instead of sending request number daily_limit + 1."""
        st = self._load()
        if st["requests"] >= self.daily_limit:
            h = seconds_until_reset(self.now()) / 3600
            raise DailyLimitReached(
                f"Daily Gemini request limit reached ({st['requests']}/{self.daily_limit}). Resets at midnight "
                f"Pacific, in {h:.1f} h. Rerun the same command then: finished passes are cached.")
        if est_tokens > self.tpm:
            log.warning("Request estimated at %d tokens, over the %d tokens/minute limit", est_tokens, self.tpm)
        while True:
            t = self.now()
            st["recent"] = [r for r in st["recent"] if t - r[0] < 60]
            used = sum(r[1] for r in st["recent"])
            if st["recent"] and (len(st["recent"]) >= self.rpm or used + est_tokens > self.tpm):
                wait = 60 - (t - min(r[0] for r in st["recent"])) + 0.5
                log.info("Rate limit: waiting %.0f s (%d requests, %d tokens in the last minute)",
                         wait, len(st["recent"]), used)
                self.sleep(max(wait, 0.5))
                continue
            break
        st["requests"] += 1
        st["recent"].append([self.now(), int(est_tokens)])
        self._save(st)

    def settle(self, actual_tokens: int | None) -> None:
        """Replace the last request's estimate with the real input token count."""
        if not actual_tokens:
            return
        st = self._load()
        if st["recent"]:
            st["recent"][-1][1] = int(actual_tokens)
            self._save(st)


class UsageLog:
    """One JSON line per Gemini request: what was sent (frames, resolution) and what it cost (usage_metadata)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def write(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
