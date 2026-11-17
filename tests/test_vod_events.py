import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tftnet.vod import events as ev
from tftnet.vod.budget import Budget, DailyLimitReached
from tftnet.vod.gemini import GeminiVideo, VideoPart, classify_error
from tftnet.vod.rounds import consensus, gold_accounting, wisp_schedule, xp_gained
from tftnet.vod.schema import Segmentation, Snapshots, SnapshotsFlat, Zooms


# ---------------------------------------------------------------- gold accounting
def test_gold_accounting_splits_spend_into_rerolls():
    # 50 -> 20: 1 XP buy (4) + Karma and Yorick (1 + 1) + a 2-gold Wisp = 8, leaving 22 = 11 rerolls
    a = gold_accounting(50, 20, xp_buys=1, unit_cost=2, wisp_cost=2, sold_value=0)
    assert a["spend"] == 30 and a["rerolls"] == 11 and a["acct_flags"] == ""


def test_gold_accounting_flags_odd_negative_and_mismatch():
    assert "acct_odd" in gold_accounting(50, 21, 1, 2, 2)["acct_flags"]
    assert "acct_negative" in gold_accounting(30, 28, 1, 4, 0)["acct_flags"]
    assert "gold_mismatch" in gold_accounting(50, 20, 1, 2, 2, rerolls_read=8)["acct_flags"]
    assert gold_accounting(50, 20, 1, 2, 2, rerolls_read=10)["acct_flags"] == ""  # 2 gold off is tolerated
    assert gold_accounting(50, 20, None, 2, 0)["acct_flags"] == "acct_partial"
    assert gold_accounting(None, 20, 1, 2, 0)["acct_flags"] == "acct_no_gold"


def test_selling_adds_gold_back():
    a = gold_accounting(10, 12, xp_buys=0, unit_cost=0, wisp_cost=0, sold_value=4)
    assert a["spend"] == 2 and a["rerolls"] == 1


def test_xp_gained_uses_hud_max_then_table():
    assert xp_gained(5, 10, 5, 18, 20) == 8
    assert xp_gained(5, 10, 6, 2, 20) == 12          # 10 to finish level 5, then 2
    assert xp_gained(5, 10, 6, 2, None) == 12        # same from the table (5 -> 6 needs 20)
    assert math.isnan(xp_gained(5, None, 6, 2, 20))


def test_consensus_never_imputes_zero():
    assert consensus([12, 12, 13]) == (12.0, "exact")
    assert consensus([12, None, None]) == (12.0, "inferred")
    assert consensus([12, 14]) == (13.0, "inferred")
    v, c = consensus([None, None])
    assert math.isnan(v) and c == "missing"


# ---------------------------------------------------------------- Wisp schedule
def test_wisp_schedule_alternates_and_fills_unseen():
    stages = ["2-1", "2-2", "2-3", "2-4", "2-5", "2-6"]
    status = ["offered", "unseen", "offered", "no_shop", "unseen", "unseen"]
    assert wisp_schedule(stages, status) == [True, False, True, None, False, True]


def test_wisp_schedule_odd_rerolls_keep_parity_and_backfill():
    stages = ["2-1", "2-2", "2-3"]
    # 2-1 not seen; 2-2 offered with 3 rerolls (4 shops) so 2-3's first shop is a Wisp shop again
    assert wisp_schedule(stages, ["unseen", "offered", "unseen"], [None, 3, None]) == [False, True, True]


def test_wisp_schedule_every_shop_after_two_offers():
    stages = ["3-1", "3-2", "3-3"]
    assert wisp_schedule(stages, ["offered", "offered", "unseen"], [0, 0, None]) == [True, True, True]


def test_wisp_schedule_unknown_without_sightings():
    assert wisp_schedule(["2-1", "2-2"], ["unseen", "unseen"]) == [None, None]


# ---------------------------------------------------------------- budget
class Clock:
    def __init__(self, t):
        self.t, self.slept = t, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


NOON_PT = 1794772800  # 2026-11-15 12:00 PST


