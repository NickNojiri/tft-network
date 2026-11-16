# research.md: pre-research for the TFT network project

Companion to `plan.md`. Researched 2026-11-11. Every fact is tagged:

- **[V]** verified against a source this session (links at bottom)
- **[U]** unverified: from general knowledge or inferred. Confirm in code before relying on it.

Where this file and `plan.md` disagree, this file wins (it is newer and more specific).

---

## 1. Timeline

- [V] Set 18 "Enchanted Wilds" went live Aug 26, 2026 with patch 18.1.
- [V] Set 18 patches: 18.1 Aug 26, 18.2 Sep 10, 18.3 Sep 23, 18.4 Oct 7, 18.5 Oct 21, 18.6 Nov 4, 18.6B Nov 11.
- [V] Set 19 launches Dec 1, 2026 with patch 19.1.
- Consequence: about 3 weeks of Set 18 ladder left. Pull the Master+ puuid list now and re-pull it once more before Dec 1.
- [U] `game_version` string format in match JSON. Print it from the first match and build the patch parser from what you see.

## 2. Riot API

### Keys and limits
- [V] Dev key: 20 requests/sec and 100 requests/2 min. Expires every 24 hours.
- [V] Rate limit headers on every response: `X-App-Rate-Limit`, `X-App-Rate-Limit-Count`, `X-Method-Rate-Limit`, `X-Method-Rate-Limit-Count`. `Retry-After` on 429.
- [V] Auth header: `X-Riot-Token: <key>`.
- [V] Riot lists "school projects" as a fit for a personal key (does not expire). Applying is optional. The daily dev key works if someone regenerates it.
- [V] Policy: do not embed keys in code. Share keys only with teammates.

### Routing
- [V] League endpoints use platform host: `https://na1.api.riotgames.com`
- [V] Match endpoints use regional host: `https://americas.api.riotgames.com` (AMERICAS serves NA, BR, LAN, LAS).

### tft-league-v1 (seed list)
- [V] `GET /tft/league/v1/challenger`, `/grandmaster`, `/master`. Query `queue`, default `RANKED_TFT`.
- [V] Response `LeagueListDTO`: `leagueId`, `tier`, `name`, `queue`, `entries[]`.
- [V] Each entry (`LeagueItemDTO`) includes `puuid` directly, plus `leaguePoints`, `wins`, `losses`, `rank`, `hotStreak`, `veteran`, `freshBlood`, `inactive`. No summoner lookup step needed.
- [V] Lower tiers if more players are needed: `GET /tft/league/v1/entries/{tier}/{division}?page=N` (DIAMOND and below).

### tft-match-v1
- [V] `GET /tft/match/v1/matches/by-puuid/{puuid}/ids` with query `start` (default 0), `count` (default 20), `startTime`, `endTime` (epoch seconds).
- Use `startTime` = Aug 26, 2026 epoch to skip Set 17 matches at the source.
- [U] Max `count` per call. Try 200, fall back to paging with `start`.
- [V] `GET /tft/match/v1/matches/{matchId}` returns `MatchDto { metadata, info }`.

`info` fields [V]:
- `game_datetime` (unix), `game_length` (seconds), `game_version`, `queue_id`, `tft_set_number`, `tft_set_core_name`, `tft_game_type`, `endOfGameResult`, `participants[]`
- `queueId` is deprecated, use `queue_id`.

`participants[]` fields [V]:
- `puuid`, `riotIdGameName`, `riotIdTagline`
- `placement`, `win` (bool), `level`, `last_round`, `gold_left`, `time_eliminated`, `players_eliminated`, `total_damage_to_players`
- `traits[]`: `name`, `num_units`, `style` (0 none, 1 bronze, 2 silver, 3 gold, 4 chromatic), `tier_current`, `tier_total`
- `units[]`: `character_id`, `tier` (star level), `rarity`, `itemNames[]`, `items[]`, `name` (often blank)
- `companion` (ignore)

Gotchas:
- [V] `last_round` is a running round count, not stage-round. 2-1 maps to 5.
- [V] `rarity` does not equal unit cost. Join cost from static data by `character_id` instead.
- [V] `level` is tactician level, not unit count.
- [V] The documented DTO has no `augments` field. Riot removed augments from match history starting with Set 13. Confirm on the first pulled match, then treat augments as VOD-only data.
- [U] Ranked queue is `queue_id == 1100`. Confirm by counting `queue_id` values in the first 50 matches pulled from Master+ players.
- [U] `character_id` and trait names use a `TFT18_` prefix. Confirm from data.
- [U] `units[]` may include summoned or non-champion entries (see section 3). Filter to ids that exist in the champion table.
- [V] No round-by-round data exists in this API.

