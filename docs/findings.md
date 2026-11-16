# Findings log

Fill these in as you go. Code comments marked UNVERIFIED point here.

## Riot data (after the first 50 matches)

| # | Question | Expected | Answer | Where to change code |
|---|---|---|---|---|
| Q1 | Does a Set 18 participant have an `augments` field? | No (removed since Set 13) | | `parse.py` |
| Q2 | Ranked `queue_id` (`parse --any-queue` prints counts) | 1100 | | `.env` `TFT_QUEUE_IDS` |
| Q3 | Format of `character_id`, trait `name`, `itemNames` | `TFT18_Ahri`, `TFT18_Blossom`, `DA_...` | | `static.ALIASES` |
| Q4 | How do Lux variants and summons (trees, Tinybeaks, plants) appear in `units`? | | | `static.is_lux`, `label.py` |
| Q5 | `game_version` string and which value maps to 18.1 ... 18.6B | | | `parse.patch_of` |
| Q6 | Max `count` accepted by match-ids endpoint | 200? | | `collect --per-player` |
| Q7 | Units in matches that do not join to `data/static/champions.csv` | none | | `static.ALIASES` |

Quick check for Q3 and Q7:

```python
import pandas as pd, json
df = pd.read_parquet("data/processed/players.parquet")
units = pd.Series([u["raw_id"] for x in df["units"] for u in json.loads(x)])
print(units.value_counts().head(40))
print(df["units"].map(lambda x: [u["raw_id"] for u in json.loads(x) if u["cost"] is None]).explode().value_counts())
```

## Gemini (after `vod test-one`)

| # | Question | Answer |
|---|---|---|
| G1 | Which API path worked (`interactions` or `generate_content`)? See `api` in `data/vods/raw/*.json` | |
| G2 | Did structured output work with a video in the same call? | |
| G3 | Are returned timestamps relative to the clip or the full video? (code auto-detects) | |
| G4 | Gold / HP / level correct on 15 rounds checked by eye | __ / 15, __ / 15, __ / 15 |
| G5 | Where does a bought Wisp show on the HUD? | |
| G6 | Is x-7 a monster round and x-4 a carousel in Set 18? (`vod/rounds.py`) | |
| G7 | HUD layout matches `vod/prompts.HUD_LAYOUT`? | |
| G8 | Tokens/cost per game at fps 0.25 high resolution | |

Go / no-go for Track B: G4 at 14/15 or better on gold and HP. If not, use Gemini only for timestamps and
have people read the numbers (about 5 minutes per game).
