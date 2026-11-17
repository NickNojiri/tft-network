"""Command line: python -m tftnet <command>. Run `python -m tftnet -h` for the list."""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
from pathlib import Path

import pandas as pd

from .config import get_settings

log = logging.getLogger("tftnet")


def _res_path(s, name):
    p = s.out_dir / "results"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{name}.pkl"


def save_result(s, name, obj):
    _res_path(s, name).write_bytes(pickle.dumps(obj))


def load_result(s, name):
    p = _res_path(s, name)
    return pickle.loads(p.read_bytes()) if p.exists() else None


def labeled(s) -> pd.DataFrame:
    p = s.processed_dir / "players_labeled.parquet"
    if not p.exists():
        sys.exit("Run `python -m tftnet parse` and `python -m tftnet label` first.")
    return pd.read_parquet(p)


# ------------------------------------------------------------------ commands
def cmd_collect(a, s):
    from .riot.client import RiotClient
    from .riot import collect

    client = RiotClient(s.riot_api_key, s.platform, s.region)
    if a.step in ("seed", "all"):
        collect.seed_players(client, s)
    if a.step in ("ids", "all"):
        collect.collect_match_ids(client, s, per_player=a.per_player, max_players=a.max_players)
    if a.step in ("matches", "all"):
        collect.collect_matches(client, s, limit=a.limit)
    log.info("Riot API calls this run: %d", client.calls)


def cmd_smoke(a, s):
    from .riot.client import RiotClient
    from . import smoke

    print(smoke.run(s, RiotClient(s.riot_api_key, s.platform, s.region), a.players, a.matches))


def cmd_parse(a, s):
    from .parse import build_players

    q = () if a.any_queue else None
    df = build_players(s, queue_ids=q)
    if not df.empty:
        log.info("Queue ids seen: %s | patches: %s", df["queue_id"].value_counts().to_dict(),
                 df.groupby("patch")["match_id"].nunique().to_dict())


def cmd_label(a, s):
    from .parse import load_players
    from . import label

    if a.score_validation:
        print(label.score_validation(pd.read_csv(a.score_validation)))
        return
    df = label.label_players(load_players(s), min_share=a.min_share)
    if a.contention:
        from .analysis.congestion import add_contention
        df = add_contention(df)
    df.to_parquet(s.processed_dir / "players_labeled.parquet", index=False)
    vc = df["comp"].value_counts()
    log.info("%d comps (+other). 'other' share: %.1f%%. Top: %s", len(vc) - ("other" in vc),
             100 * vc.get("other", 0) / len(df), vc.head(8).to_dict())
    if a.export_validation:
        out = s.out_dir / "label_validation_sample.csv"
        label.export_validation_sample(df).to_csv(out, index=False)
        log.info("Hand-label sheet: %s", out)


def cmd_h1(a, s):
    from .analysis import pagerank
    from .parse import split_train_test

    train, test = split_train_test(labeled(s))
    res = pagerank.evaluate(train, test, n_boot=a.n_boot)
    res["rankings"].to_csv(s.processed_dir / "comp_rankings.csv")
    save_result(s, "h1", res)
    for k, v in res["spearman_vs_test"].items():
        print(f"{k:28s} rho={v['estimate']:+.3f}  [{v['ci_low']:+.3f}, {v['ci_high']:+.3f}]")


def cmd_h2(a, s):
    from .analysis import congestion

    res = congestion.analyze(labeled(s), n_boot=a.n_boot, n_perm=a.n_perm)
    save_result(s, "h2", res)
    sl = res["slope_per_extra_player"]
    print(f"placement change per extra same-comp player: {sl['estimate']:+.3f} [{sl['ci_low']:+.3f}, {sl['ci_high']:+.3f}], "
          f"permutation p={res['permutation_p_one_sided']:.3f}")
    print(res["by_k"])


def cmd_h3(a, s):
    from .analysis import equilibrium

    res = equilibrium.analyze(labeled(s), min_games=a.min_games)
    res["table"].to_csv(s.processed_dir / "comp_patch_table.csv", index=False)
    save_result(s, "h3", res)
    print("static:", res["static"])
    d = res["dynamics"]
    print({k: v for k, v in d.items() if k != "pairs"})


def cmd_coven(a, s):
    from .analysis.streaks import coven_track_a

    res = coven_track_a(labeled(s))
    save_result(s, "coven", res)
    print(res)


def _budget(s):
    from .vod.budget import Budget
    return Budget(s.vod_dir / "raw" / "gemini_budget.json", daily_limit=s.gemini_daily_limit, rpm=s.gemini_rpm,
                  tpm=s.gemini_tpm)