def test_budget_stops_at_daily_limit_and_resets_next_day(tmp_path):
    c = Clock(NOON_PT)
    b = Budget(tmp_path / "b.json", daily_limit=3, rpm=100, tpm=10**9, now=c.now, sleep=c.sleep)
    for _ in range(3):
        b.acquire(1000)
    with pytest.raises(DailyLimitReached):
        b.acquire(1000)
    # counter survives a restart
    assert Budget(tmp_path / "b.json", daily_limit=3, now=c.now).used_today() == 3
    c.t += 13 * 3600  # 01:00 PT the next day
    b.acquire(1000)
    assert b.used_today() == 1


def test_budget_waits_for_rpm_and_tpm(tmp_path):
    c = Clock(NOON_PT)
    b = Budget(tmp_path / "b.json", daily_limit=50, rpm=2, tpm=100_000, now=c.now, sleep=c.sleep)
    b.acquire(10)
    b.acquire(10)
    assert c.slept == []
    b.acquire(10)  # third request in the same minute waits
    assert sum(c.slept) >= 59
    c.slept.clear()
    c.t += 120
    b.acquire(90_000)
    b.settle(95_000)
    b.acquire(20_000)  # 95K + 20K > 100K tokens/minute
    assert sum(c.slept) >= 59


# ---------------------------------------------------------------- retries
class APIError(Exception):
    def __init__(self, code, msg):
        super().__init__(f"{code} {msg}")
        self.code = code


def _gem(tmp_path, clock, **kw):
    gem = GeminiVideo("test-key-not-real", "gemini-test", sleep=clock.sleep,
                      budget=Budget(tmp_path / "b.json", daily_limit=kw.pop("limit", 10), rpm=100, tpm=10**9,
                                    now=clock.now, sleep=clock.sleep), **kw)
    return gem


def test_classify_error():
    assert classify_error(APIError(503, "UNAVAILABLE. high demand")) == "retry"
    assert classify_error(APIError(429, "RESOURCE_EXHAUSTED quota GenerateRequestsPerMinute")) == "rate"
    assert classify_error(APIError(429, "RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProject")) == "daily"
    assert classify_error(APIError(400, "INVALID_ARGUMENT bad schema")) == "fatal"


def test_retries_503_with_backoff_and_counts_every_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr("tftnet.vod.gemini.random.uniform", lambda a, b: 0.0)  # no jitter
    c = Clock(NOON_PT)
    gem = _gem(tmp_path, c)
    calls = []

    def flaky(parts, prompt, schema, res, audio):
        calls.append(1)
        if len(calls) < 3:
            raise APIError(503, "UNAVAILABLE")
        return '{"rounds": []}', {"prompt_tokens": 1234, "in_video_tokens": 1000}, "generate_content"

    gem._ia_parts = flaky
    parsed, _, _, usage = gem.ask_parts([VideoPart("u", 0, 60, 0.2)], "p", Segmentation, "low")
    assert parsed.rounds == [] and usage["prompt_tokens"] == 1234 and usage["audio_excluded"]
    assert len(calls) == 3 and c.slept == [8.0, 16.0]
    assert gem.budget.used_today() == 3


def test_daily_quota_error_stops(tmp_path):
    c = Clock(NOON_PT)
    gem = _gem(tmp_path, c)

    def boom(*a):
        raise APIError(429, "RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProject")

    gem._ia_parts = boom
    with pytest.raises(DailyLimitReached):
        gem.ask_parts([VideoPart("u", 0, 60, 0.2)], "p", Segmentation, "low")


def test_key_is_scrubbed_from_logged_errors(tmp_path):
    from tftnet.vod.budget import UsageLog

    c = Clock(NOON_PT)
    log = UsageLog(tmp_path / "log.jsonl")
    gem = _gem(tmp_path, c, usage_log=log)

    def leak(*a):
        raise APIError(400, "bad request for key test-key-not-real")

    gem._ia_parts = gem._gc_parts = leak
    with pytest.raises(APIError):
        gem.ask_parts([VideoPart("u", 0, 60, 0.2)], "p", Segmentation, "low")
    assert "test-key-not-real" not in (tmp_path / "log.jsonl").read_text()


