"""Pull Set 18 data from Community Dragon (run on a machine with normal internet access).

Main use: the full Wisp list, which Mobalytics only renders in a browser. CDragon's JSON layout is
built by the community and can change; this saves the raw file and writes best-effort CSVs.
"""
from __future__ import annotations

import json
import logging

import pandas as pd
import requests

from .config import Settings

log = logging.getLogger(__name__)
URL = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"


def fetch_cdragon(s: Settings, set_number: int = 18) -> None:
    raw_path = s.static_dir / "cdragon_en_us.json"
    data = requests.get(URL, timeout=120).json()
    raw_path.write_text(json.dumps(data))
    log.info("Saved raw CDragon file (%d MB)", raw_path.stat().st_size // 1_000_000)

    items = pd.DataFrame(data.get("items", []))
    if not items.empty:
        api = items.get("apiName", pd.Series(dtype=str)).fillna("")
        wisp = items[api.str.contains("Wisp|Charm", case=False) | items.get("name", "").astype(str).str.contains("Wisp", case=False)]
        cols = [c for c in ["apiName", "name", "desc", "effects", "icon"] if c in wisp.columns]
        wisp[cols].to_csv(s.static_dir / "wisps_cdragon_candidates.csv", index=False)
        log.info("%d Wisp candidates written. Check apiName patterns by hand; adjust the filter if needed.", len(wisp))

    for st in data.get("setData", data.get("sets", [])):
        if str(st.get("number")) == str(set_number) or str(set_number) in str(st.get("mutator", "")):
            champs = pd.DataFrame(st.get("champions", []))
            champs.to_json(s.static_dir / "cdragon_champions.json", orient="records")
            pd.DataFrame(st.get("traits", [])).to_json(s.static_dir / "cdragon_traits.json", orient="records")
            log.info("Set %s: %d champions, %d traits", set_number, len(champs), len(st.get("traits", [])))
            break
    else:
        log.warning("Set %s not found in setData; inspect %s", set_number, raw_path)