def _gem(s):
    from .vod.budget import UsageLog
    from .vod.gemini import GeminiVideo
    return GeminiVideo(s.gemini_api_key, s.gemini_model, budget=_budget(s),
                       usage_log=UsageLog(s.vod_dir / "raw" / "token_log.jsonl"),
                       caps_path=s.vod_dir / "raw" / "gemini_caps.json")


def _event_config(a):
    from .vod.events import EventConfig
    cfg = EventConfig()
    for k in ["seg_fps", "snap_res", "zoom_fps", "zoom_requests", "max_parts", "snap_max_parts"]:
        if getattr(a, k, None) is not None:
            setattr(cfg, k, getattr(a, k))
    cfg.audio = bool(getattr(a, "audio", False))
    return cfg


def _extract_events(s, gem, m, cfg, wait: bool):
    """Event-sampled extraction. On the daily limit, keeps finished games and either stops or sleeps
    until midnight Pacific and carries on (--wait-for-reset)."""
    import time as _time

    from .vod import events
    from .vod.budget import DailyLimitReached, seconds_until_reset

    while True:
        try:
            return events.extract_events(s, gem, m, cfg)
        except DailyLimitReached as e:
            df, summaries = getattr(e, "partial", (pd.DataFrame(), []))
            log.warning("%s", e)
            if not wait:
                return df, summaries
            done = {x["game_id"] for x in summaries}
            m = m[~m["game_id"].isin(done)]
            secs = seconds_until_reset(_time.time()) + 120
            log.info("Sleeping %.1f h until the daily limit resets", secs / 3600)
            _time.sleep(secs)
            more, more_s = events.extract_events(s, gem, m, cfg)
            return pd.concat([df, more], ignore_index=True), summaries + more_s


def _print_event_summaries(summaries):
    for x in summaries:
        print(f"\n{x['game_id']}: {x.get('rounds_found')} rounds found of {x.get('rounds_expected')} expected; "
              f"{x.get('requests_sent', 0)} requests sent ({x.get('requests_total', 0)} incl. cached); "
              f"zoomed {x.get('zoomed', 0)}/{x.get('zoom_triggers', 0)} triggered rounds")
        print("  missing rounds:", ", ".join(x.get("rounds_missing", [])) or "none")
        print("  confidence:", x.get("confidence"), " gold:", x.get("gold_conf"))
        if x.get("acct_flags"):
            print("  accounting flags:", x["acct_flags"])
        for p, st in sorted(x.get("passes", {}).items()):
            print(f"  pass {p}: {st['requests']} sent, {st['cached']} cached, {st['parts']} parts, "
                  f"{st['frames']} frames, est {st['est_tokens']} tok, real prompt {st['prompt_tokens']} tok "
                  f"(video {st['video_tokens']}, audio {st['audio_tokens']}), output {st['output_tokens']}")


def _save_raw(raw_path, df):
    from .vod import pipeline as vp

    old = pd.read_csv(raw_path) if raw_path.exists() else pd.DataFrame()
    if not old.empty and len(df):
        old = old[~old["game_id"].isin(df["game_id"].unique())]
    pd.concat([old, df], ignore_index=True).pipe(vp.stage_sort).to_csv(raw_path, index=False)


