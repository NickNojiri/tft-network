"""Resumable data collection: seed players -> match ids -> match JSON.

Everything is cached on disk, so a key expiring mid-run only means: new key, rerun.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config import Settings
from .client import RiotClient, write_json

log = logging.getLogger(__name__)


def seed_players(client: RiotClient, s: Settings, tiers=("challenger", "grandmaster", "master")) -> Path:
    """Save the current Master+ puuid list. Run this BEFORE Set 19 (Dec 1) resets the ladder."""
    rows = []
    for tier in tiers:
        data = client.league(tier)
        for e in data.get("entries", []):
            if e.get("puuid"):
                rows.append({"puuid": e["puuid"], "tier": tier.upper(), "lp": e.get("leaguePoints"),
                             "wins": e.get("wins"), "losses": e.get("losses")})
        log.info("%s: %d players", tier, len(data.get("entries", [])))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = s.raw_dir / f"seed_{stamp}.json"
    write_json(path, rows)
    write_json(s.raw_dir / "seed_latest.json", rows)
    log.info("Saved %d players to %s", len(rows), path)
    return path


def collect_match_ids(client: RiotClient, s: Settings, per_player: int = 100, max_players: int | None = None) -> Path:
    seed = json.loads((s.raw_dir / "seed_latest.json").read_text())
    ids_dir = s.raw_dir / "match_ids"
    ids_dir.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(seed[: max_players or len(seed)]):
        out = ids_dir / f"{row['puuid'][:40]}.json"
        if out.exists():
            continue
        ids = client.match_ids(row["puuid"], start_time=s.set_start_epoch, count=per_player)
        write_json(out, ids)
        if i % 50 == 0:
            log.info("match ids: %d/%d players", i, len(seed))
    all_ids = sorted({m for f in ids_dir.glob("*.json") for m in json.loads(f.read_text())})
    path = s.raw_dir / "match_ids_all.txt"
    path.write_text("\n".join(all_ids))
    log.info("%d unique match ids", len(all_ids))
    return path


def collect_matches(client: RiotClient, s: Settings, limit: int | None = None) -> int:
    ids = [x for x in (s.raw_dir / "match_ids_all.txt").read_text().split() if x]
    todo = [m for m in ids if not (s.matches_dir / f"{m}.json").exists()]
    if limit:
        todo = todo[:limit]
    log.info("%d matches cached, %d to fetch", len(ids) - len(todo), len(todo))
    t0 = time.time()
    for i, mid in enumerate(todo, 1):
        data = client.match(mid)
        if data is not None:
            write_json(s.matches_dir / f"{mid}.json", data)
        if i % 100 == 0:
            rate = i / (time.time() - t0)
            log.info("%d/%d fetched (%.2f/s, ~%.0f min left)", i, len(todo), rate, (len(todo) - i) / rate / 60)
    return len(todo)
