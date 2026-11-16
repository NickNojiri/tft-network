> Commands and setup now live in ../README.md. Static data and corrections: docs/research.md addenda. This plan is kept for the reasoning behind each choice.

# plan.md: TFT as a Network + Game (CECS 427 final project)

Audience: a coding agent (Opus) building the pipeline, plus the 6-person team.
Deliverable: 2 page report + 10 min talk. Code is a means, keep it small.

## 0. Verdict

Viable. Two datasets, each covering what the other cannot:

- **Track A, Riot API (wide):** thousands of matches, final boards and placements only. Tests H1, H2, H3.
- **Track B, VODs via Gemini (deep):** 25 to 40 pro games with round-by-round gold, HP, level, streaks. Tests H4 and H5 with measured data instead of a pure simulation.

H1 to H3 remain the core of the report. H4 is now a real empirical result on a small sample. H5 is a secondary result from the same VOD data.

Corrections to the proposal (checked 2026-11-11):

- Set 19 launches Dec 1, 2026 (patch 19.1), not Nov 18. Last Set 18 patch is 18.6B (Nov 11). Still pull data this week, because the ladder resets with the new set and the Master+ player list is the seed.
- Augments have been stripped from match history since Set 13 (Nov 2024). Assume the `augments` field is missing or empty. Check the first match pulled, then drop augments from the plan if confirmed.
- Dev key: 20 requests/sec, 100 requests/2 min, expires every 24 hours. That is about 3,000 requests/hour.

## 1. How "recording" works (there is none to build)

Nobody records gameplay. Riot stores an end-of-game summary of every match and the API returns it as JSON. The pipeline just downloads those summaries.

What one match gives you, per player (8 per lobby):

- `placement` (1 to 8), `level`, `last_round`, `gold_left`, `time_eliminated`
- `players_eliminated`, `total_damage_to_players`
- `traits`: name, num_units, tier_current, style (0 inactive up to 4 prismatic)
- `units`: character_id, star tier, rarity (cost), item names
- match level: `game_version` (patch), `tft_set_number`, `queue_id`, `game_datetime`

What it does NOT give you:

- Round-by-round anything: gold, HP, streaks, who fought whom, board at stage 3
- Augments (see above)
- Positioning

Consequence: every Track A claim must be answerable from final boards + final placement. Round-level questions (streaks, economy curves) go to Track B, section 3B.

## 2. How "annotation" works (comp labeling)

The API never says "this player played comp X". It gives 8 final boards. Annotation = turning each board into a comp label. This is the step everything else depends on, so do it carefully.

Step A, rule-based labeler (build first):

1. Take active traits (style >= 1). Drop unique 1-unit traits.
2. Primary trait = highest style, tie-break by num_units.
3. Carry = the unit holding 3 items with the highest cost, tie-break by star tier.
4. Label = `{primary_trait}_{carry}`, e.g. `Coven_Unit`.
5. Labels under ~1% pick rate collapse into `other`. Target 20 to 40 comps.

Step B, sanity check by clustering (optional):

- One-hot each board over units, cluster with Jaccard distance (agglomerative or HDBSCAN). Compare clusters to rule labels. Big disagreements mean the rules are wrong.

Step C, human validation (the actual annotation task, person 2):

- Sample 100 random boards into a CSV with units, items, traits.
- Two teammates label each by hand from a fixed comp list, blind to the rule label.
- Report agreement (human vs human, human vs rules). Over ~85% is fine. This is one sentence in the report and it protects every result.

Known bias to state in the report: boards of players who died early (7th, 8th) are unfinished, so their labels are noisier.

## 3. Data collection

Endpoints (platform host `na1`, regional host `americas`):

1. `tft/league/v1/challenger`, `/grandmaster`, `/master` (queue `RANKED_TFT`) to get the player list. Confirm whether entries carry `puuid` directly; if not, resolve via summoner endpoint.
2. `tft/match/v1/matches/by-puuid/{puuid}/ids?count=...` to get match IDs.
3. `tft/match/v1/matches/{matchId}` to get the match.

