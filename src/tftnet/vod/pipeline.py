"""VOD pipeline: manifest -> Gemini pass 1 (economy) -> checks -> Gemini self-review of flagged rounds
-> derived fields -> Gemini pass 2 (board state at key rounds) -> accuracy reports.

Every Gemini response is cached in data/vods/raw/, so reruns cost nothing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Settings
from . import prompts
from .rounds import derive, game_features, stage_index, ts_seconds, validate
from .schema import BoardState, EconomyTable, RoundCheck

log = logging.getLogger(__name__)

KEY_ROUNDS = ["2-1", "3-2", "4-1", "4-5"]
MANIFEST_COLS = ["game_id", "video_url", "start_s", "end_s", "channel", "streamer_name", "streamer_riot_id",
                 "patch", "final_placement", "notes"]


def load_manifest(s: Settings) -> pd.DataFrame:
    path = s.vod_dir / "manifest.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing. Copy data/vods/manifest.example.csv and fill it in.")
    m = pd.read_csv(path)
    missing = set(MANIFEST_COLS[:6]) - set(m.columns)
    if missing:
        raise ValueError(f"manifest.csv missing columns: {missing}")
    return m


def _cache(s: Settings, name: str) -> Path:
    return s.vod_dir / "raw" / f"{name}.json"


def _cached_call(s: Settings, gem, name: str, **kw) -> dict:
    path = _cache(s, name)
    if path.exists():
        return json.loads(path.read_text())
    parsed, text, api = gem.ask_video(**kw)
    rec = {"request": {k: v for k, v in kw.items() if k != "schema"}, "api": api, "raw_text": text,
           "parsed": parsed.model_dump() if parsed else None}
    rec["request"]["prompt"] = rec["request"]["prompt"][:400] + "..."
    target = path if parsed else path.with_suffix(".failed.json")  # failed parses are retried next run
    target.write_text(json.dumps(rec, indent=1))
    return rec


# ---------------------------------------------------------------- pass 1
def extract_economy(s: Settings, gem, manifest: pd.DataFrame, fps: float = 0.25) -> pd.DataFrame:
    tables = []
    for g in manifest.itertuples(index=False):
        rec = _cached_call(s, gem, f"{g.game_id}__economy", url=g.video_url,
                           prompt=prompts.economy_prompt(g.streamer_name), schema=EconomyTable,
                           start_s=float(g.start_s), end_s=float(g.end_s), fps=fps)
        if not rec["parsed"]:
            log.warning("%s: Gemini output did not parse; see %s", g.game_id, _cache(s, f"{g.game_id}__economy"))
            continue
        df = pd.DataFrame(rec["parsed"]["rounds"])
        df.insert(0, "game_id", g.game_id)
        df["video_s"] = df["timestamp"].map(ts_seconds)
        # Timestamps may be relative to the clip or to the full video. Detect and store absolute time.
        clip_len = float(g.end_s) - float(g.start_s)
        if df["video_s"].max() <= clip_len + 60:
            df["video_s"] = df["video_s"] + float(g.start_s)
        df["final_placement_gemini"] = rec["parsed"].get("final_placement")
        tables.append(df)
    return pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()


# ---------------------------------------------------------------- Gemini self-review
def review_flagged(s: Settings, gem, rounds: pd.DataFrame, manifest: pd.DataFrame,
                   window: tuple[float, float] = (-5, 30)) -> pd.DataFrame:
    """Gemini's second look: re-read each flagged round from a short, high-frame-rate clip.
    Original values are kept in *_pass1 columns, so the change is auditable."""
    m = manifest.set_index("game_id")
    r = rounds.copy()
    for col in ["gold", "hp", "level"]:
        if f"{col}_pass1" not in r:  # keep the original reading if review is run twice
            r[f"{col}_pass1"] = r[col]
    r["review"] = r["review"].fillna("") if "review" in r else ""
    flagged = r[(r["flags"] != "") & r["video_s"].notna()]
    log.info("Gemini reviewing %d flagged rounds", len(flagged))
    for i, row in flagged.iterrows():
        g = m.loc[row["game_id"]]
        rec = _cached_call(s, gem, f"{row['game_id']}__check_{row['stage']}", url=g["video_url"],
                           prompt=prompts.round_check_prompt(row["stage"], g["streamer_name"]), schema=RoundCheck,
                           start_s=max(0.0, row["video_s"] + window[0]), end_s=row["video_s"] + window[1], fps=1.0)
        p = rec.get("parsed")
        if not p:
            r.at[i, "review"] = "no_parse"
            continue
        changed = []
        for col in ["gold", "hp", "level"]:
            if p.get(col) is not None and p[col] != row[col]:
                r.at[i, col] = p[col]
                changed.append(col)
        r.at[i, "review"] = f"conf={p.get('confidence')};changed={','.join(changed) or 'none'}"
    # Re-run checks per game after corrections
    return pd.concat([validate(gr.drop(columns=["flags", "stage_idx"], errors="ignore"))
                      .assign(review=gr["review"].values)
                      for _, gr in r.groupby("game_id", sort=False)], ignore_index=True)


# ---------------------------------------------------------------- pass 2
def extract_boards(s: Settings, gem, rounds: pd.DataFrame, manifest: pd.DataFrame,
                   window: tuple[float, float] = (-5, 35)) -> list[dict]:
    m = manifest.set_index("game_id")
    out = []
    for gid, gr in rounds.groupby("game_id"):
        g = m.loc[gid]
        last_stage = gr.sort_values("stage_idx")["stage"].iloc[-1]
        for stage in dict.fromkeys(KEY_ROUNDS + [last_stage]):
            row = gr[gr["stage"] == stage]
            if row.empty or pd.isna(row["video_s"].iloc[0]):
                continue
            t = float(row["video_s"].iloc[0])
            rec = _cached_call(s, gem, f"{gid}__board_{stage}", url=g["video_url"],
                               prompt=prompts.board_prompt(stage, g["streamer_name"]), schema=BoardState,
                               start_s=max(0.0, t + window[0]), end_s=t + window[1], fps=1.0)
            if rec["parsed"]:
                out.append({"game_id": gid, "is_last": stage == last_stage, **rec["parsed"]})
    return out


# ---------------------------------------------------------------- assemble
def build_tables(s: Settings, rounds: pd.DataFrame, manifest: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Derived round table + one row per game. Final placement prefers the manifest (human) value."""
    parts = []
    for _, gr in rounds.groupby("game_id", sort=False):
        if "flags" not in gr:
            gr = validate(gr)
        parts.append(derive(gr))
    r = pd.concat(parts, ignore_index=True)
    m = manifest.set_index("game_id")
    games = []
    for gid, gr in r.groupby("game_id"):
        feat = game_features(gr)
        placement = m.loc[gid, "final_placement"] if "final_placement" in m.columns else np.nan
        if pd.isna(placement) and "final_placement_gemini" in gr:
            placement = gr["final_placement_gemini"].dropna().iloc[0] if gr["final_placement_gemini"].notna().any() else np.nan
        games.append({"game_id": gid, "channel": m.loc[gid].get("channel"), "patch": m.loc[gid].get("patch"),
                      "final_placement": placement, "n_rounds": len(gr),
                      "share_rounds_flagged": float((gr["flags"] != "").mean()), **feat})
    r.to_csv(s.processed_dir / "vod_rounds.csv", index=False)
    g = pd.DataFrame(games)
    g.to_csv(s.processed_dir / "vod_games.csv", index=False)
    return r, g


