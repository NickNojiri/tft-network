"""Statistics helpers. Resampling is always by MATCH, because the 8 rows in a lobby are dependent."""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd
from scipy import stats as sps


def bootstrap_by_match(df: pd.DataFrame, fn: Callable[[pd.DataFrame], float], n_boot: int = 500,
                       seed: int = 0, match_col: str = "match_id") -> dict:
    rng = np.random.default_rng(seed)
    df = df.reset_index(drop=True)
    positions = list(df.groupby(match_col).indices.values())
    point = fn(df)
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(positions), size=len(positions))
        # Rename match ids per draw so a match drawn twice counts as two lobbies.
        idx = np.concatenate([positions[i] for i in pick])
        lobby = np.repeat(np.arange(len(pick)), [len(positions[i]) for i in pick])
        sample = df.iloc[idx].reset_index(drop=True)
        sample[match_col] = lobby
        try:
            vals.append(fn(sample))
        except Exception:  # degenerate resample
            continue
    vals = np.array([v for v in vals if np.isfinite(v)])
    lo, hi = (np.percentile(vals, [2.5, 97.5]) if len(vals) else (np.nan, np.nan))
    return {"estimate": float(point), "ci_low": float(lo), "ci_high": float(hi), "n_boot": int(len(vals))}


def mean_ci_by_match(df: pd.DataFrame, col: str, n_boot: int = 300, seed: int = 0) -> dict:
    return bootstrap_by_match(df, lambda d: d[col].mean(), n_boot=n_boot, seed=seed)


def spearman(x, y) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return float("nan")
    return float(sps.spearmanr(x[ok], y[ok]).statistic)


def ols(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def fe_slope(df: pd.DataFrame, y: str, x: str, fe: list[str]) -> float:
    """Slope of y on x with fixed effects (dummies) for each column in fe."""
    d = df[[y, x] + fe].dropna()
    X = [np.ones(len(d)), d[x].to_numpy(float)]
    for col in fe:
        dummies = pd.get_dummies(d[col], drop_first=True, dtype=float)
        if dummies.shape[1]:
            X.append(dummies.to_numpy())
    Xm = np.column_stack([a if a.ndim == 2 else a[:, None] for a in X])
    return float(ols(d[y].to_numpy(float), Xm)[1])