Rules:

- Save the Master+ puuid list to disk TODAY. It disappears at set reset.
- Dedupe match IDs before fetching. Master+ players share lobbies, so many IDs repeat.
- Keep only ranked queue, `tft_set_number == 18`.
- Store raw JSON one file per match (`data/raw/{matchId}.json`). Never re-fetch.
- Rate limiter: respect 100 per 2 min, honor `Retry-After` on 429, resume from disk on restart. Key goes in `.env`, never in the repo. On 401/403, stop and ask for a fresh key.
- Target: 5,000 to 10,000 matches (40k to 80k player rows). Roughly 2 to 4 hours of fetching per 5k. Split across 2 or 3 teammates' keys if needed.
- Record patch per match. Analyze per patch, or patch as a control. Do not pool blindly across balance changes.

## 3B. Track B: round-by-round data from VODs (Gemini)

The Gemini API accepts a public YouTube URL directly as video input. No downloading, no frame extraction, no OCR code.

Video selection:

- 25 to 40 full, unedited ranked games from 5+ pro or high-Challenger channels, Set 18, note the patch.
- Take every game in a stream VOD in order, wins and losses. Do not use highlight uploads. This is the main defense against "only good games get uploaded".
- Log each game in `data/vods/manifest.csv`: video URL, start and end offset of the game, channel, patch, final placement.

Extraction (`vod_extract.py`):

1. One request per game: YouTube URL + the game's time range + prompt + JSON schema (structured output).
2. Prompt: "This is a Teamfight Tactics game from the streamer's view. For every round, at the start of the planning phase, report the stage-round shown at the top, the streamer's gold, HP, level, and the video timestamp. Report only values visible on the HUD. Use null if a value is not readable. Do not guess."
3. Schema, one object per round: `stage` (string like "3-2"), `gold` (int), `hp` (int), `level` (int), `timestamp` (MM:SS), plus per game `final_placement`.
4. Use the highest media resolution setting available. HUD digits are small and low-res frames cause misreads.
5. If a game returns missing or out-of-order rounds, re-request in 10 minute segments and merge.
6. Free tier allows about 8 hours of YouTube video per day, roughly 12 to 15 games. Spread over 3 days or 2 keys. Cache every raw response to `data/vods/raw/`.

Board-state fields (second pass, key rounds only):

Run a separate request per game for rounds 2-1, 3-2, 4-1, 4-5, and the last round alive. Asking for full board state on every round is too much per request and lowers accuracy.

- Tier 1, text on the HUD, expected reliable: `active_traits` (name + unit count from the trait panel), `augments` (name, read at the pick screen on augment rounds, since only icons show afterward), `wisp_active` (bool, or whatever the set mechanic indicator shows).
- Tier 2, icons and models, validate before trusting: `board_units` (champion + star level), `bench_units` (champion + star level).
- Tier 3, tiny icons, expect errors: `board_items` (items per unit), `bench_items` (unequipped components and items).

Rules for these fields:

