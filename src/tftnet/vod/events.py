"""Event-sampled VOD extraction, sized for the Gemini free tier (5 requests/min, 250K tokens/min, 20/day).

Per game, three requests when everything fits (more only if one would pass the token cap):
  A  segmentation  whole game, low resolution, 1 frame / 5 s, as labelled parts   -> round boundaries
  B  snapshots     ONE request: a few frames at each planning start and before each combat, medium res
  C  zooms         ONE request: whole planning phases at high res, 1 fps, for the rounds that need it:
                   unreadable numbers, failed checks, gold drop >= 10, a Wisp offered, an expected Wisp unseen
Gold accounting then splits each zoomed round's spending into XP buys, units, the Wisp and rerolls.

Values are never imputed: a number no frame shows stays empty with confidence 'missing'. Every response is
cached in data/vods/raw/, keyed by its exact request, so reruns cost nothing and a run stopped by the daily
limit picks up where it left off.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..config import Settings
from . import prompts
from .gemini import VideoPart, estimate_tokens
from .rounds import (XP_PER_BUY, consensus, gold_accounting, has_shop, parse_stage, round_type, sell_value,
                     stage_index, ts_seconds, validate, wisp_schedule, xp_gained)
from .schema import Segmentation, Snapshots, Zooms

log = logging.getLogger(__name__)

PLANNING_S = 30.0  # fallback planning length when combat start was not read. UNVERIFIED for Set 18.
CONF_ORDER = {"exact": 0, "inferred": 1, "missing": 2}


@dataclass
class EventConfig:
    seg_fps: float = 0.2
    seg_res: str = "low"
    seg_part_s: float = 600.0
    snap_fps: float = 0.5
    snap_frames: int = 3
    snap_res: str = "medium"
    snap_requests: int = 1
    zoom_fps: float = 1.0
    zoom_res: str = "high"
    zoom_requests: int = 1
    zoom_drop: int = 10
    max_parts: int = 10       # video parts per request. Gemini documents up to 10 videos per request.
    audio: bool = False
    token_cap: int = 240_000  # stay under the 250K tokens/minute free-tier cap


# ---------------------------------------------------------------- small helpers
def fmt_ts(sec: float) -> str:
    if sec is None or pd.isna(sec):
        return ""
    sec = int(round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _ts(x) -> float:
    try:
        return ts_seconds(x)
    except (TypeError, ValueError):
        return np.nan


def _tri(x) -> bool | None:
    """True / False / None from a cell that may hold numpy bools or NaN."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    return bool(x)


def _key(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:10]


def batches(parts: list[VideoPart], res: str, audio: bool, cap: int, max_parts: int) -> list[list[VideoPart]]:
    out, cur = [], []
    for p in parts:
        if cur and (len(cur) >= max_parts or estimate_tokens(cur + [p], res, audio) > cap):
            out.append(cur)
            cur = []
        cur.append(p)
    if cur:
        out.append(cur)
    return out


def expected_stages(last: str) -> list[str]:
    """Every round up to `last`: 1-1..1-4, then x-1..x-7 (x-4 carousel, x-7 PvE). UNVERIFIED for Set 18."""
    p = parse_stage(last)
    if not p:
        return []
    out = []
    for s in range(1, p[0] + 1):
        for r in range(1, (5 if s == 1 else 8)):
            if (s, r) > p:
                return out
            out.append(f"{s}-{r}")
    return out


def _wisps(s: Settings) -> tuple[list[str], dict[str, float]]:
    from ..static import norm

    path = s.static_dir / "wisps.csv"
    if not path.exists():
        return [], {}
    w = pd.read_csv(path)
    return sorted(w["name"].astype(str)), {norm(n): float(c) for n, c in zip(w["name"], w["cost_gold"])}


def _unit_costs() -> dict[str, int]:
    from ..static import cost_lookup

    return cost_lookup()