def cmd_vod(a, s):
    from .vod import pipeline as vp
    from .vod.rounds import validate

    raw_path = s.vod_dir / "gemini_rounds_raw.csv"
    checked_path = s.vod_dir / "gemini_rounds_checked.csv"
    mode = getattr(a, "mode", "full")
    if a.step == "test-one":
        m = vp.load_manifest(s).head(1)
        if mode == "events":
            df, summ = _extract_events(s, _gem(s), m, _event_config(a), False)
            _print_event_summaries(summ)
            cols = ["stage", "timestamp", "gold", "gold_end", "hp", "level", "confidence", "wisp_status", "zoomed",
                    "rerolls", "acct_flags"]
            print(df[[c for c in cols if c in df]].head(15).to_string() if len(df) else "no rounds")
        else:
            df = vp.extract_economy(s, _gem(s), m, fps=a.fps)
            print(df.head(15).to_string())
        print("\nCompare these 15 rounds with the video by eye. Record the result in docs/findings.md.")
        return
    if a.step == "extract":
        m = vp.load_manifest(s)
        if a.games:
            m = m[m["game_id"].isin(a.games.split(","))]
        if mode == "events":
            df, summ = _extract_events(s, _gem(s), m, _event_config(a), getattr(a, "wait_for_reset", False))
            _print_event_summaries(summ)
        else:
            df = vp.extract_economy(s, _gem(s), m, fps=a.fps)
        if len(df):
            _save_raw(raw_path, df)
        log.info("Saved %d rounds for %d games to %s", len(df), df["game_id"].nunique() if len(df) else 0, raw_path)
        return
    if a.step == "usage":
        from .vod.budget import UsageLog
        from .vod.events import usage_report

        b = _budget(s)
        print(f"Gemini requests today (Pacific): {b.used_today()}/{b.daily_limit}")
        rep = usage_report(UsageLog(s.vod_dir / "raw" / "token_log.jsonl").read())
        print(rep.to_string(index=False) if len(rep) else "token log is empty")
        return
    if a.step == "check":
        raw = pd.read_csv(raw_path)
        checked = pd.concat([validate(g) for _, g in raw.groupby("game_id", sort=False)], ignore_index=True)
        checked.to_csv(checked_path, index=False)
        f = checked["flags"].fillna("")
        log.info("%d/%d rounds flagged (%.1f%%). Top flags: %s", (f != "").sum(), len(f), 100 * (f != "").mean(),
                 f[f != ""].str.split(";").explode().value_counts().head(6).to_dict())
        return
    if a.step == "review":
        checked = pd.read_csv(checked_path).fillna({"flags": ""})
        reviewed = vp.review_flagged(s, _gem(s), checked, vp.load_manifest(s))
        reviewed.to_csv(checked_path, index=False)
        log.info("After Gemini review: %d rounds still flagged", (reviewed["flags"].fillna("") != "").sum())
        return
    if a.step == "build":
        checked = pd.read_csv(checked_path).fillna({"flags": ""})
        rounds, games = vp.build_tables(s, checked, vp.load_manifest(s))
        log.info("vod_rounds.csv: %d rounds, vod_games.csv: %d games", len(rounds), len(games))
        hand = vp.load_hand_labels(s)
        if not hand.empty:
            acc = vp.accuracy_vs_hand_labels(rounds, hand)
            save_result(s, "vod_accuracy", acc)
            print(acc.to_string(index=False))
        return
    if a.step == "boards":
        rounds = pd.read_csv(s.processed_dir / "vod_rounds.csv")
        boards = vp.extract_boards(s, _gem(s), rounds, vp.load_manifest(s))
        (s.processed_dir / "vod_boards.json").write_text(json.dumps(boards, indent=1, default=str))
        log.info("Board states for %d key rounds", len(boards))
        return


def cmd_h4(a, s):
    from .analysis import streaks

    games = pd.read_csv(s.processed_dir / "vod_games.csv")
    rounds = pd.read_csv(s.processed_dir / "vod_rounds.csv")
    res = streaks.analyze_games(games)
    res["curves"] = streaks.gold_curves(rounds, games)
    save_result(s, "h4", res)
    for k, v in res.items():
        if k != "curves":
            print(k, v)


def cmd_sim(a, s):
    from .analysis import econ_sim

    res = econ_sim.compare_streak_rules(n_games=a.n_games)
    h4 = load_result(s, "h4")
    if h4 is not None:
        res["calibration"] = econ_sim.calibrate_to_vod(h4["curves"])
    save_result(s, "sim", res)
    print("strength SD with streak gold:", round(res["mean_strength_sd_on"], 3),
          "| without:", round(res["mean_strength_sd_off"], 3))
    print("avg placement by strategy (streak on):", res["placement_on"].round(2).to_dict())


def cmd_figures(a, s):
    from . import figures as F

    fd = s.figures_dir
    made = []
    lp = s.processed_dir / "players_labeled.parquet"
    if lp.exists():
        made.append(F.comp_graph(pd.read_parquet(lp), fd / "1_comp_graph.png"))
    for name, fn, file in [("h1", F.ranking_comparison, "2_h1_rankings.png"), ("h2", F.congestion_plot, "3_h2_congestion.png"),
                           ("h3", F.equilibrium_plot, "4_h3_equilibrium.png"), ("sim", F.sim_dispersion, "7_sim_dispersion.png")]:
        r = load_result(s, name)
        if r is not None:
            made.append(fn(r, fd / file))
    h4 = load_result(s, "h4")
    if h4 is not None:
        made.append(F.vod_curves(h4["curves"], fd / "5_vod_gold_hp_curves.png"))
        made.append(F.gold_vs_placement(pd.read_csv(s.processed_dir / "vod_games.csv"), fd / "6_gold_vs_placement.png"))
    for p in made:
        log.info("figure: %s", p)