### Static data (names, costs, traits)
- [V] Community Dragon publishes a combined TFT JSON built from game files, pattern `https://raw.communitydragon.org/<patch>/cdragon/tft/en_us.json` (`pbe` and `latest` are valid patch values). Contains sets, champions (`apiName`, `cost`, `traits`), traits, items.
- [V] Official Data Dragon ships per-patch TFT files: `tft-champion.json`, `tft-trait.json`, `tft-item.json`, `tft-augments.json`, `tft-queues.json`. Version list at `ddragon.leagueoflegends.com/api/versions.json`.
- Use CDragon as the primary source, DDragon `tft-queues.json` to confirm the ranked queue id.
- Download once, commit to `data/static/`, and generate the name lists for the Gemini prompt from it.

## 3. Set 18 game facts

### Roster and pool (drives H2)
- [V] 65 champions, 35 traits (13 origins, 12 classes, 10 single-champion traits).
- [V] Shared pool per champion: 1-cost 30 copies (14 champs), 2-cost 25 (13), 3-cost 18 (14), 4-cost 10 (14), 5-cost 9 (10). Total 1,227 copies.
- Contention math: a 3-star needs 9 copies. Two players 3-starring the same 4-cost is impossible (18 needed, 10 exist). Three players 2-starring the same 4-cost uses 9 of 10. This is why contention should be weighted by cost.
- [V] Shop odds, level 8: 15/20/32/30/3. Level 9: 10/17/25/33/15. Level 3: 75/25. Level 4: 55/30/15. Full table at the seemeta odds page in Sources.

### Economy rules (drives H4, H5, econ_sim)
- [V] Passive income 5 gold per planning phase. Early ramp: 2, 2, 3, 4 gold at rounds 1-2, 1-3, 1-4, 2-1.
- [V] Interest: +1 per 10 gold held, cap 5, calculated before passive income.
- [V] PvP win: +1 gold.
- [V] Streak gold (same for win and loss streaks): streak of 3 to 4 gives +1, 5 gives +2, 6+ gives +3. Changed from 2-3/4/5+ in patch 14.1. Many older guides show the old numbers.
- [V] Max standard income per round: 5 + 5 + 3 + 1 = 14.
- [U] Player damage formula and starting HP (100) for the current set. Not needed unless the sim models HP. Look up before coding it.

### Wisps (set mechanic)
- [V] Single-use buyable effects, a rework of Set 12 Charms. About 200 of them.
- [V] Appear in the rightmost shop slot, in every other shop. One purchase per round unless Blossom changes that.
- [V] Categories: Champion, Combat, Gold/XP, Item, Misc, Risky, Shop.
- [V] Get stronger and more expensive by stage. From stage 5, every other Wisp offered is a Combat Wisp.
- [U] How a purchased Wisp shows on the HUD afterward. Check one VOD by eye before writing the Gemini prompt for `wisp_active`. More reliable field: "which Wisp is offered in the shop and was it bought", since the shop slot shows the name.
- Wisp purchases are a gold sink the economy model must account for. A gold drop between rounds is not always a roll-down.

### Traits that matter for the hypotheses
- [V] **Coven** (the "lose to win" trait): gain Essence from kills and from losing player combat, cash out for rewards at thresholds. Breakpoints 3/4/5/7. Essence at (3): 2 per kill, 18 per loss. (5): 3 and 32. (7): 10 and 60. Sources disagree on the (4) values. Champions: Camille 1, Caitlyn 2, Elise 2, Cassiopeia 3, Morgana 4, plus Lux.
- [V] **Blossom** (Wisp trait): 3 upgrades Wisps, 5 puts Wisps in every shop, 7 gives gold on Wisp purchase, 9 allows 2 Wisps per round. Champions: Karma 1, Yorick 1, Yunara 2, Master Yi 3, Ahri 4, Sett 4, Ashe 5, plus Lux.
- [V] **Fae**: at (4), after 7 Pixies you attract Golden Pixies that grant gold. Champions: Rakan 1, Xayah 1, Tristana 3, Lillia 4, plus Lux.
- [V] Other economy-adjacent traits: Inferno (upgrades shop slots), Bounty Seeker (Draven, rewards), Riftbeast 5 (special shops).
- Trait values above came from pre-launch and launch-era pages. Numbers may have been patched. Trait names and structure are stable.

