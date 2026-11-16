"""H4 (streaks: rich-get-richer or balanced?) and H5 (economy) on VOD per-game features.

Small n (25-40 games): rank-based statistics and bootstrap CIs over games. Frame as a case study.
Plus a Track A check: placement distribution of Coven boards (the 'lose to win' trait).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..stats import spearman


def _boot_spearman(x, y, n_boot=1000, seed=0) -> dict:
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, len(x), len(x))
        if len(set(x[i])) > 1 and len(set(y[i])) > 1:
            vals.append(sps.spearmanr(x[i], y[i]).statistic)
    lo, hi = np.percentile(vals, [2.5, 97.5]) if vals else (np.nan, np.nan)
    return {"rho": spearman(x, y), "ci_low": float(lo), "ci_high": float(hi), "n": int(len(x))}


def analyze_games(games: pd.DataFrame) -> dict:
    g = games.dropna(subset=["final_placement"])
    if len(g) < 5:
        return {"n_games": len(g), "note": "too few games with a known placement"}
    out = {"n_games": int(len(g))}
    # Negative rho = more of X goes with a better (lower) placement.
    for col in ["gold_3_2", "gold_4_1", "hp_4_1", "level_4_1", "max_win_streak_s2_s3", "max_loss_streak_s2_s3",
                "total_interest", "rounds_at_50_plus", "first_rolldown_stage_idx"]:
        if col in g:
            out[f"rho_placement_vs_{col}"] = _boot_spearman(g[col], g["final_placement"])
    groups = {k: v["final_placement"].to_numpy(float) for k, v in g.groupby("opener")}
    out["placement_by_opener"] = {k: {"n": len(v), "mean": float(v.mean()), "top4_rate": float((v <= 4).mean())}
                                  for k, v in groups.items()}
    valid = [v for v in groups.values() if len(v) >= 2]
    if len(valid) >= 2:
        out["kruskal_p_opener"] = float(sps.kruskal(*valid).pvalue)
    # Balancing test: loss-streakers trade HP for gold. Do they have more gold and less HP at 4-1?
    ls, ws = g[g["opener"] == "loss_streak"], g[g["opener"] == "win_streak"]
    if len(ls) >= 2 and len(ws) >= 2:
        out["loss_vs_win_streak_at_4_1"] = {
            "gold_diff": float(ls["gold_4_1"].mean() - ws["gold_4_1"].mean()),
            "hp_diff": float(ls["hp_4_1"].mean() - ws["hp_4_1"].mean())}
    return out


def gold_curves(rounds: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Mean gold and HP per stage-round, by opener type (for the figure and sim calibration)."""
    r = rounds.merge(games[["game_id", "opener"]], on="game_id")
    return r.groupby(["opener", "stage_idx", "stage"], as_index=False)[["gold", "hp"]].mean()


def coven_track_a(players: pd.DataFrame) -> dict:
    """Placement distribution for boards with Coven active vs everything else (API data)."""
    def coven_active(t):
        return any(x["name"] == "coven" and x["style"] >= 1 for x in json.loads(t))
    is_cov = players["traits"].map(coven_active)
    def dist(s):
        return s.value_counts(normalize=True).reindex(range(1, 9), fill_value=0).round(4).to_dict()
    cov, rest = players.loc[is_cov, "placement"], players.loc[~is_cov, "placement"]
    return {"n_coven": int(is_cov.sum()), "coven": dist(cov) if len(cov) else {}, "others": dist(rest),
            "coven_first_or_eighth": float(cov.isin([1, 8]).mean()) if len(cov) else np.nan,
            "others_first_or_eighth": float(rest.isin([1, 8]).mean())}