def cmd_bundle(a, s):
    from . import bundle

    sections = {}
    meta = {}
    lp = s.processed_dir / "players_labeled.parquet"
    if lp.exists():
        df = pd.read_parquet(lp)
        meta = {"matches": df["match_id"].nunique(), "player_rows": len(df), "patches": df.groupby("patch")["match_id"].nunique().to_dict(),
                "comps_labeled": int(df["comp"].nunique()), "other_share": float((df["comp"] == "other").mean())}
    titles = {"h1": "H1 PageRank vs pick rate", "h2": "H2 Congestion", "h3": "H3 Equilibrium", "coven": "H4 Track A: Coven placement spread",
              "h4": "H4/H5 VOD streaks and economy", "sim": "Economy simulation", "vod_accuracy": "VOD extraction accuracy (vs hand labels)"}
    for k, t in titles.items():
        r = load_result(s, k)
        if r is None:
            continue
        if isinstance(r, dict):
            r = {kk: vv for kk, vv in r.items() if kk not in ("curve_on", "curve_off", "pairs")}
        sections[t] = r if isinstance(r, dict) else {"table": r}
    figs = sorted(s.figures_dir.glob("*.png"))
    path = bundle.write_bundle(s, sections, meta, figs)
    if a.gemini:
        bundle.gemini_second_opinion(s, path)
        path = bundle.write_bundle(s, sections, meta, figs, include_opinion=True)
    print(f"Bundle: {path}\nZip: {s.out_dir / 'claude_bundle.zip'}\n"
          "Upload the zip (or paste the .md) into a Claude chat and say: 'analyze this bundle'.")


def cmd_all(a, s):
    """Track A + Track B analyses from already-collected data."""
    ns = argparse.Namespace(**vars(a))
    for fn in (cmd_parse, cmd_label, cmd_h1, cmd_h2, cmd_h3, cmd_coven):
        fn(ns, s)
    if (s.vod_dir / "gemini_rounds_raw.csv").exists():
        for step in ("check", "build"):
            ns.step = step
            cmd_vod(ns, s)
        cmd_h4(ns, s)
    cmd_sim(ns, s)
    cmd_figures(ns, s)
    cmd_bundle(ns, s)


def cmd_demo(a, s):
    """Generate synthetic data with planted effects into ./demo_data and run everything on it."""
    from .demo.synthetic import make_matches, make_vods

    root = Path(a.dir).resolve()
    os.environ["TFTNET_DATA"] = str(root / "data")
    os.environ["TFTNET_OUT"] = str(root / "out")
    s = get_settings()
    if not any(s.matches_dir.glob("*.json")):
        make_matches(s.matches_dir, n_matches=a.n_matches)
    if not (s.vod_dir / "manifest.csv").exists():
        make_vods(s.vod_dir)
    a.any_queue, a.min_share, a.contention, a.export_validation, a.score_validation = False, 0.01, True, True, None
    a.gemini = False
    cmd_all(a, s)


def cmd_static(a, s):
    from . import static

    if a.step == "check":
        for name, fn in [("champions", static.champions), ("traits", static.traits), ("items", static.items), ("augments", static.augments)]:
            print(f"{name}: {len(fn())} rows")
        print("allowed-name list sizes:", {k: len(v) for k, v in static.allowed_names().items()})
    elif a.step == "cdragon":
        from .static_fetch import fetch_cdragon
        fetch_cdragon(s)


