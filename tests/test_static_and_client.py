from tftnet.riot.client import RateLimiter
from tftnet.static import is_lux, norm, unit_cost, allowed_names


def test_norm_handles_riot_ids_and_names():
    assert norm("TFT18_Ahri") == norm("Ahri") == "ahri"
    assert norm("TFT18_KhaZix") == norm("Kha'Zix")
    assert norm("DA_InfinityEdge") == norm("Infinity Edge")
    assert norm("TFT18_Eldritch") == "blackthorn"


def test_costs_and_lux():
    assert unit_cost("TFT18_Ahri") == 4
    assert unit_cost("TFT18_Lux_Blossom") == 5 and is_lux("TFT18_Lux_Blossom")
    assert unit_cost("TFT18_Unknown", rarity=2) == 3


def test_allowed_names_nonempty():
    n = allowed_names()
    assert len(n["champions"]) == 65 and len(n["augments"]) == 245


def test_rate_limiter_respects_windows():
    t = [0.0]
    sleeps = []
    lim = RateLimiter([(3, 1.0), (5, 10.0)], clock=lambda: t[0], sleep=lambda d: (sleeps.append(d), t.__setitem__(0, t[0] + d)))
    for _ in range(6):
        lim.wait()
    # 3 calls at t=0, then the 1 s window forces a wait; the 6th call must wait for the 10 s window.
    assert len(sleeps) == 2
    assert 1.0 <= sum(sleeps[:1]) < 1.1 and t[0] >= 10.0
