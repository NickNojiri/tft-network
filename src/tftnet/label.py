"""Comp labeling: turn each final board into a comp label like 'blossom_ahri'.

Rules (see docs/plan.md section 2):
1. Active non-unique traits (style >= 1). Primary = highest style, then most units.
2. Carry = unit with the most items (>= 2), ties by cost then star. Lux is never the carry
   (she appears in many comps), so ties skip her.
3. Label = '{primary}_{carry}'. Rare labels (< min_share of boards) collapse to 'other'.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .static import is_lux, unique_traits


def label_board(units: list[dict], traits: list[dict]) -> tuple[str, str, str]:
    uniq = unique_traits()
    active = [t for t in traits if t["style"] >= 1 and t["name"] not in uniq and t["num_units"] >= 2]
    primary = max(active, key=lambda t: (t["style"], t["num_units"]))["name"] if active else "none"

    candidates = [u for u in units if len(u.get("items", [])) >= 2 and not is_lux(u["id"])]
    if not candidates:
        candidates = [u for u in units if u.get("items") and not is_lux(u["id"])]
    if candidates:
        carry = max(candidates, key=lambda u: (len(u["items"]), u.get("cost") or 0, u.get("star", 1)))["id"]
    else:
        carry = "none"
    return f"{primary}_{carry}", primary, carry


def label_players(df: pd.DataFrame, min_share: float = 0.01) -> pd.DataFrame:
    df = df.copy()
    out = [label_board(json.loads(u), json.loads(t)) for u, t in zip(df["units"], df["traits"])]
    df["comp_raw"] = [o[0] for o in out]
    df["primary_trait"] = [o[1] for o in out]
    df["carry"] = [o[2] for o in out]
    share = df["comp_raw"].value_counts(normalize=True)
    keep = set(share[share >= min_share].index)
    df["comp"] = np.where(df["comp_raw"].isin(keep), df["comp_raw"], "other")
    df["n_same_comp_in_lobby"] = df.groupby(["match_id", "comp"])["comp"].transform("size")
    df.loc[df["comp"] == "other", "n_same_comp_in_lobby"] = np.nan
    return df


def export_validation_sample(df: pd.DataFrame, n: int = 100, seed: int = 7) -> pd.DataFrame:
    """Boards for two teammates to hand-label blind. Fill in 'label_a' and 'label_b'."""
    sample = df.sample(min(n, len(df)), random_state=seed)
    rows = []
    for _, r in sample.iterrows():
        units = json.loads(r["units"])
        traits = json.loads(r["traits"])
        rows.append({
            "match_id": r["match_id"], "puuid": r["puuid"], "placement": r["placement"],
            "units": "; ".join(f"{u['id']}*{u['star']}" + (f"[{','.join(u['items'])}]" if u["items"] else "") for u in units),
            "active_traits": "; ".join(f"{t['name']}{t['num_units']}" for t in traits if t["style"] >= 1),
            "rule_label": r.get("comp", ""), "label_a": "", "label_b": "",
        })
    return pd.DataFrame(rows)


def score_validation(filled: pd.DataFrame) -> dict:
    """Agreement rates from a filled validation CSV."""
    f = filled.dropna(subset=["label_a", "label_b"])
    f = f[(f["label_a"].astype(str) != "") & (f["label_b"].astype(str) != "")]
    if f.empty:
        return {"n": 0}
    return {
        "n": len(f),
        "human_human": float((f["label_a"] == f["label_b"]).mean()),
        "rule_vs_a": float((f["rule_label"] == f["label_a"]).mean()),
        "rule_vs_b": float((f["rule_label"] == f["label_b"]).mean()),
    }