class _Caller:
    """Sends one multi-part request through the cache, and counts what each pass cost."""

    def __init__(self, s: Settings, gem, cfg: EventConfig, game_id: str):
        self.s, self.gem, self.cfg, self.gid = s, gem, cfg, game_id
        self.stats: dict[str, dict] = {}

    def __call__(self, pass_: str, idx: int, parts: list[VideoPart], prompt: str, schema, res: str) -> dict:
        spec = {"model": getattr(self.gem, "model", "?"), "parts": [asdict(p) for p in parts], "prompt": prompt,
                "schema": schema.__name__, "res": res, "audio": self.cfg.audio}
        path = self.s.vod_dir / "raw" / f"{self.gid}__ev{pass_}{idx}_{_key(spec)}.json"
        st = self.stats.setdefault(pass_, {"requests": 0, "cached": 0, "parts": 0, "frames": 0, "est_tokens": 0,
                                           "prompt_tokens": 0, "video_tokens": 0, "audio_tokens": 0,
                                           "output_tokens": 0, "failed_parse": 0})
        st["parts"] += len(parts)
        st["frames"] += sum(p.frames for p in parts)
        st["est_tokens"] += estimate_tokens(parts, res, self.cfg.audio, prompt)
        if path.exists():
            rec = json.loads(path.read_text())
            st["cached"] += 1
        else:
            parsed, text, api, usage = self.gem.ask_parts(
                parts, prompt, schema, resolution=res, audio=self.cfg.audio,
                tag={"pass": pass_, "game_id": self.gid, "request": idx})
            rec = {"request": {"parts": spec["parts"], "res": res, "prompt_head": prompt[:300]}, "api": api,
                   "usage": usage, "raw_text": text, "parsed": parsed.model_dump() if parsed else None}
            path.parent.mkdir(parents=True, exist_ok=True)
            (path if parsed else path.with_suffix(".failed.json")).write_text(json.dumps(rec, indent=1))
            st["requests"] += 1
        u = rec.get("usage") or {}
        st["prompt_tokens"] += u.get("prompt_tokens", 0) or 0
        st["video_tokens"] += u.get("in_video_tokens", 0) or 0
        st["audio_tokens"] += u.get("in_audio_tokens", 0) or 0
        st["output_tokens"] += (u.get("output_tokens", 0) or 0) + (u.get("thoughts_tokens", 0) or 0)
        if not rec.get("parsed"):
            st["failed_parse"] += 1
            log.warning("%s pass %s request %d did not parse; see %s", self.gid, pass_, idx, path)
        return rec


# ---------------------------------------------------------------- pass A: segmentation
def segmentation_parts(url: str, start_s: float, end_s: float, cfg: EventConfig) -> list[VideoPart]:
    n = max(1, math.ceil((end_s - start_s) / cfg.seg_part_s))
    step = (end_s - start_s) / n
    out = []
    for i in range(n):
        a, b = start_s + i * step, start_s + (i + 1) * step
        out.append(VideoPart(url, a, b, cfg.seg_fps, f"Part {i + 1}: video {fmt_ts(a)} to {fmt_ts(b)}"))
    return out


def abs_time(t: float, part_no, parts: list[VideoPart]) -> float:
    """Gemini sometimes answers on the clip's clock instead of the video's. Map back to video time."""
    if t is None or pd.isna(t):
        return np.nan
    part = parts[int(part_no) - 1] if part_no and 1 <= int(part_no) <= len(parts) else None
    if part is None:
        part = next((p for p in parts if p.start_s - 5 <= t <= p.end_s + 5), None)
    if part is not None and t < part.start_s - 5 and t <= part.end_s - part.start_s + 5:
        return t + part.start_s
    return t


