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