def test_rejected_frames_only_falls_back_and_is_remembered(tmp_path):
    c = Clock(NOON_PT)
    caps = tmp_path / "caps.json"
    gem = _gem(tmp_path, c, caps_path=caps)
    seen = []

    def ia(parts, prompt, schema, res, frames_only):
        seen.append(("ia", frames_only))
        if frames_only:
            raise APIError(400, "INVALID_ARGUMENT Unknown name \"content_layers\": Cannot find field.")
        return '{"rounds": []}', {"prompt_tokens": 10}, "interactions"

    gem._ia_parts = ia
    _, _, api, usage = gem.ask_parts([VideoPart("u", 0, 60, 0.2)], "p", Segmentation, "low")
    assert seen == [("ia", True), ("ia", False)] and api == "interactions" and usage["audio_excluded"] is False
    again = _gem(tmp_path, c, caps_path=caps)
    assert again.mode() == {"api": "interactions", "frames_only": False}  # no second failed probe


class InteractionsBadRequest(Exception):
    """Shaped like the Interactions client's error: status_code, not code."""

    def __init__(self, msg):
        super().__init__(f"Error code: 400 - {{'error': {{'code': 'invalid_request', 'message': '{msg}'}}}}")
        self.status_code = 400


def test_interactions_style_400_also_falls_back(tmp_path):
    c = Clock(NOON_PT)
    gem = _gem(tmp_path, c)
    seen = []

    def ia(parts, prompt, schema, res, frames_only):
        seen.append(frames_only)
        if frames_only:
            raise InteractionsBadRequest("content_layers is only available in v1beta")
        return '{"rounds": []}', {}, "interactions"

    gem._ia_parts = ia
    gem.ask_parts([VideoPart("u", 0, 60, 0.2)], "p", Segmentation, "low")
    assert seen == [True, False]


def test_frames_only_interactions_call_uses_v1beta(tmp_path):
    c = Clock(NOON_PT)
    gem = _gem(tmp_path, c)
    cap = {}

    class Resp:
        output_text = '{"rounds": []}'
        usage = None

    gem.client = SimpleNamespace(interactions=SimpleNamespace(create=lambda **kw: cap.update(kw) or Resp()))
    gem._ia_parts([VideoPart("u", 0, 60, 0.2)], "p", Segmentation, "low", True)
    assert cap["api_version"] == "v1beta" and cap["input"][0]["processing"]["content_layers"] == ["frames"]
    cap.clear()
    gem._ia_parts([VideoPart("u", 0, 60, 0.2)], "p", Segmentation, "low", False)
    assert "api_version" not in cap and "content_layers" not in cap["input"][0]["processing"]


def test_schema_rejection_skips_to_generate_content(tmp_path):
    c = Clock(NOON_PT)
    gem = _gem(tmp_path, c)
    seen = []

    def ia(*a):
        seen.append("ia")
        raise APIError(400, "INVALID_ARGUMENT response_format schema is not supported with video")

    def gc(*a):
        seen.append("gc")
        return '{"rounds": []}', {}, "generate_content"

    gem._ia_parts, gem._gc_parts = ia, gc
    _, _, api, _ = gem.ask_parts([VideoPart("u", 0, 60, 0.2)], "p", Segmentation, "low")
    assert seen == ["ia", "gc"] and api == "generate_content"


def test_parts_limit_is_reported_not_retried(tmp_path):
    from tftnet.vod.gemini import PartsLimitError

    c = Clock(NOON_PT)
    gem = _gem(tmp_path, c)

    def ia(*a):
        raise APIError(400, "INVALID_ARGUMENT: a request can contain at most 10 video parts")

    gem._ia_parts = ia
    with pytest.raises(PartsLimitError):
        gem.ask_parts([VideoPart("u", 0, 60, 0.2)] * 12, "p", Segmentation, "low")
    assert gem.budget.used_today() == 1


# ---------------------------------------------------------------- segmentation and windows
def test_abs_time_maps_clip_clock_to_video_clock():
    parts = [VideoPart("u", 600, 1200, 0.2), VideoPart("u", 1200, 1800, 0.2)]
    assert ev.abs_time(30, 2, parts) == 1230      # answered on the clip's clock
    assert ev.abs_time(1250, 2, parts) == 1250    # already video time
    assert ev.abs_time(700, None, parts) == 700


