from tftnet.analysis import congestion, equilibrium, pagerank
from tftnet.demo.synthetic import make_matches
from tftnet.label import label_players
from tftnet.parse import build_players, split_train_test


def test_pipeline_recovers_planted_effects(settings):
    make_matches(settings.matches_dir, n_matches=450, seed=3)
    df = build_players(settings)
    assert df["match_id"].nunique() == 450
    lab = label_players(df)
    assert (lab["comp"] != "other").mean() > 0.9

    h2 = congestion.analyze(lab, n_boot=20, n_perm=20)
    assert h2["slope_per_extra_player"]["ci_low"] > 0  # congestion was planted

    train, test = split_train_test(lab)
    h1 = pagerank.evaluate(train, test, n_boot=20)
    assert h1["spearman_vs_test"]["pagerank"]["estimate"] > 0.5

    h3 = equilibrium.analyze(lab, min_games=40)
    assert h3["dynamics"]["n_pairs"] > 0


def test_contention_runs(settings):
    make_matches(settings.matches_dir, n_matches=30, seed=4)
    lab = congestion.add_contention(label_players(build_players(settings)))
    assert lab["contention"].notna().all()
