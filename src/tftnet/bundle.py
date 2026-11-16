"""Package all results into one markdown file (+ zip of CSVs/figures) to hand to Claude for analysis,
and optionally ask Gemini for an independent read of the same numbers first.

Output: out/claude_bundle.md, out/claude_bundle.zip, and (optional) out/gemini_second_opinion.md
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Settings

CLAUDE_PROMPT = """You are helping a 6-person CECS 427 (Dynamic Networks) team finish a TFT Set 18 project.
Below are the computed results. Please:
1. For each hypothesis H1-H5, say whether the evidence supports, contradicts, or is inconclusive, citing the
   specific numbers and confidence intervals. Flag anything that looks like an artifact (sample size,
   circularity, label noise, selection bias in VODs).
2. Point out the 2-3 most interesting findings for a 10-minute talk.
3. List any extra analysis worth running with the data we already have, and which file it would use.
4. If a Gemini second opinion is included at the end, say where you agree or disagree with it and why.
Keep it tight: this feeds a 2-page report."""


def _fmt(obj, depth=0) -> str:
    if isinstance(obj, pd.DataFrame):
        return obj.round(4).to_markdown() if len(obj) <= 40 else obj.head(40).round(4).to_markdown() + f"\n\n({len(obj)} rows, first 40 shown)"
    if isinstance(obj, pd.Series):
        return _fmt(obj.to_frame(), depth)
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            if isinstance(v, (pd.DataFrame, pd.Series)):
                lines.append(f"\n**{k}**\n\n{_fmt(v)}\n")
            elif isinstance(v, dict) and depth < 2:
                lines.append(f"- **{k}**:\n" + "\n".join("  " + x for x in _fmt(v, depth + 1).splitlines()))
            else:
                lines.append(f"- **{k}**: {_num(v)}")
        return "\n".join(lines)
    return str(_num(obj))


def _num(v):
    if isinstance(v, (float, np.floating)):
        return f"{v:.4f}"
    return v


def write_bundle(s: Settings, sections: dict[str, dict], meta: dict, figures: list[Path],
                 include_opinion: bool = False) -> Path:
    md = [f"# TFT Set 18 network project: results bundle",
          f"Generated {datetime.now().isoformat(timespec='minutes')}", "",
          "## Instructions for Claude", CLAUDE_PROMPT, "", "## Dataset", _fmt(meta), ""]
    for title, content in sections.items():
        md += [f"## {title}", _fmt(content), ""]
    opinion = s.out_dir / "gemini_second_opinion.md"
    if include_opinion and opinion.exists():
        md += ["## Gemini second opinion (independent read of the same numbers)", opinion.read_text(), ""]
    md += ["## Files in the zip", *[f"- {p.name}" for p in figures],
           "- players_labeled.parquet, vod_rounds.csv, vod_games.csv, comp_rankings.csv (when present)"]
    path = s.out_dir / "claude_bundle.md"
    path.write_text("\n".join(md))

    zpath = s.out_dir / "claude_bundle.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(path, path.name)
        for f in figures:
            if f.exists():
                z.write(f, f"figures/{f.name}")
        for name in ["vod_rounds.csv", "vod_games.csv", "comp_rankings.csv", "comp_patch_table.csv"]:
            p = s.processed_dir / name
            if p.exists():
                z.write(p, name)
        if include_opinion and opinion.exists():
            z.write(opinion, opinion.name)
    return path


def gemini_second_opinion(s: Settings, bundle_md: Path) -> Path:
    """Gemini reads the same numbers independently. The opinion is appended to the bundle so Claude can
    compare (call write_bundle again with include_opinion=True). Two models disagreeing on a conclusion is a signal to look closer."""
    from .vod.gemini import GeminiVideo

    gem = GeminiVideo(s.gemini_api_key, s.gemini_model, resolution="")
    text = bundle_md.read_text().replace(CLAUDE_PROMPT,
        "Give an independent statistical read of these results for H1-H5: supported, contradicted, or "
        "inconclusive, with the numbers that decide it, and the biggest threat to validity for each.")
    opinion = gem.ask_text(text)
    out = s.out_dir / "gemini_second_opinion.md"
    out.write_text(opinion)
    return out
