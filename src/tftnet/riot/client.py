"""Rate-limited Riot API client with disk caching.

Dev key limits: 20 requests / 1 s and 100 requests / 120 s. We stay slightly under both.
On 429 we honor Retry-After. On 401/403 we stop: the dev key expires every 24 hours.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)


class KeyExpiredError(RuntimeError):
    pass


class RateLimiter:
    """Sliding-window limiter over several (max_calls, seconds) windows."""

    def __init__(self, windows: list[tuple[int, float]], clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.windows = windows
        self.clock = clock
        self.sleep = sleep
        self.history: deque[float] = deque()

    def wait(self) -> None:
        while True:
            now = self.clock()
            longest = max(s for _, s in self.windows)
            while self.history and now - self.history[0] > longest:
                self.history.popleft()
            delay = 0.0
            for max_calls, seconds in self.windows:
                recent = [t for t in self.history if now - t < seconds]
                if len(recent) >= max_calls:
                    delay = max(delay, seconds - (now - recent[-max_calls]) + 0.01)
            if delay <= 0:
                self.history.append(now)
                return
            self.sleep(delay)


class RiotClient:
    def __init__(self, api_key: str, platform: str = "na1", region: str = "americas",
                 limiter: RateLimiter | None = None, session: requests.Session | None = None,
                 max_retries: int = 5):
        if not api_key:
            raise KeyExpiredError("RIOT_API_KEY is empty. Put your key in .env (see .env.example).")
        self.platform_host = f"https://{platform}.api.riotgames.com"
        self.region_host = f"https://{region}.api.riotgames.com"
        self.limiter = limiter or RateLimiter([(19, 1.0), (98, 120.0)])
        self.session = session or requests.Session()
        self.session.headers["X-Riot-Token"] = api_key
        self.max_retries = max_retries
        self.calls = 0

    def _get(self, url: str, params: dict | None = None) -> Any:
        for attempt in range(self.max_retries):
            self.limiter.wait()
            self.calls += 1
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (401, 403):
                raise KeyExpiredError(f"{resp.status_code} from Riot. Dev keys expire every 24h: regenerate at "
                                      "developer.riotgames.com and update .env, then rerun (progress is cached).")
            if resp.status_code == 404:
                return None
            if resp.status_code == 400 and params and params.get("count", 0) > 20:
                log.warning("HTTP 400 with count=%s; retrying with count=20", params["count"])
                params = {**params, "count": 20}
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                log.warning("HTTP %s, sleeping %.1fs (attempt %d)", resp.status_code, wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
        raise RuntimeError(f"Gave up after {self.max_retries} attempts: {url}")

    # ---- endpoints -------------------------------------------------------
    def league(self, tier: str, queue: str = "RANKED_TFT") -> dict:
        """tier in {'challenger','grandmaster','master'}. Entries include puuid."""
        return self._get(f"{self.platform_host}/tft/league/v1/{tier}", {"queue": queue}) or {"entries": []}

    def match_ids(self, puuid: str, start_time: int | None = None, count: int = 100, start: int = 0) -> list[str]:
        params: dict[str, Any] = {"count": count, "start": start}
        if start_time:
            params["startTime"] = start_time
        return self._get(f"{self.region_host}/tft/match/v1/matches/by-puuid/{puuid}/ids", params) or []

    def match(self, match_id: str) -> dict | None:
        return self._get(f"{self.region_host}/tft/match/v1/matches/{match_id}")

    def account_by_riot_id(self, game_name: str, tag_line: str) -> dict | None:
        return self._get(f"{self.region_host}/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj))
    tmp.replace(path)