def test_parse_segmentation_drops_boundary_duplicates():
    parts = [VideoPart("u", 0, 600, 0.2), VideoPart("u", 600, 1200, 0.2)]
    parsed = {"rounds": [{"stage": "3-2", "part": 1, "planning": "09:30", "combat": "09:58"},
                         {"stage": "3-3", "part": 1, "planning": "10:40", "combat": "11:10"},
                         {"stage": "3-3", "part": 2, "planning": "00:41", "combat": "01:10"},
                         {"stage": "3-4", "part": 2, "planning": "01:50", "combat": "05:00"}]}
    seg, _ = ev.parse_segmentation([(parsed, parts)])
    assert list(seg["stage"]) == ["3-2", "3-3", "3-4"]
    assert seg.loc[1, "planning_s"] == 640
    assert math.isnan(seg.loc[2, "combat_s"])  # carousel: no combat time kept


def _seg(n):
    stages = [f"{2 + i // 7}-{1 + i % 7}" for i in range(n)]
    return pd.DataFrame({"stage": stages, "planning_s": [100.0 + 80 * i for i in range(n)],
                         "combat_s": [130.0 + 80 * i for i in range(n)]})


def test_snapshot_parts_fit_max_parts():
    cfg = ev.EventConfig(max_parts=10)
    few = ev.snapshot_parts(_seg(4), "u", cfg)
    assert len(few) == 8 and all(p.frames == 3 for p in few)  # two 3-frame windows per round
    many = ev.snapshot_parts(_seg(30), "u", cfg)
    assert len(many) == 10
    assert "round 2-1" in many[0].label and "Part 1" in many[0].label


def test_summarize_reads_per_frame_and_flat_answers():
    seg = pd.DataFrame({"stage": ["2-1", "2-2"], "planning_s": [100.0, 180.0], "combat_s": [130.0, 210.0]})
    per_frame = {"stage": "2-1", "start": [_frame("01:41", 26)] * 2 + [_frame("01:45", 27)],
                 "end": [_frame("02:09", 20)] * 3}
    flat = {"stage": "2-2", "gold_start": 30, "hp": 88, "level_start": 5, "gold_end": None, "frames_agree": False}
    t = ev.summarize_snapshots(seg, [per_frame, flat]).set_index("stage")
    assert (t.loc["2-1", "gold"], t.loc["2-1", "gold_conf"]) == (26, "exact")
    assert (t.loc["2-2", "gold"], t.loc["2-2", "gold_conf"], t.loc["2-2", "hp_conf"]) == (30, "inferred", "inferred")
    assert np.isnan(t.loc["2-2", "gold_end"]) and t.loc["2-2", "gold_end_conf"] == "missing"
    assert ev.snapshot_coverage(seg, [per_frame, flat]) == 1.0


def test_expected_stages():
    assert ev.expected_stages("2-3") == ["1-1", "1-2", "1-3", "1-4", "2-1", "2-2", "2-3"]


# ---------------------------------------------------------------- one game end to end
def _frame(t, gold, hp=90, level=5, xp=4, xp_max=20):
    return {"t": t, "gold": gold, "hp": hp, "level": level, "xp": xp, "xp_max": xp_max}


