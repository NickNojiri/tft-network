"""Raw match JSON -> one row per player per match (players.parquet)."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path

import pandas as pd

from .config import Settings
from .static import norm, unit_cost

log = logging.getLogger(__name__)

PATCH_RE = re.compile(r"(\d+)\.(\d+)")


def patch_of(game_version: str) -> str:
    """Extract 'major.minor' from Riot's game_version string. Mapping to TFT 18.x is UNVERIFIED:
    record what you see in docs/findings.md and adjust here if needed."""
    m = PATCH_RE.search(game_version or "")
    return f"{m.group(1)}.{m.group(2)}" if m else "unknown"


def parse_match(data: dict) -> list[dict]:
    info = data["info"]
    mid = data["metadata"]["match_id"]
    rows = []
    for p in info["participants"]:
        units = [
            {
                "id": norm(u.get("character_id")),
                "raw_id": u.get("character_id"),
                "star": int(u.get("tier", 1)),
                "cost": unit_cost(u.get("character_id", ""), u.get("rarity")),
                "items": [norm(x) for x in (u.get("itemNames") or [])],
            }
            for u in p.get("units", [])
        ]
        tr = [
            {"name": norm(t.get("name")), "raw_name": t.get("name"), "num_units": int(t.get("num_units", 0)),
             "style": int(t.get("style", 0)), "tier_current": int(t.get("tier_current", 0))}
            for t in p.get("traits", [])
        ]
        rows.append({
            "match_id": mid,
            "game_datetime": info.get("game_datetime"),
            "game_version": info.get("game_version", ""),
            "patch": patch_of(info.get("game_version", "")),
            "queue_id": info.get("queue_id", info.get("queueId")),
            "tft_set_number": info.get("tft_set_number"),
            "puuid": p.get("puuid"),
            "placement": int(p["placement"]),
            "win": bool(p.get("win", p["placement"] <= 4)),
            "level": p.get("level"),
            "last_round": p.get("last_round"),
            "gold_left": p.get("gold_left"),
            "players_eliminated": p.get("players_eliminated"),
            "damage_to_players": p.get("total_damage_to_players"),
            "has_augments_field": "augments" in p,
            "augments": json.dumps([norm(a) for a in p.get("augments", [])]),
            "units": json.dumps(units),
            "traits": json.dumps(tr),
        })
    return rows


def build_players(s: Settings, set_number: int | None = None, queue_ids: tuple[int, ...] | None = None) -> pd.DataFrame:
    set_number = s.tft_set if set_number is None else set_number
    queue_ids = s.ranked_queue_ids if queue_ids is None else queue_ids
    rows, skipped = [], Counter()
    for f in sorted(s.matches_dir.glob("*.json")):
        data = json.loads(f.read_text())
        info = data.get("info", {})
        if info.get("tft_set_number") != set_number:
            skipped["wrong_set"] += 1
            continue
        q = info.get("queue_id", info.get("queueId"))
        if queue_ids and q not in queue_ids:
            skipped[f"queue_{q}"] += 1
            continue
        if len(info.get("participants", [])) != 8:
            skipped["not_8_players"] += 1
            continue
        rows.extend(parse_match(data))
    df = pd.DataFrame(rows)
    if skipped:
        log.info("Skipped matches: %s", dict(skipped))
    if df.empty:
        log.warning("No matches parsed. If every match was skipped by queue, check the real ranked queue id "
                    "(skip counts above) and set TFT_QUEUE_IDS in .env.")
        return df
    # Sanity checks
    per_match = df.groupby("match_id")["placement"].apply(lambda x: sorted(x) == list(range(1, 9)))
    bad = (~per_match).sum()
    if bad:
        log.warning("%d matches have placements that are not exactly 1..8; dropping them", bad)
        df = df[df["match_id"].isin(per_match[per_match].index)]
    out = s.processed_dir / "players.parquet"
    df.to_parquet(out, index=False)
    log.info("Wrote %d player rows from %d matches to %s (augments field present: %s)",
             len(df), df["match_id"].nunique(), out, bool(df["has_augments_field"].any()))
    return df


def load_players(s: Settings) -> pd.DataFrame:
    return pd.read_parquet(s.processed_dir / "players.parquet")


def split_train_test(df: pd.DataFrame, test_frac: float = 0.3, salt: str = "tftnet") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deterministic split by match_id hash, so every teammate gets the same split."""
    import hashlib

    def h(m: str) -> float:
        return int(hashlib.md5((salt + m).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF

    is_test = df["match_id"].map(h) < test_frac
    return df[~is_test].copy(), df[is_test].copy()
