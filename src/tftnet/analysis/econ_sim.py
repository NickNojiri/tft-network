"""Economy simulation (H4/H5 model side). Clearly a model, not a measurement.

Rules (verified, see docs/research.md): passive 2,2,3,4 then 5; interest 1 per 10 gold held (cap 5,
before passive); +1 gold per PvP win; streak gold for streaks of 3-4: +1, 5: +2, 6+: +3 (win or loss).

Players differ only in strategy (how much they spend each round). Spending buys board strength;
fights are won with probability logistic(strength difference). Loss costs HP. We compare gold and
strength dispersion across players with streak gold ON vs OFF: if streak gold increases dispersion,
streaks are rich-get-richer; if loss-streak gold narrows it, the system is balancing.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

STAGES = [(1, r) for r in (2, 3, 4)] + [(s, r) for s in range(2, 7) for r in range(1, 8)]


def passive(stage: tuple[int, int]) -> int:
    return {(1, 2): 2, (1, 3): 2, (1, 4): 3, (2, 1): 4}.get(stage, 5)


def streak_gold(streak: int) -> int:
    a = abs(streak)
    return 0 if a < 3 else 1 if a <= 4 else 2 if a == 5 else 3


@dataclass
class Strategy:
    name: str
    save_until: int      # keep at least this much gold until roll-down stage
    rolldown_stage: int  # stage at which they spend down
    spend_rate: float    # share of gold above the floor spent per round


DEFAULT_STRATS = [
    Strategy("greedy_econ", 50, 4, 0.2),
    Strategy("balanced", 30, 4, 0.5),
    Strategy("aggressive", 10, 3, 0.9),
    Strategy("loss_streak_then_roll", 50, 3, 0.1),
]


def simulate(n_games: int = 2000, streak_on: bool = True, strategies=DEFAULT_STRATS, seed: int = 0,
             strength_per_gold: float = 0.06, decay: float = 0.98, fight_noise: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    records = []
    for game in range(n_games):
        strat = [strategies[i % len(strategies)] for i in rng.permutation(8)]
        gold = np.zeros(8)
        strength = rng.normal(0, 0.3, 8)
        hp = np.full(8, 100.0)
        streak = np.zeros(8, int)
        alive = np.ones(8, bool)
        elim_order = []
        for stage in STAGES:
            s, r = stage
            gold += np.minimum(5, gold // 10)          # interest on gold held
            gold += passive(stage)
            if streak_on:
                gold += np.array([streak_gold(x) for x in streak])
            gold_at_planning = float(gold[alive].mean()) if alive.any() else 0.0  # what a VOD shows
            for i in range(8):                          # spending
                if not alive[i]:
                    continue
                st = strat[i]
                floor = 0 if s >= st.rolldown_stage else st.save_until
                spend = max(0.0, gold[i] - floor) * (1.0 if s >= st.rolldown_stage + 1 else st.spend_rate)
                gold[i] -= spend
                strength[i] = strength[i] * decay + strength_per_gold * spend
            if s == 1 or r == 4 or r == 7:              # PvE / carousel: no PvP outcome
                continue
            idx = rng.permutation(np.flatnonzero(alive))
            for a, b in zip(idx[::2], idx[1::2]):
                p_a = 1 / (1 + np.exp(-(strength[a] - strength[b]) / fight_noise))
                w, l = (a, b) if rng.random() < p_a else (b, a)
                gold[w] += 1
                streak[w] = streak[w] + 1 if streak[w] > 0 else 1
                streak[l] = streak[l] - 1 if streak[l] < 0 else -1
                hp[l] -= 2 + s * 1.5 + max(0.0, strength[w] - strength[l])
            for i in np.flatnonzero(alive & (hp <= 0)):
                alive[i] = False
                elim_order.append(i)
            records.append({"game": game, "stage": f"{s}-{r}", "gold_sd": float(gold[alive].std()) if alive.sum() > 1 else 0,
                            "strength_sd": float(strength[alive].std()) if alive.sum() > 1 else 0,
                            "mean_gold": gold_at_planning})
            if alive.sum() <= 1:
                break
        survivors = sorted(np.flatnonzero(alive), key=lambda i: -hp[i])
        order = survivors + elim_order[::-1]
        for place, i in enumerate(order, 1):
            records.append({"game": game, "stage": "final", "player_strategy": strat[i].name, "placement": place})
    return pd.DataFrame(records)


def compare_streak_rules(n_games: int = 1000, seed: int = 0) -> dict:
    on = simulate(n_games, True, seed=seed)
    off = simulate(n_games, False, seed=seed)

    def summary(df):
        curve = df[df["stage"] != "final"].groupby("stage", sort=False)[["gold_sd", "strength_sd", "mean_gold"]].mean()
        place = df[df["stage"] == "final"].groupby("player_strategy")["placement"].mean()
        return curve, place

    c_on, p_on = summary(on)
    c_off, p_off = summary(off)
    return {"curve_on": c_on, "curve_off": c_off, "placement_on": p_on, "placement_off": p_off,
            "mean_strength_sd_on": float(c_on["strength_sd"].mean()),
            "mean_strength_sd_off": float(c_off["strength_sd"].mean())}


def calibrate_to_vod(curves: pd.DataFrame, grid=(0.03, 0.06, 0.1), n_games: int = 300) -> dict:
    """Pick strength_per_gold whose simulated mean-gold curve best matches the VOD mean gold curve (MSE)."""
    target = curves.groupby("stage")["gold"].mean()
    best = None
    for k in grid:
        sim = simulate(n_games, True, strength_per_gold=k)
        sc = sim[sim["stage"] != "final"].groupby("stage")["mean_gold"].mean()
        j = pd.concat([target, sc], axis=1, join="inner").dropna()
        if j.empty:
            continue
        mse = float(((j.iloc[:, 0] - j.iloc[:, 1]) ** 2).mean())
        if best is None or mse < best["mse"]:
            best = {"strength_per_gold": k, "mse": mse, "n_stages_compared": len(j)}
    return best or {"note": "no overlapping stages between VOD curves and simulation"}
