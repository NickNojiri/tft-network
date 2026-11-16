"""Stage-round helpers, consistency checks and derived economy fields for VOD round tables."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

CAROUSEL_ROUND = 4      # x-4 is the carousel from stage 2 on (Set 18 brought the carousel back)
PVE_ROUND = 7           # x-7 is a monster round from stage 2 on. UNVERIFIED for Set 18, check one VOD.
MAX_GOLD_JUMP = 70      # bigger single-round gold increases are flagged (loot orbs can be large)


def parse_stage(stage: str) -> tuple[int, int] | None:
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", str(stage))
    return (int(m.group(1)), int(m.group(2))) if m else None


def stage_index(stage: str) -> float:
    p = parse_stage(stage)
    return p[0] * 10 + p[1] if p else np.nan


def round_type(stage: str) -> str:
    p = parse_stage(stage)
    if not p:
        return "unknown"
    s, r = p
    if s == 1:
        return "pve"
    if r == CAROUSEL_ROUND:
        return "carousel"
    if r == PVE_ROUND:
        return "pve"
    return "pvp"


def ts_seconds(ts: str | None) -> float:
    if not ts:
        return np.nan
    parts = [float(x) for x in str(ts).strip().split(":")]
    sec = 0.0
    for x in parts:
        sec = sec * 60 + x
    return sec


def validate(rounds: pd.DataFrame) -> pd.DataFrame:
    """Adds a 'flags' column. Empty flags = passed every check."""
    r = rounds.copy().reset_index(drop=True)
    r["stage_idx"] = r["stage"].map(stage_index)
    flags = [[] for _ in range(len(r))]
    for i in range(len(r)):
        if np.isnan(r.loc[i, "stage_idx"]):
            flags[i].append("bad_stage")
        if i and r.loc[i, "stage_idx"] <= r.loc[i - 1, "stage_idx"]:
            flags[i].append("stage_not_increasing")
        for col, lo, hi in [("gold", 0, 300), ("hp", 0, 150), ("level", 1, 10)]:
            v = r.loc[i, col]
            if pd.isna(v):
                flags[i].append(f"{col}_missing")
            elif not lo <= v <= hi:
                flags[i].append(f"{col}_out_of_range")
        if i:
            prev = r.loc[i - 1]
            if pd.notna(r.loc[i, "hp"]) and pd.notna(prev["hp"]) and r.loc[i, "hp"] > prev["hp"]:
                flags[i].append("hp_increased")
            if pd.notna(r.loc[i, "level"]) and pd.notna(prev["level"]) and r.loc[i, "level"] < prev["level"]:
                flags[i].append("level_decreased")
            if pd.notna(r.loc[i, "gold"]) and pd.notna(prev["gold"]) and r.loc[i, "gold"] - prev["gold"] > MAX_GOLD_JUMP:
                flags[i].append("gold_jump")
    r["flags"] = [";".join(f) for f in flags]
    return r


def derive(rounds: pd.DataFrame) -> pd.DataFrame:
    """Win/loss from HP drops, signed streak, interest, round type. Input must be one game, sorted."""
    r = rounds.copy().sort_values("stage_idx" if "stage_idx" in rounds else "stage").reset_index(drop=True)
    if "stage_idx" not in r:
        r["stage_idx"] = r["stage"].map(stage_index)
    r["round_type"] = r["stage"].map(round_type)
    r["interest"] = r["gold"].map(lambda g: min(5, int(g) // 10) if pd.notna(g) else np.nan)
    # Outcome of round i is visible as HP at round i+1 vs round i.
    hp_next = r["hp"].shift(-1)
    res = np.where(r["round_type"] != "pvp", None,
                   np.where(hp_next.isna() | r["hp"].isna(), None, np.where(hp_next < r["hp"], "L", "W")))
    r["result"] = res
    streak, cur = [], 0
    for x in r["result"]:
        if x == "W":
            cur = cur + 1 if cur > 0 else 1
        elif x == "L":
            cur = cur - 1 if cur < 0 else -1
        streak.append(cur)  # PvE / carousel rounds keep the current streak
    r["streak"] = streak
    return r


def game_features(r: pd.DataFrame) -> dict:
    """One row of per-game features for H4/H5."""
    r = r.reset_index(drop=True)

    def at(stage, col):
        v = r.loc[r["stage"] == stage, col]
        return float(v.iloc[0]) if len(v) and pd.notna(v.iloc[0]) else np.nan

    early = r[(r["stage_idx"] >= 21) & (r["stage_idx"] < 41)]
    wins = int((early["result"] == "W").sum())
    losses = int((early["result"] == "L").sum())
    if early["streak"].max() >= 4 and wins >= losses + 3:
        opener = "win_streak"
    elif early["streak"].min() <= -4 and losses >= wins + 3:
        opener = "loss_streak"
    else:
        opener = "mixed"
    g = r["gold"].to_numpy(float)
    rolldown = np.nan
    for i in range(1, len(g)):
        if np.isfinite(g[i - 1]) and np.isfinite(g[i]) and g[i - 1] >= 40 and g[i - 1] - g[i] >= 25:
            rolldown = r.loc[i, "stage_idx"]
            break
    return {
        "gold_3_2": at("3-2", "gold"), "gold_4_1": at("4-1", "gold"), "hp_4_1": at("4-1", "hp"),
        "level_4_1": at("4-1", "level"),
        "max_win_streak_s2_s3": int(max(0, early["streak"].max() if len(early) else 0)),
        "max_loss_streak_s2_s3": int(max(0, -(early["streak"].min() if len(early) else 0))),
        "opener": opener,
        "total_interest": float(r["interest"].sum(skipna=True)),
        "rounds_at_50_plus": int((r["gold"] >= 50).sum()),
        "first_rolldown_stage_idx": rolldown,
    }