- Give Gemini the Set 18 champion, trait, augment, and item name lists in the prompt and require answers from those lists only.
- Report accuracy per field from the hand-labeled games. Drop any field under ~85% from the analysis rather than using noisy data.
- Free ground truth for the last round: find the same match in the Riot API (streamer's puuid + game time) and compare Gemini's final board, star levels, and items against the API's. This measures Tier 2 and 3 accuracy without extra hand-labeling.
- Augments from VODs are a bonus dataset, since the API no longer returns them.
- If Tier 3 fails, fallback: Gemini returns timestamps of the key rounds and a human reads items from the paused frame.

Derived fields (computed, not read from video):

- `result`: loss if HP dropped during the round, else win. PvE and carousel rounds are excluded.
- `streak`: signed run length of consecutive results.
- `interest`: min(5, gold // 10). `econ_state`: above or below 50 gold.

Validation (required):

- Two teammates hand-label 3 full games from the video. Compare field by field.
- Target 95%+ exact match on gold and HP. Report the accuracy number in the paper.
- Automatic checks on every game: rounds sequential, HP never increases, gold change between rounds within a plausible bound. Flag failures for manual fix.
- If accuracy is under target, fall back: use Gemini only to get planning-phase timestamps, then a human reads the numbers at those timestamps (about 5 minutes per game).

Known limits to state in the report: one player's view per game, small n, pro players only, model-extracted data with measured error rate.

## 4. Repo layout

```
tft-network/
  .env.example
  src/
    collect.py      # steps 1-3 above, resumable
    parse.py        # raw JSON -> players.parquet (one row per player per match)
    label.py        # comp labeler (section 2)
    graph.py        # build comp graph, PageRank
    congestion.py   # H2
    equilibrium.py  # H3
    vod_extract.py  # Track B: Gemini extraction, cache, validation checks
    streaks.py      # H4/H5 on VOD round data
    econ_sim.py     # economy simulation, calibrated on VOD data
    stats.py        # bootstrap, permutation helpers
  notebooks/figures.ipynb
  data/raw/  data/processed/  data/vods/
  tests/test_label.py  tests/test_graph.py
```

Stack: Python, requests, pandas, networkx, numpy, scipy, matplotlib, google-genai. Nothing heavier. Check the current Gemini docs for the exact YouTube URL request shape and model name before coding, the API surface changes often.

`players.parquet` columns: match_id, patch, puuid, placement, level, last_round, gold_left, damage, comp, primary_trait, carry, n_same_comp_in_lobby, unit list, trait list.

## 5. Analyses

Split matches 70/30 by match_id into train and test before doing anything.

### H1: PageRank beats pick rate

- Nodes = comps. For each lobby, for each of the 28 player pairs with different comps, add an edge from the lower-placed comp to the higher-placed comp, weight = placement gap. Sum weights across lobbies.
- Run weighted PageRank (networkx, damping 0.85). Rank flows toward comps that beat others.
- Evaluate on TEST matches: Spearman correlation between each ranking and test average placement.
- Baselines: (a) pick rate, (b) train average placement. Baseline (b) is the honest one. PageRank is built from placements, so beating pick rate alone is nearly circular.
- Real question for the report: does PageRank add anything over raw average placement? Its theoretical edge is strength of schedule (beating strong comps counts more). If it ties, that is still a reportable result.
- Also try gap-weighted vs unweighted edges, and top-4 only edges.

### H2: congestion hurts (strongest hypothesis)

- For each player, `k` = number of players in the lobby on the same comp (1, 2, 3+).
- Regress placement on k with comp fixed effects and patch fixed effects. The fixed effects matter: strong comps are both popular and contested.
- Plot mean placement by k for the top 8 comps with bootstrap CIs.
- Permutation test: shuffle comp labels within lobbies' comp pool to confirm the slope is not an artifact.
- Finer version if time: contest the carry unit specifically instead of the whole comp, since the shared pool is per champion.

### H3: equilibrium

- Scatter pick rate vs average placement per comp, per patch, with bootstrap CIs.
- Trap: high pick rate means more samples, so averages look closer to 4.5 from sample size alone. CIs handle this. Do not claim convergence from the raw scatter.
- Better test, dynamic: for comps present in consecutive patches, does over-performance in patch N predict a pick rate increase in patch N+1 and a move toward 4.5? That is players responding to payoffs, which is the equilibrium story.
- Combine with H2: congestion is the mechanism that pushes popular comps back to average.

### H4: streaks (Track B, measured)

- Per game, compute longest win streak and longest loss streak in stages 2 and 3, and the gold and HP curves.
- Rich-get-richer test: does gold at 3-2 and 4-1 predict final placement? Does a stage 2 win streak predict a stage 4 win rate above the player's baseline?
- Balancing test: do loss-streakers at stage 2 to 3 recover (higher gold at 4-1, comparable placement), or does the HP cost outweigh the gold? Plot HP at 4-1 vs gold at 4-1, colored by placement.
- Group games into win-streak, loss-streak, and mixed openers. Compare placement distributions. With ~30 games use rank-based tests and bootstrap CIs, and call it a case study.
- Track A support: placement distribution of loss-streak trait boards vs others. A bimodal shape (many 1sts and 8ths) supports a high-variance strategy.
- Simulation: 8 players with base income, interest (1 per 10g, cap 5), win gold, streak gold. Calibrate it against the measured gold curves, then toggle streak bonuses off to show what they do to gold dispersion.

### H5: economy (Track B, secondary)

- From gold curves: rounds spent at 50+ gold, round when the player first drops below 50 (the roll-down), total interest earned.
- Does more interest earned predict better placement, or is there a sweet spot (greedy players bleed too much HP)?
- Track A check: placement for boards with econ traits active vs not. Weak alone, because players often drop the econ trait before the final board.

## 6. Statistics rules

- Bootstrap by MATCH, not by player. The 8 rows in a lobby are dependent (placements sum to 36).
- Report effect sizes with 95% CIs, not just p-values.
- Minimum 200 games per comp to appear in any chart.
- Everything in the report comes from the test split or is flagged as exploratory.

## 7. Build order for the agent

1. `collect.py` with limiter, resume, dedupe. Pull 50 matches. Print one full match JSON and confirm the field list in section 1, including whether augments exist.
2. `parse.py` to parquet. Assert 8 players per match, placements are 1 to 8.
3. `label.py` + tests. Export the 100-board validation CSV for humans.
4. Kick off the full pull in the background (hours).
5. `graph.py` + H1 evaluation.
6. `congestion.py`, `equilibrium.py`.
7. Figures: (1) comp graph with node size = PageRank, (2) ranking comparison bar chart, (3) placement vs k, (4) pick rate vs placement with CIs.
8. Track B, can run in parallel from day 1: `vod_extract.py` on ONE game. Compare 10 rounds against the video by eye. Only then batch the manifest.
9. `streaks.py` (H4, H5), then `econ_sim.py` calibrated on the VOD curves.
10. Figure (5): gold and HP curves by opener type. Figure (6): gold at 4-1 vs placement.

Stop and report after step 1, step 3, and the one-game test in step 8 before continuing.

## 8. Team split (adjusted)

1. Collection (runs the pull this week)
2. Parsing + comp labeling + human validation set
3. Graph + PageRank (H1)
4. Congestion (H2) + equilibrium across patches (H3)
5. VOD track: manifest, Gemini extraction, hand-label validation, H4 and H5
6. Report + slides, owns figures and the limitations paragraph, helps hand-label

## 8B. Optional depth upgrades (not approved yet, pick at most two)

- Bipartite player-champion graph per lobby for a continuous contention score (upgrades H2).
- Bradley-Terry and Elo as ranking baselines next to PageRank (upgrades H1).
- One graph per patch, test best-response migration toward last patch's overperformers (upgrades H3).
- Unit co-occurrence network + community detection to validate comp labels.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Comp labels are wrong | Human validation set, clustering cross-check |
| PageRank adds nothing over avg placement | Report it honestly, lean on H2 and H3 |
| Ladder reset wipes the seed list | Save puuids today |
| Patch changes mix metas | Per-patch analysis |
| Key expires mid-pull | Resumable collector, raw JSON cache |
| Gemini misreads HUD numbers | High media resolution, 3-game hand-label check, automatic consistency checks, human fallback |
| VODs skew toward good games | Full stream VODs in order, no highlights, state the bias |
| VOD sample too small | Case-study framing, bootstrap CIs, rank-based tests |
| Too much scope for 2 pages | H1 to H3 get one page. H4 and H5 share half a page and two figures |

## 10. Riot policy note

Dev key is fine for a class project. Do not publish a public stats site off it, and do not commit the key.
