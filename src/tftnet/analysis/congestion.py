"""H2: The more players in a lobby on the same comp (shared champion pool), the worse its placement.

Two measures:
- k = number of players in the lobby on the same comp label (1, 2, 3+).
- contention = cost-weighted overlap of a player's units with everyone else's units in the lobby,
  using the Set 18 pool sizes. Does not depend on comp labels, so it is a robustness check.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..stats import bootstrap_by_match, fe_slope

POOL_SIZE = {1: 30, 2: 25, 3: 18, 4: 10, 5: 9}


def add_contention(df: pd.DataFrame) -> pd.DataFrame:
    """Sum over a player's units of (copies held by OTHER players in lobby / pool size for that cost).
    Copies per unit = 3 ** (star - 1)."""
    df = df.copy()
    vals = np.full(len(df), np.nan)
    pos = {i: n for n, i in enumerate(df.index)}
    for _, g in df.groupby("match_id"):
        boards = {i: json.loads(u) for i, u in zip(g.index, g["units"])}
        copies = {i: {} for i in boards}
        for i, units in boards.items():
            for u in units:
                copies[i][u["id"]] = copies[i].get(u["id"], 0) + 3 ** (u["star"] - 1)
        for i, units in boards.items():
            total = 0.0
            for u in {u["id"]: u for u in units}.values():
                pool = POOL_SIZE.get(u.get("cost") or 0)
                if not pool:
                    continue
                others = sum(copies[j].get(u["id"], 0) for j in boards if j != i)
                total += others / pool
            vals[pos[i]] = total
    df["contention"] = vals
    return df


def analyze(df: pd.DataFrame, n_boot: int = 300, n_perm: int = 200, seed: int = 0) -> dict:
    d = df[df["comp"] != "other"].copy()
    d["k"] = d["n_same_comp_in_lobby"].clip(upper=3)

    slope = bootstrap_by_match(d, lambda x: fe_slope(x, "placement", "k", ["comp", "patch"]), n_boot=n_boot, seed=seed)

    # Permutation null: shuffle comp labels across players within each patch, recompute k and slope.
    rng = np.random.default_rng(seed)
    null = []
    for _ in range(n_perm):
        p = d.copy()
        p["comp"] = p.groupby("patch")["comp"].transform(lambda s: rng.permutation(s.to_numpy()))
        p["k"] = p.groupby(["match_id", "comp"])["comp"].transform("size").clip(upper=3)
        null.append(fe_slope(p, "placement", "k", ["comp", "patch"]))
    null = np.array(null)
    p_value = float((np.sum(null >= slope["estimate"]) + 1) / (len(null) + 1))

    by_k = d.groupby("k")["placement"].agg(["mean", "size"]).rename(columns={"size": "n"})
    top = d["comp"].value_counts().head(8).index
    per_comp = d[d["comp"].isin(top)].groupby(["comp", "k"])["placement"].agg(["mean", "size"]).reset_index()

    out = {"slope_per_extra_player": slope, "permutation_p_one_sided": p_value, "by_k": by_k, "per_comp": per_comp}
    if "contention" in df.columns:
        c = df.dropna(subset=["contention"])
        out["contention_slope"] = bootstrap_by_match(
            c, lambda x: fe_slope(x, "placement", "contention", ["patch"]), n_boot=n_boot, seed=seed)
    return out
