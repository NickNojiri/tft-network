"""Small live check: pull a few real matches, run every Track A step, and answer docs/findings.md Q1-Q7.
Numbers from ~60 matches mean nothing statistically. This only proves the pipeline works on real data."""
from __future__ import annotations

import json
import logging
from collections import Counter

import pandas as pd

from .config import Settings

log = logging.getLogger(__name__)


def run(s: Settings, client, n_players: int = 15, n_matches: int = 60) -> str:
    from .analysis import congestion, pagerank
    from .label import label_players
    from .parse import build_players, split_train_test
    from .riot import collect

    collect.seed_players(client, s)
    collect.collect_match_ids(client, s, per_player=20, max_players=n_players)
    collect.collect_matches(client, s, limit=n_matches)

    files = sorted(s.matches_dir.glob("*.json"))
    infos = [json.loads(f.read_text())["info"] for f in files]
    lines = [f"# Live smoke test: {len(files)} matches on disk", ""]
    lines.append(f"- Q2 queue_id counts (all sets): {dict(Counter(i.get('queue_id') for i in infos))}")
    lines.append(f"- set numbers: {dict(Counter(i.get('tft_set_number') for i in infos))}")
    lines.append(f"- Q5 game_version examples: {sorted({i.get('game_version') for i in infos})[:4]}")

    df = build_players(s, queue_ids=())  # any queue, current set
    if df.empty:
        return "\n".join(lines + ["", "No Set %d matches parsed. Check TFT_SET and the lines above." % s.tft_set])
    lines.append(f"- Q1 augments field present: {bool(df['has_augments_field'].any())}")
    units = [u for x in df["units"] for u in json.loads(x)]
    traits = [t for x in df["traits"] for t in json.loads(x)]
    lines.append(f"- Q3 unit ids: {[u for u, _ in Counter(x['raw_id'] for x in units).most_common(8)]}")
    lines.append(f"- Q3 trait names: {[t for t, _ in Counter(x['raw_name'] for x in traits).most_common(8)]}")
    items = Counter(i for x in units for i in x["items"])
    lines.append(f"- Q3 item keys (normalized): {[i for i, _ in items.most_common(8)]}")
    lines.append(f"- Q4 Lux ids: {sorted({x['raw_id'] for x in units if x['id'].startswith('lux')})}")
    from .static import cost_lookup
    known = cost_lookup()
    miss = Counter(x["raw_id"] for x in units if x["id"] not in known and not x["id"].startswith("lux"))
    lines.append(f"- Q7 units not in static data ({sum(miss.values())}/{len(units)}): {dict(miss.most_common(12))}")
    lines.append("  Add each to static.ALIASES (normalized id -> static name key), or ignore if it is a summon.")

    lab = label_players(df, min_share=0.02)
    lab.to_parquet(s.processed_dir / "players_labeled.parquet", index=False)
    vc = lab["comp"].value_counts()
    lines += ["", f"## Pipeline on {df['match_id'].nunique()} Set {s.tft_set} matches",
              f"- comps found: {len(vc)}; 'other' share {100 * vc.get('other', 0) / len(lab):.0f}%; top: {vc.head(6).to_dict()}"]
    try:
        h2 = congestion.analyze(lab, n_boot=30, n_perm=20)
        sl = h2["slope_per_extra_player"]
        lines.append(f"- H2 ran: slope {sl['estimate']:+.2f} [{sl['ci_low']:+.2f}, {sl['ci_high']:+.2f}] (too few matches to trust)")
        tr, te = split_train_test(lab)
        h1 = pagerank.evaluate(tr, te, n_boot=30)
        lines.append(f"- H1 ran: PageRank rho {h1['spearman_vs_test']['pagerank']['estimate']:+.2f} (too few matches to trust)")
    except Exception as e:  # tiny samples can be degenerate; the full pull fixes this
        lines.append(f"- analysis step hit {type(e).__name__}: {e} (expected with very few matches)")
    out = s.out_dir / "smoke_report.md"
    out.write_text("\n".join(lines))
    return "\n".join(lines) + f"\n\nSaved to {out}. Copy the Q answers into docs/findings.md."
