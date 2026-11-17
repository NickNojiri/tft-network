# VOD protocol (Track B)

## Picking videos (about 20 minutes, one person)
- Public YouTube only. Full, unedited stream VODs or full-game uploads. No highlight videos.
- Take every game in a VOD in order, wins and losses. This is the main defense against "only good games get uploaded".
- Native HUD visible. Skip channels whose overlay covers the HP list, trait panel or gold.
- 1080p, patch 18.5 or later, NA ranked, at least 5 different players.
- Note the streamer's Riot ID (Name#TAG) so we can match the game in the Riot API for free ground truth.

## Manifest (`data/vods/manifest.csv`, copy from `manifest.example.csv`)
One row per game: `game_id` (channel_date_gN), `video_url`, `start_s` and `end_s` (seconds where the game
starts at 1-1 and ends at the placement screen), `channel`, `streamer_name` (in-game name shown in the HP
list), `streamer_riot_id`, `patch`, `final_placement` (read it yourself, takes 2 seconds), `notes`.

Target: 30 games. Free tier allows about 8 hours of video per day, so about 12-15 games per day per key.

## Hand labels (two people, 3 games each, overlapping on 1)
Copy `data/vods/hand_labels/TEMPLATE_game_id.csv`, rename to the game id, and record stage, gold, HP and
level at the start of every planning phase. Skip carousels. The overlap game gives human-vs-human
agreement; all labeled games give Gemini's accuracy (`vod build` prints it).

## Order of operations
1. `vod test-one`, check by eye, fill docs/findings.md G1-G8.
2. Fill the manifest, `vod extract`.
3. `vod check` then `vod review` (Gemini re-reads flagged rounds).
4. Hand-label 3-5 games, `vod build`. Report the accuracy table in the paper.
5. Optional: `vod boards`. Compare last-round boards against the Riot API for unit and item accuracy.

## Event sampling (`--mode events`, for the free tier)
The free tier allows 20 requests a day, 5 a minute and 250K input tokens a minute. `vod extract --mode events`
spends about 3 requests per game instead of one per 10-minute clip:

| Pass | What it reads | Sampling | Requests |
|---|---|---|---|
| A segmentation | stage-round, planning and combat start times | whole game, low res, 1 frame / 5 s, 10-minute labelled parts | 1 |
| B snapshots | gold, HP, level, XP, Wisp slot, augment | 3 frames at planning start + 3 before combat, high res, all rounds packed into 10 parts of one request | 1 |
| C zooms | spending: XP buys, units bought and sold, rerolls, Wisp bought | full planning phase, high res, 1 fps, up to 10 rounds | 1 |

Pass C goes to rounds where a number was unreadable, a check failed, gold dropped by 10 or more, a Wisp was
offered, or a Wisp was expected but the shop was hidden. Wisps sit in every other shop and a reroll counts as a
shop, so the expected pattern is recomputed from each sighting.

Audio is not sent: multi-part requests go through the Interactions API with `processing.content_layers=["frames"]`
and a resolution per video part. In the first live run, generate_content rejected `content_layers` with a 400, and
its global `media_resolution` did not change tokens per frame. If a mode is rejected, the next one is tried
(Interactions with audio, then generate_content with audio). The mode that works is saved in
`data/vods/raw/gemini_caps.json`. Measured on gemini-3.6-flash, frames only: 66 video tokens per frame at low, 44 at
medium and 253 at high. `--snap-parts 70` gives every round its own two short snapshot windows. The API accepted 63
parts, but the answer degenerated (10 of 33 rounds). So an answer covering under half the rounds, or a refused part
count, re-plans with 10.

Snapshots use one flat reading per round: Gemini takes the value most frames show and says whether the start frames
agreed. A per-frame list schema came back near-empty through Interactions twice. Each round gets `gold_conf`,
`hp_conf`, `level_conf` and an overall `confidence`: `exact` (the frames agreed, or the high-res zoom read it),
`inferred` (frames disagreed or agreement unknown) or `missing`. Missing numbers
stay empty, never 0. Zoomed rounds get gold accounting: rerolls = (gold spent + gold from sales - 4 per XP buy -
unit costs - Wisp cost) / 2, flagged `acct_odd`, `acct_negative` or `gold_mismatch` (more than 2 gold off
Gemini's own reroll count).

The daily counter is in `data/vods/raw/gemini_budget.json` and stops at 19 requests (`GEMINI_DAILY_LIMIT`). When
it stops, rerun the same command the next day: finished passes are cached, or pass `--wait-for-reset`.
`vod usage` prints requests and real token counts per game and pass from `data/vods/raw/token_log.jsonl`.

Keep `--mode full` (the default) until events mode matches the hand labels at least as well.
