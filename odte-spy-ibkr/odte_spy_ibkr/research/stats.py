from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureTestResult:
    feature: str
    n: int
    ic: float | None
    hit_rate: float | None
    t_stat: float | None
    p_value_approx: float | None
    baseline_hit: float | None


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return num / math.sqrt(vx * vy)


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for r, idx in enumerate(order, start=1):
        ranks[idx] = float(r)
    return ranks


def spearman_ic(xs: list[float], ys: list[float]) -> float | None:
    return _pearson(_rank(xs), _rank(ys))


def ic_t_stat(ic: float, n: int) -> float | None:
    if n < 4 or abs(ic) >= 1:
        return None
    return ic * math.sqrt(n - 2) / math.sqrt(1 - ic * ic)


def two_sided_p_from_t(t: float, df: int) -> float | None:
    if df < 1:
        return None
    x = df / (df + t * t)
    return min(1.0, 2.0 * _incomplete_beta(df / 2, 0.5, x))


def _incomplete_beta(a: float, b: float, x: float) -> float:
    """Rough regularized incomplete beta for p-values (adequate for research reporting)."""
    steps = 200
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    total = 0.0
    for i in range(steps):
        t = (i + 0.5) / steps
        if t < x:
            total += (t ** (a - 1)) * ((1 - t) ** (b - 1))
    return total / steps * x


def hit_rate(xs: list[float], ys: list[float]) -> float | None:
    if not xs:
        return None
    hits = sum(1 for x, y in zip(xs, ys, strict=True) if (x > 0) == (y > 0))
    return hits / len(xs)


def evaluate_feature(
    name: str,
    xs: list[float],
    ys: list[float],
    *,
    baseline_ys: list[float] | None = None,
) -> FeatureTestResult:
    pairs = [(x, y) for x, y in zip(xs, ys, strict=True) if x == x and y == y]  # drop NaN
    if len(pairs) < 5:
        return FeatureTestResult(name, len(pairs), None, None, None, None, None)
    fx, fy = zip(*pairs, strict=True)
    ic = spearman_ic(list(fx), list(fy))
    hr = hit_rate(list(fx), list(fy))
    t = ic_t_stat(ic, len(fx)) if ic is not None else None
    p = two_sided_p_from_t(t, len(fx) - 2) if t is not None else None
    baseline = hit_rate([1.0] * len(fy), list(fy)) if baseline_ys is None else hit_rate([1.0] * len(baseline_ys), baseline_ys)
    return FeatureTestResult(name, len(fx), ic, hr, t, p, baseline)


def bonferroni_adjust(p_values: list[float | None]) -> list[float | None]:
    m = sum(1 for p in p_values if p is not None)
    return [min(1.0, p * m) if p is not None else None for p in p_values]