def parse_segmentation(results: list[tuple[dict, list[VideoPart]]]) -> tuple[pd.DataFrame, dict]:
    rows, meta = [], {"final_placement": None, "game_end_s": np.nan}
    for parsed, parts in results:
        if not parsed:
            continue
        for r in parsed.get("rounds", []):
            if not parse_stage(r.get("stage")):
                continue
            p = abs_time(_ts(r.get("planning")), r.get("part"), parts)
            c = abs_time(_ts(r.get("combat")), r.get("part"), parts)
            rows.append({"stage": parse_stage(r["stage"]) and "{}-{}".format(*parse_stage(r["stage"])),
                         "planning_s": p, "combat_s": c})
        if parsed.get("final_placement"):
            meta["final_placement"] = parsed["final_placement"]
        if parsed.get("game_end"):
            meta["game_end_s"] = abs_time(_ts(parsed["game_end"]), None, parts)
    seg = pd.DataFrame(rows, columns=["stage", "planning_s", "combat_s"])
    if seg.empty:
        return seg, meta
    seg = seg.dropna(subset=["planning_s"]).sort_values("planning_s")
    # One row per round, in order: boundary duplicates and out-of-order reads are dropped.
    keep, last = [], -1.0
    for i, r in seg.iterrows():
        k = stage_index(r["stage"])
        if k > last:
            keep.append(i)
            last = k
    seg = seg.loc[keep].reset_index(drop=True)
    bad = ~((seg["combat_s"] > seg["planning_s"]) & (seg["combat_s"] - seg["planning_s"] <= 90))
    seg.loc[bad, "combat_s"] = np.nan
    seg.loc[seg["stage"].map(round_type) == "carousel", "combat_s"] = np.nan
    return seg, meta


# ---------------------------------------------------------------- pass B: snapshots
def _planning_end(r) -> float:
    return r["combat_s"] if pd.notna(r["combat_s"]) else r["planning_s"] + PLANNING_S


def snapshot_parts(seg: pd.DataFrame, url: str, cfg: EventConfig) -> list[VideoPart]:
    """Two short windows per round (planning start, just before combat). If they don't fit
    max_parts x snap_requests, each round becomes one window, then consecutive rounds share a part."""
    cap = cfg.max_parts * cfg.snap_requests
    lead = (cfg.snap_frames - 1) / cfg.snap_fps
    two = []
    for _, r in seg.iterrows():
        p, c = r["planning_s"], _planning_end(r)
        if c - p > 2 * lead + 6:
            two.append(VideoPart(url, p + 1, p + 1 + lead, cfg.snap_fps,
                                 f"Round {r['stage']}, start of planning (planning starts {fmt_ts(p)})"))
            two.append(VideoPart(url, c - 1 - lead, c - 1, cfg.snap_fps,
                                 f"Round {r['stage']}, just before combat (combat starts {fmt_ts(c)})"))
        else:
            two.append(VideoPart(url, p + 1, max(p + 2, c - 1), cfg.snap_fps,
                                 f"Round {r['stage']}, planning {fmt_ts(p)} to combat {fmt_ts(c)}"))
    if len(two) <= cap:
        return [_numbered(p, i) for i, p in enumerate(two)]
    # one window per round, spanning the planning phase, about 2*snap_frames frames
    rounds = []
    for _, r in seg.iterrows():
        p, c = r["planning_s"], _planning_end(r)
        fps = min(cfg.snap_fps, 2 * cfg.snap_frames / max(4.0, c - p - 2))
        rounds.append((r["stage"], p, c, fps))
    per = math.ceil(len(rounds) / cap)
    out = []
    for g in range(0, len(rounds), per):
        grp = rounds[g:g + per]
        desc = "; ".join(f"round {st}: planning {fmt_ts(p)}, combat {fmt_ts(c)}" for st, p, c, _ in grp)
        out.append(VideoPart(url, grp[0][1] + 1, grp[-1][2] - 1, min(x[3] for x in grp), desc))
    return [_numbered(p, i) for i, p in enumerate(out)]


