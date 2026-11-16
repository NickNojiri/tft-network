"""H1: Do comps ranked by PageRank on a who-beat-whom graph predict placement better than pick rate?

Graph: nodes = comps. In each lobby, for every pair of players on different comps, add an edge
from the lower-placed comp to the higher-placed comp, weighted by placement gap. PageRank mass
flows toward comps that beat other (strong) comps.

Evaluated on held-out matches against three baselines: pick rate, train average placement,
and Bradley-Terry strength fit on the same pairwise outcomes.
"""
from __future__ import annotations

from itertools import combinations

import networkx as nx
import numpy as np
import pandas as pd

from ..stats import bootstrap_by_match, spearman


def pairwise_outcomes(df: pd.DataFrame, weighted: bool = True, top4_only: bool = False) -> pd.DataFrame:
    """(loser_comp, winner_comp, weight) for every cross-comp pair within each lobby."""
    d = df[df["comp"] != "other"]
    rows = []
    for _, g in d.groupby("match_id"):
        recs = g[["comp", "placement"]].to_numpy()
        for (c1, p1), (c2, p2) in combinations(recs, 2):
            if c1 == c2:
                continue
            (lc, lp), (wc, wp) = ((c1, p1), (c2, p2)) if p1 > p2 else ((c2, p2), (c1, p1))
            if top4_only and wp > 4:
                continue
            rows.append((lc, wc, float(lp - wp) if weighted else 1.0))
    return pd.DataFrame(rows, columns=["loser", "winner", "weight"])


def build_graph(pairs: pd.DataFrame) -> nx.DiGraph:
    agg = pairs.groupby(["loser", "winner"], as_index=False)["weight"].sum()
    G = nx.DiGraph()
    for r in agg.itertuples(index=False):
        G.add_edge(r.loser, r.winner, weight=r.weight)
    return G


def pagerank_scores(df: pd.DataFrame, alpha: float = 0.85, weighted: bool = True, top4_only: bool = False) -> pd.Series:
    G = build_graph(pairwise_outcomes(df, weighted, top4_only))
    return pd.Series(nx.pagerank(G, alpha=alpha, weight="weight"), name="pagerank")


def bradley_terry(pairs: pd.DataFrame, iters: int = 200) -> pd.Series:
    """Minorization-maximization fit (Hunter 2004). Higher = stronger."""
    comps = sorted(set(pairs["loser"]) | set(pairs["winner"]))
    idx = {c: i for i, c in enumerate(comps)}
    n = len(comps)
    W = np.zeros((n, n))  # W[i, j] = times i beat j
    for r in pairs.itertuples(index=False):
        W[idx[r.winner], idx[r.loser]] += 1
    wins = W.sum(1)
    games = W + W.T
    p = np.ones(n)
    for _ in range(iters):
        denom = (games / (p[:, None] + p[None, :] + 1e-12)).sum(1)
        p = np.where(denom > 0, (wins + 0.5) / denom, p)  # +0.5 keeps winless comps finite
        p /= p.sum()
    return pd.Series(p, index=comps, name="bradley_terry")


def evaluate(train: pd.DataFrame, test: pd.DataFrame, n_boot: int = 300) -> dict:
    """Spearman correlation between each ranking (fit on train) and test performance (-avg placement)."""
    tr = train[train["comp"] != "other"]
    rankings = pd.DataFrame({
        "pagerank": pagerank_scores(tr),
        "pagerank_unweighted": pagerank_scores(tr, weighted=False),
        "pagerank_top4": pagerank_scores(tr, top4_only=True),
        "bradley_terry": bradley_terry(pairwise_outcomes(tr, weighted=False)),
        "pick_rate": tr["comp"].value_counts(normalize=True),
        "train_avg_placement_neg": -tr.groupby("comp")["placement"].mean(),
    })

    te = test[test["comp"].isin(rankings.index)]

    def score(col: str):
        def fn(d: pd.DataFrame) -> float:
            perf = -d.groupby("comp")["placement"].mean()
            j = rankings[[col]].join(perf.rename("perf"), how="inner")
            return spearman(j[col], j["perf"])
        return fn

    results = {col: bootstrap_by_match(te, score(col), n_boot=n_boot) for col in rankings.columns}
    table = rankings.join(te.groupby("comp")["placement"].agg(test_avg_placement="mean", test_n="size"))
    return {"spearman_vs_test": results, "rankings": table.sort_values("pagerank", ascending=False)}