# ---------------------------------------------------------------- accuracy
def accuracy_vs_hand_labels(rounds: pd.DataFrame, hand: pd.DataFrame) -> pd.DataFrame:
    """hand: columns game_id, stage, gold, hp, level (from data/vods/hand_labels/*.csv)."""
    j = hand.merge(rounds, on=["game_id", "stage"], how="left", suffixes=("_true", "_gem"))
    rows = []
    for col in ["gold", "hp", "level"]:
        t, p = j[f"{col}_true"], j[f"{col}_gem"]
        have = t.notna()
        rows.append({"field": col, "n_labeled": int(have.sum()),
                     "coverage": float(p[have].notna().mean()) if have.any() else np.nan,
                     "exact_match": float((t[have] == p[have]).mean()) if have.any() else np.nan,
                     "mean_abs_error": float((t[have] - p[have]).abs().mean()) if have.any() else np.nan})
    missing_rounds = int(j[["gold_gem", "hp_gem", "level_gem"]].isna().all(axis=1).sum())
    out = pd.DataFrame(rows)
    out.attrs["rounds_not_found"] = missing_rounds
    return out


def accuracy_vs_api(boards: list[dict], api_boards: dict[str, list[dict]]) -> pd.DataFrame:
    """Compare Gemini's last-round board to the streamer's final board from the Riot API.
    api_boards: game_id -> parsed units list (from parse.parse_match for the streamer's puuid)."""
    from ..static import norm

    rows = []
    for b in boards:
        if not b.get("is_last") or b["game_id"] not in api_boards:
            continue
        truth = {u["id"]: u for u in api_boards[b["game_id"]]}
        pred = {norm(u["name"]): u for u in b.get("board_units", [])}
        hit = set(truth) & set(pred)
        star_ok = sum(1 for k in hit if pred[k].get("stars") == truth[k]["star"])
        item_true = sum(len(truth[k]["items"]) for k in hit)
        item_hit = sum(len(set(map(norm, pred[k].get("items", []))) & set(truth[k]["items"])) for k in hit)
        rows.append({"game_id": b["game_id"], "unit_precision": len(hit) / max(1, len(pred)),
                     "unit_recall": len(hit) / max(1, len(truth)), "star_accuracy": star_ok / max(1, len(hit)),
                     "item_recall": item_hit / max(1, item_true)})
    return pd.DataFrame(rows)


def load_hand_labels(s: Settings) -> pd.DataFrame:
    files = list((s.vod_dir / "hand_labels").glob("*.csv"))
    files = [f for f in files if not f.name.startswith("TEMPLATE")]
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True) if files else pd.DataFrame()


def stage_sort(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(_k=df["stage"].map(stage_index)).sort_values(["game_id", "_k"]).drop(columns="_k")