def _numbered(p: VideoPart, i: int) -> VideoPart:
    return VideoPart(p.url, p.start_s, p.end_s, p.fps,
                     f"Part {i + 1}: video {fmt_ts(p.start_s)} to {fmt_ts(p.end_s)}. {p.label}")


def _wisp_status(slot) -> str:
    s = str(slot or "").strip().lower()
    if s.startswith("wisp"):
        return "offered"
    if s in ("champion", "unit", "champ"):
        return "absent"
    return "unseen"


def summarize_snapshots(seg: pd.DataFrame, snaps: list[dict]) -> pd.DataFrame:
    by_stage: dict[str, list[dict]] = {}
    for sr in snaps:
        p = parse_stage(sr.get("stage"))
        if p:
            by_stage.setdefault(f"{p[0]}-{p[1]}", []).append(sr)
    rows = []
    for _, r in seg.iterrows():
        got = by_stage.get(r["stage"], [])
        start = [f for sr in got for f in sr.get("start") or []]
        end = [f for sr in got for f in sr.get("end") or []]
        row = {"stage": r["stage"], "planning_s": r["planning_s"], "combat_s": r["combat_s"]}
        row["gold"], row["gold_conf"] = consensus([f.get("gold") for f in start])
        row["hp"], row["hp_conf"] = consensus([f.get("hp") for f in start + end])
        row["level"], row["level_conf"] = consensus([f.get("level") for f in start])
        row["xp"], _ = consensus([f.get("xp") for f in start])
        row["xp_max"], _ = consensus([f.get("xp_max") for f in start])
        row["gold_end"], row["gold_end_conf"] = consensus([f.get("gold") for f in end])
        row["level_end"], _ = consensus([f.get("level") for f in end])
        row["xp_end"], _ = consensus([f.get("xp") for f in end])
        slots = [_wisp_status(sr.get("wisp_slot")) for sr in got]
        row["wisp_status"] = next((x for x in slots if x != "unseen"), "unseen") if has_shop(r["stage"]) else "no_shop"
        row["wisp_name"] = next((sr.get("wisp_name") for sr in got if sr.get("wisp_name")), None)
        row["wisp_bought"] = next((sr.get("wisp_bought") for sr in got if sr.get("wisp_bought") is not None), None)
        row["augment_picked"] = next((sr.get("augment") for sr in got if sr.get("augment")), None)
        row["source"] = "B" if got else "A"
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- pass C: zooms
def zoom_triggers(t: pd.DataFrame, cfg: EventConfig) -> pd.DataFrame:
    """Priority 0 unreadable, 1 failed check, 2 gold drop, 3 Wisp offered, 4 expected Wisp unseen."""
    v = validate(t[["stage", "gold", "hp", "level"]])
    out = []
    for i, r in t.iterrows():
        reasons, pr = [], 9
        if "missing" in (r["gold_conf"], r["hp_conf"], r["level_conf"]):
            reasons.append("unreadable")
            pr = min(pr, 0)
        if v.loc[i, "flags"]:
            reasons.append("check:" + v.loc[i, "flags"])
            pr = min(pr, 1)
        if round_type(r["stage"]) != "carousel":
            drop = r["gold"] - r["gold_end"] if pd.notna(r["gold"]) and pd.notna(r["gold_end"]) else np.nan
            if pd.isna(drop) and i + 1 < len(t) and pd.notna(t.loc[i + 1, "gold"]) and pd.notna(r["gold"]):
                drop = r["gold"] - t.loc[i + 1, "gold"]
            if pd.notna(drop) and drop >= cfg.zoom_drop:
                reasons.append(f"spend:{int(drop)}")
                pr = min(pr, 2)
            if r["wisp_status"] == "offered" and _tri(r.get("wisp_bought")) is not False:
                reasons.append("wisp_offered")
                pr = min(pr, 3)
            if r["wisp_status"] == "unseen" and _tri(r.get("wisp_expected")) is not False:
                reasons.append("wisp_unseen")
                pr = min(pr, 4)
        if reasons:
            out.append({"stage": r["stage"], "priority": pr, "reasons": ",".join(reasons), "order": i})
    return pd.DataFrame(out, columns=["stage", "priority", "reasons", "order"])