### Comp labeling traps
- [V] **Lux (Avatar)**: one 5-cost that comes in 9 origin variants (Blossom, Coven, Elderwood, Eldritch, Fae, Inferno, Lunar, Primal, Solar) and counts twice for her origin. She appears in many comps. Never use Lux as the carry in a comp label, and check how her variants appear in `character_id`.
- [V] **Elder Dragon** takes 2 team slots and adds +2 Riftbeast.
- [V] **Riftbeast** units are monsters (Gromp, Krug, Raptor, etc.), not League champions. They are real roster units.
- [V] Summon traits create extra board units: Elderwood (trees, plants), Sprykin (Big Furry Friend), Summoner class, Zyra plants. [U] whether these show up in `units[]`.
- [V] 10 single-champion traits will always show as active. Exclude traits with only one possible unit when picking the primary trait.

## 4. Gemini video API

Google's API surface changed in 2026. Older tutorials using `generate_content` and `file_data` still exist but the current docs use the Interactions API. Check the live docs page before coding.

### Request shape [V]
```python
from google import genai
client = genai.Client()  # reads GEMINI_API_KEY

interaction = client.interactions.create(
    model="gemini-3.8-flash",   # model used in current docs examples
    input=[
        {"type": "text", "text": PROMPT},
        {"type": "video", "uri": "https://www.youtube.com/watch?v=VIDEO_ID"},
    ],
    response_format={
        "type": "text",
        "mime_type": "application/json",
        "schema": RoundTable.model_json_schema(),  # Pydantic
    },
)
data = RoundTable.model_validate_json(interaction.output_text)
```
- [V] Structured output on the Interactions API uses `response_format` with `type`, `mime_type`, `schema`. The Python SDK accepts a Pydantic JSON schema.
- [U] Structured output combined with video input in one call. The docs show each separately. Test on one clip first. Fallback: ask for JSON in the prompt and validate with Pydantic.

### Video controls [V]
- Clipping: add `"processing": {"type": "static", "start_offset": ..., "end_offset": ...}` on the video part. Docs example uses 1200 and 1500. [U] unit is not stated, almost certainly seconds. Verify by clipping a known segment.
- Frame rate: `"fps"` inside the same `processing` object. Default 1 frame/sec. Example 0.5 = one frame every 2 seconds.
- Resolution: `media_resolution` controls tokens per frame. For video on Gemini 3 and later: default, low, and medium are all 70 tokens/frame. **high is 280 tokens/frame and is the setting Google recommends for reading text or small details in video.** Use high.
- [U] Exact field name and placement for per-item resolution in the Python SDK (docs show a per-content-item `resolution` field in other SDKs). Copy from the media resolution docs page.
- Agentic mode: `"processing": "agentic"` lets the model explore the timeline and pick its own frame rate and resolution. Clipping is not supported in agentic mode. Worth testing as an alternative on full-game clips, verify with `interaction.steps`.
- Timestamps in prompts and answers use `MM:SS`.

### Limits and cost [V]
- YouTube URL input is a preview feature, currently free. Free tier: 8 hours of YouTube video per day. Public videos only. Up to 10 videos per request on current models.
- Context: a 1M-token model holds about 3 hours of video at default resolution, about 1 hour at high. A full stream VOD will not fit. Always clip to one game.
- Token math at high resolution, 1 fps: about 280 + 25 audio = ~305 tokens/sec. A 35 minute game is about 640k tokens. That is heavy and may hit per-minute token limits on the free tier.
- Recommended setting: high resolution with `fps` 0.2 to 0.25 (one frame every 4 to 5 seconds). Planning phases last about 30 seconds, so each still gets 6+ frames. A 35 minute game drops to roughly 170k to 200k tokens.
- [U] Free-tier requests/min and tokens/min for the chosen model. Check AI Studio's rate limit page for the project key.

### Extraction design (two passes per game)
Pass 1, economy table, whole game clip, fps 0.2, high resolution:
- Per round: `stage` ("3-2"), `gold`, `hp`, `level`, `timestamp`. Null when unreadable.
- Also ask for `augment_picks`: round, augment name chosen, read from the selection screen.

Pass 2, board state, only at key rounds (2-1, 3-2, 4-1, 4-5, last round):
- Clip a 40 second window around each timestamp from pass 1, fps 1, high resolution. Short windows keep cost low and let the model see the board with fewer frames to confuse.
- Fields: `active_traits` (name + count), `board_units` (name + stars), `bench_units`, `board_items` per unit, `bench_items`, `wisp_offered`, `wisp_bought`.
- Put the full Set 18 champion, trait, item, augment, and Wisp name lists (from static data) in the prompt. Require answers from those lists only.

