from tftnet import figures as F


def test_ranking_plot_handles_estimate_outside_ci(tmp_path):
    h1 = {"spearman_vs_test": {"pagerank": {"estimate": 0.99, "ci_low": 0.90, "ci_high": 0.98},
                               "pick_rate": {"estimate": 0.5, "ci_low": 0.3, "ci_high": 0.7}}}
    assert F.ranking_comparison(h1, tmp_path / "r.png").exists()
