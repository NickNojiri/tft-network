from tftnet.riot.client import RateLimiter
from tftnet.static import is_lux, norm, unit_cost, allowed_names


def test_norm_handles_riot_ids_and_names():
    assert norm("TFT18_Ahri") == norm("Ahri") == "ahri"
    assert norm("TFT18_KhaZix") == norm("Kha'Zix")
    assert norm("DA_InfinityEdge") == norm("Infinity Edge")
    assert norm("TFT18_Eldritch") == "blackthorn"


def test_norm_handles_both_set18_id_formats_and_suffixes():
    # DA_18_Name and DA_Name18 both map to the static display-name key.
    assert norm("DA_18_Ahri") == norm("DA_Ahri18") == norm("Ahri") == "ahri"
    assert norm("DA_Amumu18") == norm("Amumu")
    assert norm("DA_Fiddlesticks18") == norm("Fiddlesticks")
    # Variant suffixes, alone or after the set number.
    assert norm("DA_18_Akali_AD") == norm("Akali")
    assert norm("DA_18_MasterYi_AD") == norm("Master Yi")
    assert norm("DA_KogMaw18_AD") == norm("Kog'Maw")
    assert norm("DA_Nidalee18_AP") == norm("Nidalee") and norm("DA_Gromp18_AP") == norm("Gromp")
    assert norm("DA_18_GnarSmall") == norm("Gnar")
    assert norm("DA_Lux18_Base") == "lux" and is_lux("DA_Lux18_Base") and is_lux("DA_18_Lux_Moonbeam")
    # Riftbeast units are real Set 18 champions (Community Dragon), not neutral monsters.
    assert unit_cost("DA_Krug18") == 3 and unit_cost("DA_Sentinel18") == 4 and unit_cost("DA_Scuttlecrab18") == 2
    assert unit_cost("DA_18_Sentry") == 1 and unit_cost("DA_CrimsonRaptor18") == 3
    assert unit_cost("DA_Taric18") == 5 and unit_cost("DA_18_Akali_AD") == 1
    # Traits in both formats, and renamed traits.
    assert norm("DA_Riftbeast18") == norm("DA_18_Riftbeast") == "riftbeast"
    assert norm("DA_FloraFatalis18") == norm("Flora Fatalis")
    assert norm("DA_18_Slayer") == norm("DA_18_EmblemSlayer") == norm("Ravager")
    assert norm("DA_AluneUniqueTrait18") == norm("Attuned")
    # Suffix rules never touch display names or single-digit tiers.
    assert norm("TFT6_Augment_CyberneticImplants2") == "cyberneticimplants2"
    assert norm("Small Gnar") == "smallgnar"


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