def zoom_parts(t: pd.DataFrame, trig: pd.DataFrame, url: str, cfg: EventConfig) -> tuple[list[VideoPart], list[str]]:
    cap = cfg.max_parts * cfg.zoom_requests
    chosen = trig.sort_values(["priority", "order"]).head(cap).sort_values("order")
    parts = []
    idx = t.set_index("stage")
    for st in chosen["stage"]:
        r = idx.loc[st]
        p, c = r["planning_s"], _planning_end(r)
        parts.append(VideoPart(url, max(0.0, p - 1), c + 1, cfg.zoom_fps,
                               f"Round {st}: planning {fmt_ts(p)} to combat {fmt_ts(c)}"))
    return [_numbered(p, i) for i, p in enumerate(parts)], list(chosen["stage"])


def apply_zooms(t: pd.DataFrame, zooms: list[dict], wisp_cost: dict[str, float],
                unit_cost: dict[str, int]) -> pd.DataFrame:
    from ..static import norm

    t = t.copy()
    by = {}
    for z in zooms:
        p = parse_stage(z.get("stage"))
        if p:
            by.setdefault(f"{p[0]}-{p[1]}", z)
    for col in ["xp_buys", "unit_cost", "wisp_cost", "sold_value", "rerolls", "rerolls_read", "spend"]:
        t[col] = np.nan
    t["acct_flags"] = ""
    for i, r in t.iterrows():
        z = by.get(r["stage"]) if r.get("zoomed") else None
        if z:
            for col, zc, conf in [("gold", "gold_start", "gold_conf"), ("hp", "hp", "hp_conf"),
                                  ("level", "level_start", "level_conf"), ("gold_end", "gold_end", "gold_end_conf")]:
                zv = z.get(zc)
                if zv is None:
                    continue
                if r[conf] == "exact" and pd.notna(r[col]) and r[col] != zv:
                    t.at[i, conf] = "inferred"  # B and C disagree; C (high res) wins
                else:
                    t.at[i, conf] = "exact"
                t.at[i, col] = zv
            for col, zc in [("xp", "xp_start"), ("xp_max", "xp_max_start"), ("level_end", "level_end"),
                            ("xp_end", "xp_end")]:
                if z.get(zc) is not None:
                    t.at[i, col] = z[zc]
            st = _wisp_status(z.get("wisp_slot"))
            if st != "unseen" and has_shop(r["stage"]):
                t.at[i, "wisp_status"] = st
            for col in ["wisp_name", "wisp_bought"]:
                if z.get(col) is not None:
                    t.at[i, col] = z[col]
            if z.get("augment") and not r.get("augment_picked"):
                t.at[i, "augment_picked"] = z["augment"]
            t.at[i, "source"] = "C"
        r = t.loc[i]
        flags = []
        gain = xp_gained(r["level"], r["xp"], r["level_end"], r["xp_end"], r["xp_max"])
        xp_buys = gain / XP_PER_BUY if pd.notna(gain) else np.nan
        read_buys = z.get("xp_buys") if z else None
        if pd.notna(xp_buys) and (xp_buys < 0 or xp_buys != int(xp_buys)):
            flags.append("xp_odd")
            xp_buys = float(read_buys) if read_buys is not None else np.nan
        elif pd.isna(xp_buys) and read_buys is not None:
            xp_buys = float(read_buys)
        elif read_buys is not None and pd.notna(xp_buys) and read_buys != xp_buys:
            flags.append("xp_buy_mismatch")
        units = sold = wcost = np.nan
        if z:
            costs = [unit_cost.get(norm(u)) for u in z.get("units_bought") or []]
            units = float(sum(costs)) if all(c is not None for c in costs) else np.nan
            if pd.isna(units):
                flags.append("unknown_unit")
            sv = [(unit_cost.get(norm(u.get("name"))), u.get("stars")) for u in z.get("units_sold") or []]
            sold = float(sum(sell_value(c, s) for c, s in sv)) if all(c is not None for c, _ in sv) else np.nan
            if _tri(r["wisp_bought"]) is True:
                wcost = wisp_cost.get(norm(r["wisp_name"]), np.nan) if r["wisp_name"] else np.nan
            elif r["wisp_status"] == "offered" and _tri(r["wisp_bought"]) is None:
                wcost = np.nan
            else:
                wcost = 0.0
        acct = gold_accounting(r["gold"], r["gold_end"], xp_buys, units, wcost, 0 if pd.isna(sold) else sold,
                               z.get("rerolls") if z else None)
        t.at[i, "xp_buys"], t.at[i, "unit_cost"], t.at[i, "wisp_cost"] = xp_buys, units, wcost
        t.at[i, "sold_value"], t.at[i, "spend"], t.at[i, "rerolls"] = sold, acct["spend"], acct["rerolls"]
        t.at[i, "rerolls_read"] = z.get("rerolls") if z and z.get("rerolls") is not None else np.nan
        t.at[i, "acct_flags"] = ";".join([f for f in [acct["acct_flags"]] + flags if f])
    return t


