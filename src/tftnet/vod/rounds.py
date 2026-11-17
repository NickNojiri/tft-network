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


# ---------------------------------------------------------------- gold accounting (event-sampling pipeline)
# Shop prices are long-standing TFT rules. The XP table and selling rule are UNVERIFIED for Set 18: the
# pipeline prefers the 'xp/max' numbers read off the HUD and only falls back to XP_TO_NEXT.
XP_BUY_GOLD = 4
XP_PER_BUY = 4
REROLL_GOLD = 2
GOLD_TOLERANCE = 2
XP_TO_NEXT = {1: 2, 2: 2, 3: 6, 4: 10, 5: 20, 6: 36, 7: 48, 8: 76, 9: 84}  # UNVERIFIED for Set 18


def sell_value(cost: int, stars: int | None) -> int:
    """Gold back for selling a unit. UNVERIFIED for Set 18: 2+ cost units lose 1 gold once starred up."""
    stars = int(stars or 1)
    v = int(cost) * 3 ** (stars - 1)
    return v - 1 if stars >= 2 and cost >= 2 else v


def xp_gained(level0, xp0, level1, xp1, xp_max0=None) -> float:
    """XP gained between two HUD readings. NaN if a reading is missing."""
    vals = [level0, xp0, level1, xp1]
    if any(v is None or pd.isna(v) for v in vals):
        return np.nan
    level0, xp0, level1, xp1 = (int(v) for v in vals)
    if level1 == level0:
        return float(xp1 - xp0)
    if level1 < level0:
        return np.nan
    first = int(xp_max0) if xp_max0 is not None and pd.notna(xp_max0) else XP_TO_NEXT.get(level0, np.nan)
    gain = first - xp0 + xp1
    for lv in range(level0 + 1, level1):
        gain += XP_TO_NEXT.get(lv, np.nan)
    return float(gain)


def gold_accounting(gold_start, gold_end, xp_buys=None, unit_cost=None, wisp_cost=0, sold_value=0,
                    rerolls_read=None) -> dict:
    """Split one planning phase's spending into XP buys, unit buys, the Wisp and rerolls.

    rerolls = (gold_start - gold_end + sold - 4*xp_buys - units - wisp) / 2. A negative or non-integer
    result means a reading is wrong (or something the formula ignores, like an augment paying gold).
    When Gemini also counted rerolls, a gold gap over GOLD_TOLERANCE between the two is flagged.
    """
    out = {"spend": np.nan, "rerolls": np.nan, "acct_flags": ""}
    if gold_start is None or gold_end is None or pd.isna(gold_start) or pd.isna(gold_end):
        out["acct_flags"] = "acct_no_gold"
        return out
    spend = float(gold_start) - float(gold_end) + float(sold_value or 0)
    out["spend"] = spend
    parts = [xp_buys, unit_cost, wisp_cost]
    if any(p is None or pd.isna(p) for p in parts):
        out["acct_flags"] = "acct_partial"
        return out
    rest = spend - XP_BUY_GOLD * float(xp_buys) - float(unit_cost) - float(wisp_cost)
    flags = []
    rerolls = rest / REROLL_GOLD
    if rerolls < -GOLD_TOLERANCE / REROLL_GOLD:
        flags.append("acct_negative")
    elif abs(rest) % REROLL_GOLD > 1e-9:
        flags.append("acct_odd")
    if rerolls_read is not None and pd.notna(rerolls_read) and abs(rest - REROLL_GOLD * float(rerolls_read)) > GOLD_TOLERANCE:
        flags.append("gold_mismatch")
    out["rerolls"] = rerolls
    out["acct_flags"] = ";".join(flags)
    return out


def consensus(values) -> tuple[float, str]:
    """Majority of the frame readings. 'exact' when at least two frames agree, 'inferred' when only one frame
    was readable or frames disagree (median used), 'missing' when none was. Missing stays NaN, never 0."""
    v = [int(x) for x in values if x is not None and pd.notna(x)]
    if not v:
        return np.nan, "missing"
    counts = pd.Series(v).value_counts()
    if counts.iloc[0] >= 2 and (len(counts) == 1 or counts.iloc[0] > counts.iloc[1]):
        return float(counts.index[0]), "exact"
    return float(np.median(v)), "inferred"


def has_shop(stage: str) -> bool:
    return round_type(stage) != "carousel"


def wisp_schedule(stages: list[str], status: list[str], rerolls: list | None = None) -> list[bool | None]:
    """Whether the first shop of each round should hold a Wisp.

    Wisps sit in the rightmost slot of every other shop (docs/research.md), and a reroll is a new shop, so the
    pattern flips each round unless the player rerolled an odd number of times. Each round where the slot was
    actually seen ('offered' or 'absent') re-anchors the pattern; two offered rounds in a row with an even
    shop count means Wisps are in every shop (Blossom 5). Rounds before the first sighting are filled backward.
    Returns None for rounds without a shop and when nothing was ever seen.
    """
    n = len(stages)
    rerolls = list(rerolls) if rerolls is not None else [None] * n
    shop_idx = [i for i in range(n) if has_shop(stages[i])]
    seen = [i for i in shop_idx if status[i] in ("offered", "absent")]
    out: list[bool | None] = [None] * n
    if not seen:
        return out

    def flips(i) -> bool:  # does the pattern flip between round i's first shop and the next round's?
        r = rerolls[i]
        return r is None or pd.isna(r) or int(round(r)) % 2 == 0

    every_shop = False
    exp = None
    prev = None
    for i in shop_idx:
        if exp is not None and prev is not None and not every_shop:
            exp = (not exp) if flips(prev) else exp
        if status[i] in ("offered", "absent"):
            obs = status[i] == "offered"
            known_even = prev is not None and rerolls[prev] is not None and pd.notna(rerolls[prev]) and flips(prev)
            if obs and known_even and status[prev] == "offered":
                every_shop = True
            if not obs:
                every_shop = False
            exp = obs
        if every_shop:
            exp = True
        out[i] = exp
        prev = i
    first = shop_idx.index(seen[0])
    for k in range(first - 1, -1, -1):
        i, nxt = shop_idx[k], shop_idx[k + 1]
        out[i] = (not out[nxt]) if flips(i) else out[nxt]
    return out


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
