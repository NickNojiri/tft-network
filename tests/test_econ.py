from tftnet.analysis.econ_sim import compare_streak_rules, passive, streak_gold


def test_income_rules_match_research():
    assert [passive(s) for s in [(1, 2), (1, 3), (1, 4), (2, 1), (2, 2)]] == [2, 2, 3, 4, 5]
    assert [streak_gold(x) for x in [0, 2, 3, 4, 5, 6, 9, -3, -6]] == [0, 0, 1, 1, 2, 3, 3, 1, 3]


def test_sim_runs():
    r = compare_streak_rules(n_games=20)
    assert set(r["placement_on"].index) >= {"balanced", "greedy_econ"}