def finalize(t: pd.DataFrame) -> pd.DataFrame:
    t = t.copy()
    rer = [r if pd.notna(r) and float(r).is_integer() and r >= 0 else None for r in t.get("rerolls", [np.nan] * len(t))]
    t["wisp_expected"] = wisp_schedule(list(t["stage"]), list(t["wisp_status"]), rer)
    t["confidence"] = [max((r["gold_conf"], r["hp_conf"], r["level_conf"]), key=CONF_ORDER.get)
                       for _, r in t.iterrows()]
    t["timestamp"] = t["planning_s"].map(fmt_ts)
    t["video_s"] = t["planning_s"]
    return t


# ---------------------------------------------------------------- one game
def extract_game(s: Settings, gem, g, cfg: EventConfig) -> tuple[pd.DataFrame, dict]:
    """g: a manifest row (game_id, video_url, start_s, end_s, streamer_name)."""
    call = _Caller(s, gem, cfg, g.game_id)
    url, name = g.video_url, g.streamer_name
    wisp_names, wisp_cost = _wisps(s)

    # A
    parts = segmentation_parts(url, float(g.start_s), float(g.end_s), cfg)
    prompt = prompts.segmentation_prompt(name)
    results = []
    for k, b in enumerate(batches(parts, cfg.seg_res, cfg.audio, cfg.token_cap, cfg.max_parts)):
        results.append((call("A", k, b, prompt, Segmentation, cfg.seg_res).get("parsed"), b))
    seg, meta = parse_segmentation(results)
    summary = {"game_id": g.game_id, "rounds_found": len(seg)}
    if seg.empty:
        log.warning("%s: segmentation found no rounds", g.game_id)
        return pd.DataFrame(), {**summary, "passes": call.stats}

    # B
    parts = snapshot_parts(seg, url, cfg)
    prompt = prompts.snapshot_prompt(name, wisp_names)
    snaps = []
    bs = batches(parts, cfg.snap_res, cfg.audio, cfg.token_cap, cfg.max_parts)
    if len(bs) > cfg.snap_requests:
        log.warning("%s: snapshots need %d requests (token cap), not %d", g.game_id, len(bs), cfg.snap_requests)
    for k, b in enumerate(bs):
        snaps += (call("B", k, b, prompt, Snapshots, cfg.snap_res).get("parsed") or {}).get("rounds", [])
    t = summarize_snapshots(seg, snaps)
    t["wisp_expected"] = wisp_schedule(list(t["stage"]), list(t["wisp_status"]))

    # C
    trig = zoom_triggers(t, cfg)
    t["zoomed"] = False
    t["zoom_reason"] = ""
    zooms = []
    chosen = []
    if len(trig):
        parts, chosen = zoom_parts(t, trig, url, cfg)
        prompt = prompts.zoom_prompt(name, wisp_names)
        for k, b in enumerate(batches(parts, cfg.zoom_res, cfg.audio, cfg.token_cap, cfg.max_parts)):
            zooms += (call("C", k, b, prompt, Zooms, cfg.zoom_res).get("parsed") or {}).get("rounds", [])
        reason = dict(zip(trig["stage"], trig["reasons"]))
        t["zoom_reason"] = t["stage"].map(lambda st: reason.get(st, ""))
        t.loc[t["stage"].isin(chosen), "zoomed"] = True
        t.loc[(t["zoom_reason"] != "") & ~t["zoomed"], "zoom_reason"] = "skipped:" + t["zoom_reason"]
    t = finalize(apply_zooms(t, zooms, wisp_cost, _unit_costs()))
    t.insert(0, "game_id", g.game_id)
    t["final_placement_gemini"] = meta["final_placement"]

    exp = expected_stages(t["stage"].iloc[-1])
    summary.update({
        "rounds_expected": len(exp), "rounds_missing": sorted(set(exp) - set(t["stage"]), key=stage_index),
        "zoom_triggers": len(trig), "zoomed": len(chosen), "zoom_skipped": int(len(trig) - len(chosen)),
        "confidence": t["confidence"].value_counts().to_dict(),
        "gold_conf": t["gold_conf"].value_counts().to_dict(),
        "acct_flags": t["acct_flags"][t["acct_flags"] != ""].str.split(";").explode().value_counts().to_dict(),
        "passes": call.stats,
        "requests_sent": sum(v["requests"] for v in call.stats.values()),
        "requests_total": sum(v["requests"] + v["cached"] for v in call.stats.values()),
    })
    (s.vod_dir / "raw" / f"{g.game_id}__events_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return t, summary


def extract_events(s: Settings, gem, manifest: pd.DataFrame, cfg: EventConfig | None = None
                   ) -> tuple[pd.DataFrame, list[dict]]:
    """Runs every manifest game. DailyLimitReached propagates after the finished games are returned
    through the exception's .partial attribute, so the caller can save them."""
    from .budget import DailyLimitReached

    cfg = cfg or EventConfig()
    tables, summaries = [], []
    for g in manifest.itertuples(index=False):
        try:
            t, summ = extract_game(s, gem, g, cfg)
        except DailyLimitReached as e:
            e.partial = (pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(), summaries)
            raise
        summaries.append(summ)
        if len(t):
            tables.append(t)
    return (pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()), summaries


def usage_report(records: list[dict]) -> pd.DataFrame:
    """Token log -> one row per game and pass."""
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for c in ["game_id", "pass", "status", "frames", "prompt_tokens", "in_video_tokens", "in_audio_tokens",
              "in_text_tokens", "output_tokens", "thoughts_tokens", "total_tokens"]:
        if c not in df:
            df[c] = np.nan
    df["game_id"] = df["game_id"].fillna("(single-clip)")
    ok = df["status"] == "ok"
    g = df.groupby(["game_id", "pass"])
    out = pd.DataFrame({
        "requests_ok": g["status"].apply(lambda x: int((x == "ok").sum())),
        "requests_failed": g["status"].apply(lambda x: int((x != "ok").sum())),
    })
    num = df[ok].groupby(["game_id", "pass"])[["frames", "prompt_tokens", "in_video_tokens", "in_audio_tokens",
                                               "in_text_tokens", "output_tokens", "thoughts_tokens",
                                               "total_tokens"]].sum(min_count=1)
    return out.join(num).reset_index()
