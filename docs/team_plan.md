# Team plan

Today is Nov 13. Set 18 ends Dec 1 (Set 19 launch resets the ladder). Last Set 18 patch was 18.6B (Nov 11).

| Person | Owns | First task this week |
|---|---|---|
| 1 | Riot collection | `collect seed` NOW, then ids and matches. Re-run seed once more before Dec 1 |
| 2 | Parsing + comp labels + validation | Answer findings Q1-Q7 from the first 50 matches. Run the hand-label check with person 6 |
| 3 | H1 PageRank | Run `h1` on the demo to learn the outputs, then on real data. Owns figures 1-2 |
| 4 | H2 congestion + H3 equilibrium | Same with `h2`, `h3`. Owns figures 3-4 |
| 5 | VOD track (H4, H5) | Pick channels, `vod test-one`, fill the manifest, run the Gemini steps. Owns figures 5-6 |
| 6 | Report + slides | Report skeleton from `docs/report_outline.md`, hand labels, limitations paragraph |

Everyone: `pip install -e ".[gemini,dev]"`, `python -m pytest -q`, `python -m tftnet demo`.

## Milestones
- By Nov 16: seed saved, 50 matches pulled, findings Q1-Q7 answered, one VOD tested.
- By Nov 23: full match pull done (5-10k matches), manifest at 30 games, extraction done.
- By Nov 30: all analyses run, `bundle --gemini`, Claude review of the bundle, figures final.
- After: write the 2-page report and the 10-minute talk.
