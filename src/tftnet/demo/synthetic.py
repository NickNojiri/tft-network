"""Synthetic data with KNOWN effects planted, so the whole pipeline can be run and checked offline.

Planted truths (the analyses should recover these):
- Comps have latent strengths; PageRank and Bradley-Terry should correlate with held-out placement.
- Congestion: each extra player on the same comp costs CONGESTION places on average (H2 slope ~ positive).
- Across 3 patches, pick rates move toward last patch's over-performers (H3 dynamics positive).
- VOD games: loss-streak openers bank more gold; HP at 4-1 drives placement.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..static import champions, traits

CONGESTION = 0.6
RARITY = {1: 0, 2: 1, 3: 2, 4: 4, 5: 6}
PATCHES = ["16.20", "16.21", "16.22"]  # stand-ins for Riot game_version major.minor


def _api_name(name: str) -> str:
    return "TFT18_" + re.sub(r"[^A-Za-z0-9]", "", name)


def _breakpoints() -> dict[str, list[int]]:
    out = {}
    for _, r in traits().iterrows():
        bps = [int(x) for x in str(r["breakpoints"]).split(";") if x.strip().isdigit()]
        out[r["trait"]] = sorted(b for b in bps if b > 0) or [1]
    return out


def _traits_for(units: list[str], champ: pd.DataFrame, bps: dict) -> list[dict]:
    counts: dict[str, int] = {}
    for u in units:
        for t in champ.loc[u, "trait_list"]:
            counts[t] = counts.get(t, 0) + 1
    out = []
    for t, n in counts.items():
        reached = sum(n >= b for b in bps.get(t, [1]))
        out.append({"name": "TFT18_" + re.sub(r"[^A-Za-z0-9]", "", t), "num_units": n,
                    "style": min(4, reached), "tier_current": reached, "tier_total": len(bps.get(t, [1]))})
    return out


def define_comps(n: int = 16, seed: int = 1) -> list[dict]:
    rng = np.random.default_rng(seed)
    ch = champions()
    ch = ch[~ch["name"].str.startswith("Lux")].set_index("name")
    tr = traits()
    big = tr[(tr["num_champions"] >= 5) & (tr["type"] == "origin")]["trait"].tolist() + \
          tr[(tr["num_champions"] >= 5) & (tr["type"] == "class")]["trait"].tolist()
    comps = []
    for t in big:
        members = [c for c in ch.index if t in ch.loc[c, "trait_list"]]
        carries = [c for c in members if ch.loc[c, "cost"] >= 3] or members
        for carry in carries[:2]:
            core = [carry] + [m for m in members if m != carry][:4]
            fill = [c for c in rng.permutation(ch.index) if c not in core][:3]
            comps.append({"trait": t, "carry": carry, "units": core + list(fill)})
            if len(comps) >= n:
                break
        if len(comps) >= n:
            break
    for c in comps:
        c["strength"] = float(rng.normal(0, 1))
    return comps


def make_matches(out_dir: Path, n_matches: int = 1500, seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    ch = champions()
    ch = ch[~ch["name"].str.startswith("Lux")].set_index("name")
    bps = _breakpoints()
    comps = define_comps()
    k = len(comps)
    strength = np.array([c["strength"] for c in comps])
    pop = np.ones(k) / k
    out_dir.mkdir(parents=True, exist_ok=True)
    per_patch = n_matches // len(PATCHES)
    m_id = 0
    for pi, patch in enumerate(PATCHES):
        if pi:
            # Best response: players move toward comps that over-performed; balance patch shifts strengths.
            pop = pop * np.exp(0.8 * strength)
            pop /= pop.sum()
            strength = 0.7 * strength + rng.normal(0, 0.4, k)
        for _ in range(per_patch):
            m_id += 1
            choice = rng.choice(k, size=8, p=pop)
            kcount = np.array([np.sum(choice == c) for c in choice])
            score = strength[choice] - CONGESTION * (kcount - 1) + rng.normal(0, 1.2, 8)
            placement = np.argsort(np.argsort(-score)) + 1
            parts = []
            for j in range(8):
                comp = comps[choice[j]]
                units_names = comp["units"][: rng.integers(6, 9)]
                units = []
                for u in units_names:
                    rec = ch.loc[u, "recommended_items"].split(";") if isinstance(ch.loc[u, "recommended_items"], str) else []
                    n_items = 3 if u == comp["carry"] else int(rng.integers(0, 2))
                    items = ["DA_" + re.sub(r"[^A-Za-z0-9]", "", x) for x in rec[:n_items]]
                    star = 3 if (ch.loc[u, "cost"] <= 2 and rng.random() < 0.15) else 2 if rng.random() < 0.7 else 1
                    units.append({"character_id": _api_name(u), "tier": star, "rarity": RARITY[int(ch.loc[u, "cost"])],
                                  "itemNames": items, "name": ""})
                parts.append({"puuid": f"p{rng.integers(0, 3000):05d}", "placement": int(placement[j]),
                              "win": bool(placement[j] <= 4), "level": int(rng.integers(7, 10)),
                              "last_round": int(rng.integers(20, 40)), "gold_left": int(rng.integers(0, 30)),
                              "players_eliminated": int(rng.integers(0, 3)), "total_damage_to_players": int(rng.integers(20, 150)),
                              "time_eliminated": float(rng.uniform(1200, 2200)),
                              "traits": _traits_for(units_names, ch, bps), "units": units})
            mid = f"NA1_DEMO{m_id:06d}"
            data = {"metadata": {"data_version": "6", "match_id": mid, "participants": [p["puuid"] for p in parts]},
                    "info": {"game_datetime": 1788000000000 + m_id * 1000, "game_length": 2100.0,
                             "game_version": f"Linux Version {patch}.123.4567 [PUBLIC]", "queue_id": 1100,
                             "tft_set_number": 18, "tft_game_type": "standard", "participants": parts}}
            (out_dir / f"{mid}.json").write_text(json.dumps(data))
    return comps


def make_vods(vod_dir: Path, n_games: int = 30, seed: int = 0, noise: float = 0.04) -> pd.DataFrame:
    """Writes manifest.csv, a pre-extracted 'Gemini' round table with realistic misreads, and
    hand labels (truth) for 3 games."""
    from ..analysis.econ_sim import STAGES, passive, streak_gold

    rng = np.random.default_rng(seed)
    (vod_dir / "hand_labels").mkdir(parents=True, exist_ok=True)
    manifest, gem_rows, truth_rows = [], [], []
    for gi in range(n_games):
        gid = f"demo_{gi:02d}"
        opener = rng.choice(["win_streak", "loss_streak", "mixed"])
        p_win = {"win_streak": 0.8, "loss_streak": 0.15, "mixed": 0.5}[opener]
        gold, hp, level, streak, t = 0, 100, 1, 0, 60.0
        for (s, r) in STAGES:
            gold += min(5, gold // 10) + passive((s, r)) + streak_gold(streak)
            truth = {"game_id": gid, "stage": f"{s}-{r}", "gold": int(gold), "hp": int(max(hp, 0)), "level": level,
                     "timestamp": f"{int(t // 60):02d}:{int(t % 60):02d}"}
            truth_rows.append(truth)
            # spend
            save = 50 if opener == "loss_streak" and s <= 3 else 30
            if s >= 4:
                save = 10
            spend = max(0, gold - save)
            gold -= spend
            level = min(9, level + (1 if spend > 8 and rng.random() < 0.5 else 0))
            if s >= 2 and r not in (4, 7):
                win = rng.random() < (p_win if s <= 3 else 0.5)
                streak = (streak + 1 if streak > 0 else 1) if win else (streak - 1 if streak < 0 else -1)
                if not win:
                    hp -= int(2 + s * 2 + rng.integers(0, 6))
                else:
                    gold += 1
            t += 75
            if hp <= 0:
                break
        hp41 = next((x["hp"] for x in truth_rows[::-1] if x["game_id"] == gid and x["stage"] == "4-1"), 0)
        placement = int(np.clip(round(8 - hp41 / 14 + rng.normal(0, 1.2)), 1, 8))
        manifest.append({"game_id": gid, "video_url": f"https://www.youtube.com/watch?v=DEMO{gi:04d}", "start_s": 0,
                         "end_s": t + 60, "channel": f"streamer_{gi % 5}", "streamer_name": f"Streamer{gi % 5}",
                         "streamer_riot_id": f"Streamer{gi % 5}#NA1", "patch": "18.5", "final_placement": placement,
                         "notes": "synthetic"})
    truth_df = pd.DataFrame(truth_rows)
    gem = truth_df.copy()
    # Plant misreads like a vision model would make: digit swaps on gold, a few HP misreads, missing values.
    bad = rng.random(len(gem)) < noise
    gem.loc[bad, "gold"] = gem.loc[bad, "gold"] + rng.choice([-10, 10, 1, -1], bad.sum())
    hp_bad = rng.random(len(gem)) < noise / 2
    gem.loc[hp_bad, "hp"] = gem.loc[hp_bad, "hp"] + 20
    miss = rng.random(len(gem)) < noise / 2
    gem.loc[miss, "level"] = np.nan
    gem["video_s"] = gem["timestamp"].map(lambda x: int(x[:2]) * 60 + int(x[3:]))
    pd.DataFrame(manifest).to_csv(vod_dir / "manifest.csv", index=False)
    gem.to_csv(vod_dir / "gemini_rounds_raw.csv", index=False)
    for gid in ["demo_00", "demo_01", "demo_02"]:
        truth_df[truth_df["game_id"] == gid][["game_id", "stage", "gold", "hp", "level"]].to_csv(
            vod_dir / "hand_labels" / f"{gid}.csv", index=False)
    return gem
