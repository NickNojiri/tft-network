# TFT as a Network + Game (CECS 427, Set 18 Enchanted Wilds)

Same economy rules, one shared champion pool: what separates winners? We model Set 18 as a network and
test it on real data from two sources:

- **Track A, Riot API (wide):** thousands of Master+ NA matches, final boards and placements. Tests H1-H3.
- **Track B, pro VODs via Gemini (deep):** 25-40 games, round-by-round gold, HP, level, streaks. Tests H4-H5.

| | Hypothesis | Command |
|---|---|---|
| H1 | PageRank on a who-beat-whom comp graph predicts held-out placement better than pick rate | `h1` |
| H2 | More players on the same comp in a lobby = worse placement (shared pool) | `h2` |
| H3 | Popular comps drift toward 4.5 avg placement; players best-respond across patches | `h3` |
| H4 | Win streaks snowball, or loss-streak gold balances the lobby | `coven`, `vod`, `h4`, `sim` |
| H5 | Economy (interest, roll-down timing) predicts placement | `h4`, `sim` |

## Setup (10 minutes)

```bash
git clone <this repo> && cd tft-network
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[gemini,dev]"
cp .env.example .env                                      # then paste your keys into .env
python -m pytest -q                                       # 15 tests, ~10 s
python -m tftnet demo                                     # full pipeline on synthetic data, ~35 s
python -m tftnet smoke                                    # needs RIOT_API_KEY: ~60 real matches, 2 min
```

The demo writes to `demo_run/`. Open `demo_run/out/claude_bundle.md` and `demo_run/out/figures/` to see
what the real outputs will look like. The synthetic data has known effects planted (congestion, comp
strength, best-response), and the tests check the analyses recover them.

Keys:
- **Riot**: developer.riotgames.com, sign in, copy the Development API Key. It expires every 24 hours.
  Everything is cached, so when it expires: new key into `.env`, rerun the same command.
- **Gemini**: aistudio.google.com, Get API key. Free tier allows about 8 hours of YouTube video per day.

Presenting it? See `DEMO.md`.

## Run it for real

### Track A: Riot data (do the seed step before Dec 1, when Set 19 resets the ladder)

```bash
python -m tftnet collect seed                 # Master+ NA puuids -> data/raw/seed_latest.json
python -m tftnet collect ids                  # match ids since Set 18 launch
python -m tftnet collect matches --limit 50   # first 50 matches, then STOP and check docs/findings.md Q1-Q6
python -m tftnet parse --any-queue            # prints queue ids and patches seen. Set TFT_QUEUE_IDS in .env
python -m tftnet collect matches              # the rest (~3,000 requests/hour on a dev key)
python -m tftnet parse
python -m tftnet label --export-validation --contention
python -m tftnet h1 && python -m tftnet h2 && python -m tftnet h3 && python -m tftnet coven
```

Hand-label check: two teammates fill `label_a` / `label_b` in `out/label_validation_sample.csv`, then
`python -m tftnet label --score-validation out/label_validation_sample.csv`.

### Track B: VODs with Gemini

See `docs/vod_protocol.md` for picking videos and filling `data/vods/manifest.csv`.

```bash
python -m tftnet vod test-one        # ONE game. Compare 15 rounds to the video by eye before going further
python -m tftnet vod extract         # economy table for every game in the manifest (cached per game)
python -m tftnet vod check           # consistency checks: HP never rises, stages in order, gold jumps...
python -m tftnet vod review          # Gemini re-reads each flagged round from a short high-fps clip
python -m tftnet vod build           # derived streaks/interest + accuracy vs your hand labels
python -m tftnet vod boards          # optional: board state at 2-1, 3-2, 4-1, 4-5 and last round
python -m tftnet h4
```

### Wrap up

```bash
python -m tftnet sim                 # economy simulation, calibrated to the VOD gold curves if present
python -m tftnet figures             # out/figures/*.png
python -m tftnet bundle --gemini     # out/claude_bundle.md + .zip, with Gemini's independent read
```

`python -m tftnet all` reruns parse through bundle from cached data.

### Handing results to Claude

Upload `out/claude_bundle.zip` (or paste `claude_bundle.md`) into a Claude chat and say "analyze this
bundle". The file opens with instructions: verdict per hypothesis with the numbers, artifacts to worry
about, the best findings for the talk, and where Claude agrees or disagrees with Gemini's read.

## How Gemini is used (three places)

1. **Extraction**: reads gold, HP, level, augment picks per round from YouTube links (no downloads).
2. **Self-review**: every round that fails a consistency check is re-sent as a 35-second clip at
   1 frame/sec, and Gemini reads it again. Pass-1 values are kept (`*_pass1` columns) so changes are auditable.
3. **Second opinion**: `bundle --gemini` has Gemini interpret the same result numbers independently.
   Two models disagreeing on a conclusion is a signal to look closer.

## Repo map

```
src/tftnet/
  riot/client.py, collect.py   rate-limited Riot API client, resumable collector
  parse.py                     match JSON -> players.parquet, train/test split
  label.py                     comp labeler + hand-validation sheet
  analysis/pagerank.py         H1: PageRank, Bradley-Terry, baselines
  analysis/congestion.py       H2: fixed-effects slope, permutation test, unit contention
  analysis/equilibrium.py      H3: pick rate vs placement, cross-patch dynamics
  analysis/streaks.py          H4/H5 on VOD data, Coven spread on API data
  analysis/econ_sim.py         economy simulation, streak gold on vs off
  vod/                         Gemini schemas, prompts, client, checks, pipeline
  figures.py, bundle.py, cli.py
data/static/                   Set 18 champions, abilities, traits, items, augments (Mobalytics)
docs/                          plan, research, findings (fill in), VOD protocol, team plan, report outline
```

## Known gaps

- `data/static/wisps_partial.csv` has 29 of ~200 Wisps. Run `python -m tftnet static cdragon` on a normal
  internet connection to pull the full list from Community Dragon.
- Not yet run against live APIs: ranked queue id, `game_version` to 18.x mapping, Riot unit id format,
  and Gemini accuracy on TFT footage. `docs/findings.md` lists each check.
