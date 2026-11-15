from __future__ import annotations

from odte_spy_ibkr.research.stats import bonferroni_adjust, evaluate_feature, spearman_ic


def test_spearman_perfect_negative():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [-1.0, -2.0, -3.0, -4.0, -5.0]
    assert spearman_ic(xs, ys) is not None
    assert spearman_ic(xs, ys) < -0.99


def test_bonferroni_multiplies():
    assert bonferroni_adjust([0.05, 0.05]) == [0.1, 0.1]


def test_evaluate_feature_minimum_n():
    r = evaluate_feature("x", [1.0, 2.0], [0.1, -0.1])
    assert r.n == 2
    assert r.ic is None