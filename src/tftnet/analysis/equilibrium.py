"""H3: Do popular comps converge toward average placement (4.5), like a mixed-strategy equilibrium?

Static view: pick rate vs avg placement per comp per patch, with bootstrap CIs (popular comps have
tight CIs just from sample size, so compare CI-aware distances from 4.5, not raw scatter).

Dynamic view (the real test): for comps present in consecutive patches, does over-performance in
patch N predict a pick-rate increase in patch N+1 (players best-responding)?
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..stats import bootstrap_by_match, spearman


def comp_patch_table(df: pd.DataFrame, min_games: int = 200, n_boot: int = 200) -> pd.DataFrame:
    d = df[df["comp"] != "other"]
    rows = []
    for patch, gp in d.groupby("patch"):
        total = len(gp)
        for comp, gc in gp.groupby("comp"):
            if len(gc) < min_games:
                continue
            ci = bootstrap_by_match(gc, lambda x: x["placement"].mean(), n_boot=n_boot)
            rows.append({"patch": patch, "comp": comp, "n": len(gc), "pick_rate": len(gc) / total,
                         "avg_placement": ci["estimate"], "ci_low": ci["ci_low"], "ci_high": ci["ci_high"],
                         "dist_from_4_5": abs(ci["estimate"] - 4.5)})
    return pd.DataFrame(rows)


def _patch_key(p: str):
    try:
        return tuple(int(x) for x in str(p).split("."))
    except ValueError:
        return (999, 999)


def dynamics(table: pd.DataFrame) -> dict:
    if table.empty or table["patch"].nunique() < 2:
        return {"n_pairs": 0, "note": "need at least 2 patches with enough games"}
    patches = sorted(table["patch"].unique(), key=_patch_key)
    pairs = []
    for a, b in zip(patches, patches[1:]):
        ta = table[table["patch"] == a].set_index("comp")
        tb = table[table["patch"] == b].set_index("comp")
        for comp in ta.index.intersection(tb.index):
            pairs.append({"from": a, "to": b, "comp": comp,
                          "overperf_prev": 4.5 - ta.loc[comp, "avg_placement"],
                          "d_pick_rate": tb.loc[comp, "pick_rate"] - ta.loc[comp, "pick_rate"],
                          "d_dist_from_4_5": tb.loc[comp, "dist_from_4_5"] - ta.loc[comp, "dist_from_4_5"]})
    p = pd.DataFrame(pairs)
    if p.empty:
        return {"n_pairs": 0}
    return {"n_pairs": len(p), "pairs": p,
            "spearman_overperf_vs_pickrate_change": spearman(p["overperf_prev"], p["d_pick_rate"]),
            "share_moving_toward_4_5": float((p["d_dist_from_4_5"] < 0).mean())}


def analyze(df: pd.DataFrame, min_games: int = 200) -> dict:
    t = comp_patch_table(df, min_games=min_games)
    static = {"spearman_pickrate_vs_dist": spearman(t["pick_rate"], t["dist_from_4_5"]) if len(t) else np.nan}
    return {"table": t, "static": static, "dynamics": dynamics(t)}
