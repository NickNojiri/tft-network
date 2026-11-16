# Showing it working (10 minutes)

## Level 1: no keys needed (always works)
```bash
pip install -e ".[gemini,dev]"
python -m pytest -q          # 15 tests pass
python -m tftnet demo        # ~35 s, full pipeline on synthetic data with planted effects
```
Show `demo_run/out/figures/` (7 charts) and `demo_run/out/claude_bundle.md`.
Say: "The fake data has a congestion penalty built in. The pipeline measures it back, so the method works."

## Level 2: real Riot data (2 minutes, needs a Riot dev key in .env)
```bash
cp .env.example .env         # paste RIOT_API_KEY
python -m tftnet smoke       # ~60 real Master+ matches through every step
```
Prints real unit ids, queue ids, comps found, and a (not yet meaningful) H1 and H2 number.
If it lists "units not in static data", that is expected on a first run: add them to `static.ALIASES`.

## Level 3: Gemini reading a VOD (needs GEMINI_API_KEY and one manifest row)
```bash
cp data/vods/manifest.example.csv data/vods/manifest.csv   # edit: one real YouTube game
python -m tftnet vod test-one
```
Prints stage, gold, HP, level per round. Pause the video on two rounds and compare live.
If the model name is rejected, set GEMINI_MODEL in .env to one listed in AI Studio.
