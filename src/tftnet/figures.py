"""Report figures. Each function saves one PNG and returns its path."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
import pandas as pd  # noqa: E402

plt.rcParams.update({"figure.dpi": 150, "font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
BLUE, ORANGE, GREY = "#2f6db5", "#d9822b", "#888888"


def comp_graph(df: pd.DataFrame, path: Path, top: int = 15) -> Path:
    from .analysis.pagerank import build_graph, pairwise_outcomes, pagerank_scores

    pr = pagerank_scores(df)
    keep = pr.sort_values(ascending=False).head(top).index
    pairs = pairwise_outcomes(df[df["comp"].isin(keep)])
    G = build_graph(pairs)
    pos = nx.circular_layout(G)
    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    w = [G[u][v]["weight"] for u, v in G.edges]
    mx = max(w) if w else 1
    nx.draw_networkx_edges(G, pos, ax=ax, width=[0.3 + 2.5 * x / mx for x in w], alpha=0.25, arrowsize=6, edge_color=GREY)
    nx.draw_networkx_nodes(G, pos, ax=ax, node_size=[4000 * pr.get(n, 0) / pr.max() for n in G.nodes], node_color=BLUE, alpha=0.85)
    nx.draw_networkx_labels(G, pos, ax=ax, font_size=6)
    ax.set_title(f"Who-beat-whom graph, top {top} comps (node size = PageRank)")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def ranking_comparison(h1: dict, path: Path) -> Path:
    res = h1["spearman_vs_test"]
    names = list(res)
    est = [res[n]["estimate"] for n in names]
    # Percentile CIs can exclude the point estimate for bounded stats (rho near 1): clip at 0.
    err = [[max(0.0, res[n]["estimate"] - res[n]["ci_low"]) for n in names],
           [max(0.0, res[n]["ci_high"] - res[n]["estimate"]) for n in names]]
    fig, ax = plt.subplots(figsize=(6, 3))
    colors = [BLUE if n.startswith("pagerank") else ORANGE if n == "bradley_terry" else GREY for n in names]
    ax.barh(names, est, xerr=err, color=colors, capsize=3)
    ax.axvline(0, color="black", lw=0.6)
    ax.set_xlabel("Spearman with held-out performance (95% CI)")
    ax.set_title("H1: which ranking predicts held-out placement?")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def congestion_plot(h2: dict, path: Path) -> Path:
    pc = h2["per_comp"]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    for comp, g in pc.groupby("comp"):
        ax.plot(g["k"], g["mean"], marker="o", alpha=0.5, lw=1)
    bk = h2["by_k"]
    ax.plot(bk.index, bk["mean"], marker="s", color="black", lw=2.5, label="all comps")
    s = h2["slope_per_extra_player"]
    ax.set_xticks([1, 2, 3], ["1 (alone)", "2", "3+"])
    ax.set_xlabel("Players on the same comp in the lobby")
    ax.set_ylabel("Average placement (lower is better)")
    ax.set_title(f"H2: +{s['estimate']:.2f} places per extra player (95% CI {s['ci_low']:.2f} to {s['ci_high']:.2f})")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def equilibrium_plot(h3: dict, path: Path) -> Path:
    t = h3["table"]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    if not t.empty:
        ax.errorbar(t["pick_rate"] * 100, t["avg_placement"],
                    yerr=[(t["avg_placement"] - t["ci_low"]).clip(lower=0), (t["ci_high"] - t["avg_placement"]).clip(lower=0)],
                    fmt="o", ms=3, color=BLUE, ecolor=GREY, alpha=0.8)
    ax.axhline(4.5, color=ORANGE, ls="--", lw=1, label="4.5 = average")
    ax.set_xlabel("Pick rate (% of boards)")
    ax.set_ylabel("Average placement")
    ax.invert_yaxis()
    ax.set_title("H3: pick rate vs placement, per comp per patch")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def vod_curves(curves: pd.DataFrame, path: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(7.5, 3), sharex=True)
    for opener, g in curves.groupby("opener"):
        g = g.sort_values("stage_idx")
        axes[0].plot(g["stage"], g["gold"], marker=".", label=opener)
        axes[1].plot(g["stage"], g["hp"], marker=".", label=opener)
    axes[0].set_ylabel("Gold at planning start")
    axes[1].set_ylabel("HP")
    for ax in axes:
        ax.tick_params(axis="x", rotation=90, labelsize=6)
    axes[0].legend(frameon=False, fontsize=7)
    fig.suptitle("H4/H5: gold and HP by opener type (VOD data)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def gold_vs_placement(games: pd.DataFrame, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(4.5, 3.5))
    for opener, g in games.groupby("opener"):
        ax.scatter(g["gold_4_1"], g["final_placement"], label=opener, s=18)
    ax.invert_yaxis()
    ax.set_xlabel("Gold at 4-1")
    ax.set_ylabel("Final placement")
    ax.legend(frameon=False, fontsize=7)
    ax.set_title("Gold at 4-1 vs placement")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def sim_dispersion(sim: dict, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.plot(sim["curve_on"].index, sim["curve_on"]["strength_sd"], label="streak gold ON", color=BLUE)
    ax.plot(sim["curve_off"].index, sim["curve_off"]["strength_sd"], label="streak gold OFF", color=ORANGE)
    ax.set_ylabel("SD of board strength across players")
    ax.tick_params(axis="x", rotation=90, labelsize=6)
    ax.legend(frameon=False)
    ax.set_title("Simulation: does streak gold widen or narrow the gap?")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path