def main(argv=None):
    p = argparse.ArgumentParser(prog="tftnet", description="TFT Set 18 as a network + game")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="Riot API: seed players, match ids, matches")
    c.add_argument("step", choices=["seed", "ids", "matches", "all"])
    c.add_argument("--per-player", type=int, default=100)
    c.add_argument("--max-players", type=int)
    c.add_argument("--limit", type=int, help="max matches to fetch this run")
    c.set_defaults(fn=cmd_collect)

    c = sub.add_parser("smoke", help="live check: ~60 real matches through every Track A step (about 2 minutes)")
    c.add_argument("--players", type=int, default=15)
    c.add_argument("--matches", type=int, default=60)
    c.set_defaults(fn=cmd_smoke)

    c = sub.add_parser("parse", help="raw match JSON -> players.parquet")
    c.add_argument("--any-queue", action="store_true", help="do not filter by queue id (use to discover it)")
    c.set_defaults(fn=cmd_parse)

    c = sub.add_parser("label", help="comp labels (+ optional contention, validation sheet)")
    c.add_argument("--min-share", type=float, default=0.01)
    c.add_argument("--contention", action="store_true", help="add cost-weighted unit contention (slower)")
    c.add_argument("--export-validation", action="store_true")
    c.add_argument("--score-validation", help="path to a filled validation CSV")
    c.set_defaults(fn=cmd_label)

    for name, fn, helptext in [("h1", cmd_h1, "PageRank vs baselines"), ("h2", cmd_h2, "congestion"),
                               ("h3", cmd_h3, "equilibrium"), ("coven", cmd_coven, "Coven placement spread (API)")]:
        c = sub.add_parser(name, help=helptext)
        c.add_argument("--n-boot", type=int, default=300)
        c.add_argument("--n-perm", type=int, default=200)
        c.add_argument("--min-games", type=int, default=200)
        c.set_defaults(fn=fn)

    c = sub.add_parser("vod", help="Gemini VOD pipeline")
    c.add_argument("step", choices=["test-one", "extract", "check", "review", "build", "boards", "usage"])
    c.add_argument("--fps", type=float, default=0.25, help="full mode: frames per second")
    c.add_argument("--games", help="comma-separated game_ids to (re)extract")
    c.add_argument("--mode", choices=["full", "events"], default="full",
                   help="full: one pass per manifest clip (default until events is checked against hand labels); "
                        "events: segmentation + snapshots + zooms, about 3 requests per game")
    c.add_argument("--full", dest="mode", action="store_const", const="full", help="same as --mode full")
    c.add_argument("--seg-fps", type=float, help="events: segmentation frames per second (default 0.2)")
    c.add_argument("--snap-res", choices=["low", "medium", "high"], help="events: snapshot resolution (medium)")
    c.add_argument("--zoom-fps", type=float, help="events: zoom frames per second (default 1)")
    c.add_argument("--zoom-requests", type=int, help="events: zoom requests per game (default 1)")
    c.add_argument("--max-parts", type=int, help="events: video parts per request, passes A and C (default 10)")
    c.add_argument("--snap-parts", dest="snap_max_parts", type=int,
                   help="events: video parts in the snapshot request (default 10; ~70 gives every round two "
                        "short windows; refused counts fall back to 10)")
    c.add_argument("--audio", action="store_true", help="events: send audio too (default: frames only)")
    c.add_argument("--wait-for-reset", action="store_true",
                   help="events: at the daily limit, sleep until midnight Pacific and continue")
    c.set_defaults(fn=cmd_vod)

    c = sub.add_parser("h4", help="streaks and economy on VOD data")
    c.set_defaults(fn=cmd_h4)
    c = sub.add_parser("sim", help="economy simulation")
    c.add_argument("--n-games", type=int, default=1000)
    c.set_defaults(fn=cmd_sim)
    c = sub.add_parser("figures", help="all report figures from saved results")
    c.set_defaults(fn=cmd_figures)
    c = sub.add_parser("bundle", help="results bundle for Claude (+ --gemini second opinion)")
    c.add_argument("--gemini", action="store_true")
    c.set_defaults(fn=cmd_bundle)

    c = sub.add_parser("all", help="parse -> label -> H1-H5 -> sim -> figures -> bundle")
    c.add_argument("--any-queue", action="store_true")
    c.add_argument("--min-share", type=float, default=0.01)
    c.add_argument("--contention", action="store_true")
    c.add_argument("--export-validation", action="store_true")
    c.add_argument("--score-validation")
    c.add_argument("--n-boot", type=int, default=300)
    c.add_argument("--n-perm", type=int, default=200)
    c.add_argument("--min-games", type=int, default=200)
    c.add_argument("--n-games", type=int, default=1000)
    c.add_argument("--fps", type=float, default=0.25)
    c.add_argument("--games")
    c.add_argument("--gemini", action="store_true")
    c.set_defaults(fn=cmd_all)

    c = sub.add_parser("demo", help="run everything offline on synthetic data with planted effects")
    c.add_argument("--dir", default="demo_run")
    c.add_argument("--n-matches", type=int, default=1500)
    c.add_argument("--n-boot", type=int, default=100)
    c.add_argument("--n-perm", type=int, default=100)
    c.add_argument("--min-games", type=int, default=100)
    c.add_argument("--n-games", type=int, default=400)
    c.add_argument("--fps", type=float, default=0.25)
    c.add_argument("--games")
    c.set_defaults(fn=cmd_demo)

    c = sub.add_parser("static", help="check static data, or pull Community Dragon data")
    c.add_argument("step", choices=["check", "cdragon"])
    c.set_defaults(fn=cmd_static)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    a.fn(a, get_settings())


if __name__ == "__main__":
    main()