Prompt rules that reduce invented data:
- "Report only values visible on screen. Use null if not readable. Do not infer."
- Describe where things are: stage indicator top center, gold bottom center above the shop, level bottom left, player HP list on the right edge, trait panel on the left edge, item bench on the left.
- [U] HUD positions after the Set 18 Unreal engine migration. The set moved to a new engine, so layout may differ from older footage. Check one current VOD and adjust the description.
- State that the streamer's own name in the HP list is the row to read, and give the streamer's in-game name per video.

### Ground truth for free
The streamer's final board is in the Riot API. Resolve their Riot ID with `GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}` on the regional host [V], pull their match list around the VOD date, and match by `game_datetime` and placement. Compare Gemini's last-round units, stars, and items to the API. This scores Tier 2 and Tier 3 fields without hand labeling.

## 5. VOD sourcing (human task, ~20 minutes)

Not researched: which specific channels currently post full Set 18 VODs. Someone on the team who watches TFT should pick them. Criteria:

- Full unedited stream VODs or full-game uploads, not highlights. Must be public on YouTube (Twitch VODs do not work with the URL input).
- Native game HUD visible. Avoid heavy overlays covering the HP list, trait panel, or gold.
- 1080p source.
- Patch 18.5 or later so the data matches the API pull window.
- At least 5 different players, NA ranked.
- The streamer's Riot ID is known (needed for the ground-truth match).
- Record every game in the VOD in order, with start and end timestamps, in `data/vods/manifest.csv`.

## 6. Open questions to resolve in the first hour of coding

1. Does `augments` exist in a fresh Set 18 match JSON? (expect no)
2. What is the ranked `queue_id`? (expect 1100)
3. What do `character_id`, trait `name`, and `itemNames` look like, and do they join cleanly to CDragon `apiName`?
4. How do Lux's 9 variants and summoned units appear in `units[]`?
5. What does `game_version` look like, and how does it map to 18.x?
6. Max `count` on the match-ids endpoint?
7. Gemini: does structured output work with a YouTube video part? Are clip offsets in seconds? What is the per-item resolution field in Python?
8. Gemini accuracy on ONE game for gold, HP, level, stage versus watching it yourself. Go or no-go for Track B.
9. Where does an active Wisp show on the HUD?

Write answers into `docs/findings.md` in the repo as they are resolved.

## 7. Suggested first prompt for the Opus session

> Read plan.md and research.md. research.md overrides plan.md where they differ. Scaffold the repo from plan.md section 4. Then do only this: build collect.py with the rate limiter and disk cache, pull the Master+ NA puuid list and 50 matches, and answer open questions 1 to 6 from research.md section 6 in docs/findings.md with evidence from the JSON. Stop and report. My Riot key is in .env as RIOT_API_KEY.

---

## Sources

Riot API
- Riot TFT developer docs and policy: https://developer.riotgames.com/docs/tft
- Riot rate limiting: https://developer.riotgames.com/rate-limiting.html
- tft-match-v1 reference mirror: https://glama.ai/mcp/servers/@chirpon9/riot-docs-mcp/blob/bd23bbcbe155efed4c6774a7dde0bb8020d76191/src/riot_docs/tft-match-v1.md
- tft-league-v1 reference mirror: https://glama.ai/mcp/servers/@chirpon9/riot-docs-mcp/blob/bd23bbcbe155efed4c6774a7dde0bb8020d76191/src/riot_docs/tft-league-v1.md
- Dev key limits: https://www.riotgames.com/en/DevRel/riot-games-api-change-log
- Community Dragon assets docs: https://communitydragon.org/documentation/assets
- Augments removed from match history (Riot, Nov 2024): https://reddit.birdcat.cafe/r/CompetitiveTFT/comments/1gqln4u/update_on_augments_on_end_of_game_screen/lwz6554

Set 18
- Patch schedule and Set 19 date: https://seemeta.com/en/tft/set-18/release-dates and https://esports.gg/news/teamfight-tactics/tft-set-19-sneak-peek-you-can-swap-how-the-trait-plays-like-youd-swap-a-record-during-a-set/
- Pool sizes and shop odds: https://seemeta.com/en/tft/set-18/odds
- Traits and champions: https://outof.games/realms/tft/guides/574-all-traits-in-teamfight-tactics-enchanted-wilds-set-18/
- Set overview, Coven Essence values: https://op.gg/tft/set/18
- Wisps: https://mobalytics.gg/tft/guides/set-18-mechanic-wisps
- Gold rules: https://wiki.leagueoflegends.com/en-us/TFT:Gold
- Streak change in 14.1: https://gouphee.notion.site/Ramifications-of-Streak-changes-78c740bccc4a43c19ed7f1814b646e9e