class StubGem:
    """Answers each pass like Gemini would for a 4-round game."""
    model = "stub"

    def __init__(self):
        self.calls = []

    def ask_parts(self, parts, prompt, schema, resolution="medium", audio=False, tag=None):
        self.calls.append((schema.__name__, len(parts), resolution))
        if schema is Segmentation:
            data = {"rounds": [{"stage": "2-1", "part": 1, "planning": "01:40", "combat": "02:10"},
                               {"stage": "2-2", "part": 1, "planning": "03:00", "combat": "03:30"},
                               {"stage": "2-3", "part": 1, "planning": "04:20", "combat": "04:50"},
                               {"stage": "2-5", "part": 1, "planning": "06:00", "combat": "06:30"}],
                    "final_placement": 3}
        elif schema is SnapshotsFlat:
            def flat(st, g0, g1, agree, xp_end=4, **kw):
                return {"stage": st, "gold_start": g0, "hp": 90, "level_start": 5, "xp_start": 4, "xp_max_start": 20,
                        "gold_end": g1, "level_end": 5, "xp_end": xp_end, "frames_agree": agree, **kw}

            data = {"rounds": [
                flat("2-1", 10, 10, True, wisp_slot="champion"),
                flat("2-2", 42, 20, True, xp_end=8, wisp_slot="wisp", wisp_name="All Ones"),
                flat("2-3", None, 21, None),
                flat("2-5", 26, 26, False, wisp_slot="champion")]}
        else:
            stages = [p.label.split("Round ")[1].split(":")[0] for p in parts]
            rounds = []
            for st in stages:
                if st == "2-2":
                    rounds.append({"stage": "2-2", "gold_start": 42, "gold_end": 20, "hp": 90, "level_start": 5,
                                   "xp_start": 4, "xp_max_start": 20, "level_end": 5, "xp_end": 8, "xp_buys": 1,
                                   "rerolls": 7, "units_bought": ["Karma", "Yorick"], "wisp_slot": "wisp",
                                   "wisp_name": "All Ones", "wisp_bought": True})
                else:
                    rounds.append({"stage": st})  # still unreadable at high res
            data = {"rounds": rounds}
        m = schema.model_validate(data)
        return m, m.model_dump_json(), "stub", {"prompt_tokens": 5000, "in_video_tokens": 4800}


def test_extract_game_three_requests_and_accounting(settings, monkeypatch):
    monkeypatch.setattr(ev, "_wisps", lambda s: (["All Ones"], {"allones": 0.0}))
    g = SimpleNamespace(game_id="g1", video_url="u", start_s=60, end_s=480, streamer_name="S")
    gem = StubGem()
    t, summ = ev.extract_game(settings, gem, g, ev.EventConfig())
    assert [c[0] for c in gem.calls] == ["Segmentation", "SnapshotsFlat", "Zooms"]
    assert gem.calls[0][2] == "low" and gem.calls[1][2] == "high" and gem.calls[2][2] == "high"
    r = t.set_index("stage")
    # 2-2: 42 -> 20 = 22 spent; 1 XP buy (4) + Karma + Yorick (2) + free Wisp = 6; 16 left = 8 rerolls
    assert r.loc["2-2", "zoomed"] and r.loc["2-2", "rerolls"] == 8 and r.loc["2-2", "xp_buys"] == 1
    assert "gold_mismatch" not in r.loc["2-2", "acct_flags"]  # Gemini counted 7: 2 gold off, tolerated
    # 2-3: gold hidden in every frame -> stays empty, never 0
    assert np.isnan(r.loc["2-3", "gold"]) and r.loc["2-3", "gold_conf"] == "missing"
    assert r.loc["2-3", "confidence"] == "missing" and "unreadable" in r.loc["2-3", "zoom_reason"]
    # frames agreed -> exact; Gemini says they didn't -> inferred
    assert r.loc["2-1", "gold_conf"] == "exact"
    assert r.loc["2-5", "gold"] == 26 and r.loc["2-5", "gold_conf"] == "inferred"
    assert r.loc["2-1", "wisp_expected"] == False and r.loc["2-2", "wisp_expected"] == True  # noqa: E712
    assert summ["requests_sent"] == 3 and summ["rounds_missing"] == ["1-1", "1-2", "1-3", "1-4", "2-4"]
    assert summ["passes"]["B"]["prompt_tokens"] == 5000
    # second run: everything from cache
    gem2 = StubGem()
    t2, summ2 = ev.extract_game(settings, gem2, g, ev.EventConfig())
    assert gem2.calls == [] and summ2["requests_sent"] == 0
    pd.testing.assert_frame_equal(t, t2)


class LimitedGem(StubGem):
    """Refuses snapshot requests with more than 4 parts."""

    def ask_parts(self, parts, prompt, schema, **kw):
        from tftnet.vod.gemini import PartsLimitError

        if schema is SnapshotsFlat and len(parts) > 4:
            self.calls.append(("refused", len(parts), kw.get("resolution")))
            raise PartsLimitError("too many video parts")
        return super().ask_parts(parts, prompt, schema, **kw)


