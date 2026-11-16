import pandas as pd

from tftnet.vod import pipeline as vp
from tftnet.vod.gemini import GeminiVideo
from tftnet.vod.rounds import derive, validate
from tftnet.vod.schema import RoundCheck


def _game():
    return pd.DataFrame({"game_id": "g1", "stage": ["2-1", "2-2", "2-3", "2-5", "2-6"], "gold": [10, 15, 20, 26, 33],
                         "hp": [100, 95, 99, 90, 84], "level": [4, 4, 5, 5, 5], "timestamp": ["01:00"] * 5,
                         "video_s": [60, 120, 180, 240, 300]})


def test_validate_flags_hp_increase():
    v = validate(_game())
    assert "hp_increased" in v.loc[2, "flags"]
    assert v.loc[0, "flags"] == ""


def test_derive_streaks():
    g = _game()
    g.loc[2, "hp"] = 95
    d = derive(validate(g))
    assert list(d["result"][:2]) == ["L", "W"]
    assert d.loc[0, "streak"] == -1


def test_parse_fenced_json():
    m = GeminiVideo.parse('```json\n{"stage":"3-2","gold":40}\n```', RoundCheck)
    assert m.gold == 40


class StubGem:
    """Pretends to be Gemini: re-reading a flagged round returns the corrected HP."""
    def ask_video(self, url, prompt, schema, start_s=None, end_s=None, fps=0.25):
        m = RoundCheck(stage="2-3", gold=20, hp=92, level=5, confidence="high")
        return m, m.model_dump_json(), "stub"


def test_gemini_review_fixes_flagged_round(settings):
    manifest = pd.DataFrame([{"game_id": "g1", "video_url": "u", "start_s": 0, "end_s": 400, "channel": "c",
                              "streamer_name": "S"}])
    reviewed = vp.review_flagged(settings, StubGem(), validate(_game()), manifest)
    row = reviewed[reviewed["stage"] == "2-3"].iloc[0]
    assert row["hp"] == 92 and row["hp_pass1"] == 99
    assert row["flags"] == ""
    assert "changed=hp" in row["review"]


def test_accuracy_vs_hand_labels():
    g = _game()
    hand = g[["game_id", "stage", "gold", "hp", "level"]].copy()
    hand.loc[0, "gold"] = 11
    acc = vp.accuracy_vs_hand_labels(g, hand).set_index("field")
    assert acc.loc["gold", "exact_match"] == 0.8 and acc.loc["hp", "exact_match"] == 1.0