Gemini
- Video understanding: https://ai.google.dev/gemini-api/docs/video-understanding
- Media resolution: https://ai.google.dev/gemini-api/docs/media-resolution
- Structured output: https://ai.google.dev/gemini-api/docs/structured-output.md.txt
- Interactions API reference: https://ai.google.dev/api/interactions-api

---

## Addendum: Set 18 augment list (added 2026-11-11)

- [V] `augments_set18.csv` holds all 245 Set 18 augments from Mobalytics' augments page, read in a live browser with all three tier tabs: 68 silver, 109 gold, 68 prismatic. Source: https://mobalytics.gg/tft/augments (page title "TFT Set 18 Augments List"). The project owner treats this site as trusted.
- Columns: `name`, `tier`, `tier_num`, `mentions`, `econ_related`.
- `mentions` is a keyword scan of each description (gold, xp, reroll, streak, interest, wisp, hp_cost). `econ_related` is yes when any of the first five appear. This is a rough tag, not a judgment of what the augment is for. Example: an item augment that also gives 2 gold is tagged yes. Review the yes rows by hand before using the tag in H5.
- Use the `name` column as the allowed-answers list for augments in the Gemini prompt.
- Cross-check names against CDragon static data when it is downloaded. Flag any name present in one source and not the other.
- Augments whose text mentions streaks (H4): Called Shot, Quick Streaks. Text mentions interest (H5): Capital Gains I and II, Hustler, Investment Strategy I and II, Hedge Fund, Going Long. Wisp economy: Wisp Rebate, Wisp Rebate +, Blossom's Call, Residual Magic line.
- Descriptions were not copied into the CSV. Pull them from static data if needed.

---

## Addendum 2: Mobalytics static data (pulled 2026-11-11, page data patch 18.6)

All files in `data/static/`. Source: Mobalytics page data for Set 18, read in a live browser. Project owner treats Mobalytics as trusted.

| File | Rows | Contents |
|---|---|---|
| champions.csv | 73 (65 champions, Lux counted once per 9 variants) | cost, traits, HP and AD at 1/2/3 star, attack speed, armor, MR, range, mana start/max, ability name, recommended items |
| abilities.csv | 73 | ability name, damage type, main damage 1/2/3 star, rough tags, full description |
| traits.csv | 36 | type, breakpoints, effect per breakpoint, champions with cost |
| items.csv | 140 | category, stats, recipe, emblem trait, Riot API id |
| augments.csv | 262 (245 shown on site, 17 hidden) | tier, Riot API id, Mobalytics category (economy/combat/item/trait), Mobalytics strength tier S/A/B/C |
| wisps_partial.csv | 29 | INCOMPLETE. See below. |

Corrections to earlier sections (Mobalytics is newer):
- **Coven** Essence: (3) 2 per kill / 22 per loss, (4) 2 / 28, (5) 3 / 35, (7) 10 / 60. Rewards start at 40 Essence.
- **Blossom** (7) gives 4 gold per Wisp bought. AD/AP bonus 12/30/50/55/100%.
- The origin listed earlier as **Eldritch is Blackthorn** in current data (Rek'Sai, Veigar, Warwick, Azir, Malphite, Lux). Mobalytics still uses the slug `lux-eldritch` for Lux (Blackthorn), so the rename happened after launch.
- **Pebbles** has Mobalytics slug `sentry`, **Mama Beak** has slug `crimson-raptor`. Expect Riot `character_id` names to differ from display names. Join on normalized names and check misses.
- Riot API ids for items and augments use a `DA_` prefix in Set 18 (e.g. `DA_InfinityEdge`, `DA_CalledShot`). This should match match-JSON `itemNames`. Confirm on real data.
- Ability text gaps: Ezreal and Mama Beak have missing numbers on Mobalytics (marked `[value missing]`).
- Mobalytics' augment `site_category` and `site_strength_tier` are their editorial labels, not match statistics.

Wisps: the full list (about 180 to 200) loads only in a real browser. `wisps_partial.csv` has 29 names collected from text-visible sources, several without cost or category. Do not use it as the Gemini allowed-names list. To complete it, run `python -m tftnet static cdragon` on a machine with normal internet (pulls Community Dragon TFT data, which includes Wisps), or open https://mobalytics.gg/tft/set18/wisps in a browser and export.