def test_snapshot_parts_refused_replans(settings, monkeypatch):
    from tftnet.vod.gemini import PartsLimitError

    monkeypatch.setattr(ev, "_wisps", lambda s: ([], {}))

    class RefuseOver10(StubGem):
        def ask_parts(self, parts, prompt, schema, **kw):
            if schema is SnapshotsFlat and len(parts) > 10:
                self.calls.append(("refused", len(parts), None))
                raise PartsLimitError("too many video parts")
            return super().ask_parts(parts, prompt, schema, **kw)

    seg_rounds = [{"stage": f"{2 + i // 7}-{1 + i % 7}", "part": 1, "planning": ev.fmt_ts(100 + 80 * i),
                   "combat": ev.fmt_ts(130 + 80 * i)} for i in range(8)]
    gem = RefuseOver10()
    orig = gem.ask_parts

    def ask(parts, prompt, schema, **kw):
        if schema is Segmentation:
            gem.calls.append(("Segmentation", len(parts), kw.get("resolution")))
            m = Segmentation.model_validate({"rounds": seg_rounds})
            return m, m.model_dump_json(), "stub", {}
        if schema is SnapshotsFlat and len(parts) <= 10:
            gem.calls.append(("Snapshots", len(parts), kw.get("resolution")))
            m = SnapshotsFlat.model_validate({"rounds": []})
            return m, m.model_dump_json(), "stub", {}
        return orig(parts, prompt, schema, **kw)

    gem.ask_parts = ask
    g = SimpleNamespace(game_id="g3", video_url="u", start_s=60, end_s=800, streamer_name="S")
    _, summ = ev.extract_game(settings, gem, g, ev.EventConfig(snap_max_parts=70))
    names = [c[0] for c in gem.calls]
    assert names[:3] == ["Segmentation", "refused", "Snapshots"]  # 16 windows refused, then one per round
    assert gem.calls[1][1] == 16 and gem.calls[2][1] == 8
    assert summ["passes"]["B"]["parts_refused"] == 70
    with pytest.raises(PartsLimitError):  # refusing even 10 parts is not retried
        ev.extract_game(settings, LimitedGem(), SimpleNamespace(game_id="g4", video_url="u", start_s=60,
                                                                  end_s=480, streamer_name="S"), ev.EventConfig())


def test_degenerate_snapshot_answer_replans_with_ten(settings, monkeypatch):
    monkeypatch.setattr(ev, "_wisps", lambda s: ([], {}))
    seg_rounds = [{"stage": f"{2 + i // 7}-{1 + i % 7}", "part": 1, "planning": ev.fmt_ts(100 + 80 * i),
                   "combat": ev.fmt_ts(130 + 80 * i)} for i in range(8)]
    calls = []

    class Degenerate:
        model = "stub"

        def ask_parts(self, parts, prompt, schema, **kw):
            calls.append((schema.__name__, len(parts)))
            if schema is Segmentation:
                data = {"rounds": seg_rounds}
            elif schema is SnapshotsFlat:
                stages = [r["stage"] for r in seg_rounds][: 1 if len(parts) > 10 else 8]
                data = {"rounds": [{"stage": st, "gold_start": 10, "frames_agree": True} for st in stages]}
            else:
                data = {"rounds": []}
            m = schema.model_validate(data)
            return m, m.model_dump_json(), "stub", {}

    g = SimpleNamespace(game_id="g5", video_url="u", start_s=60, end_s=800, streamer_name="S")
    t, summ = ev.extract_game(settings, Degenerate(), g, ev.EventConfig(snap_max_parts=70))
    assert [c for c in calls if c[0] == "SnapshotsFlat"] == [("SnapshotsFlat", 16), ("SnapshotsFlat", 8)]
    assert summ["passes"]["B"]["degenerate_parts"] == 70 and summ["passes"]["B"]["coverage"] == 1.0
    assert t["gold"].notna().all()