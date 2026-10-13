"""Statistical correctness tests for insights_engine preprocessing.

Covers the methods added for honest small-sample analytics:
- Theil-Sen robust follower slope (endpoint-corruption resistance)
- empirical-Bayes shrinkage of bucket means
- Poisson z-test on week-over-week momentum
- Wilson lower bound on engagement rates
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.insights_engine import (
    _momentum_z,
    _shrink_mean,
    _theil_sen_slope,
    _wilson_low,
)


def _series(points: list[int], start: datetime | None = None):
    base = start or datetime(2026, 9, 1, tzinfo=UTC)
    return [(base + timedelta(days=i), n) for i, n in enumerate(points)]


# ── Theil-Sen slope ──────────────────────────────────────────────────────


def test_theil_sen_linear_series():
    s = _theil_sen_slope(_series([10, 12, 14, 16, 18]))
    assert s == 2.0


def test_theil_sen_robust_to_endpoint_outlier():
    clean = _theil_sen_slope(_series([50, 52, 54, 56, 58]))
    # one corrupted scrape reading at the end
    dirty = _theil_sen_slope(_series([50, 52, 54, 56, 500]))
    assert clean == 2.0
    assert abs(dirty - 2.0) < 1.0  # median slope barely moves


def test_theil_sen_endpoints_delta_would_lie():
    # The failure mode we're fixing: first→last delta says +45/day
    dirty = _series([50, 52, 54, 56, 500])
    naive = (dirty[-1][1] - dirty[0][1]) / (dirty[-1][0] - dirty[0][0]).days
    assert naive == 112.5
    assert _theil_sen_slope(dirty) < 10


def test_theil_sen_needs_three_points():
    assert _theil_sen_slope(_series([10, 20])) is None
    assert _theil_sen_slope(_series([10])) is None
    assert _theil_sen_slope([]) is None


def test_theil_sen_flat_and_negative():
    assert _theil_sen_slope(_series([100, 100, 100, 100])) == 0.0
    assert _theil_sen_slope(_series([100, 90, 80, 70])) == -10.0


# ── empirical-Bayes shrinkage ────────────────────────────────────────────


def test_shrink_mean_small_bucket_pulls_to_global():
    # bucket: 1 post with ER 20; platform mean 4. k=4 → mostly prior
    shrunk = _shrink_mean(20.0, 1, 4.0)
    assert shrunk == (20.0 + 4 * 4.0) / 5.0
    assert shrunk < 10  # far from the raw 20


def test_shrink_mean_large_bucket_keeps_own_evidence():
    # n=96 vs k=4 → ~96% weight on own data
    shrunk = _shrink_mean(20.0 * 96, 96, 4.0)
    assert abs(shrunk - 20.0) < 1.0


def test_shrink_mean_zero_n_returns_prior():
    assert _shrink_mean(0.0, 0, 5.0) == 5.0


# ── Poisson z on momentum ────────────────────────────────────────────────


def test_momentum_z_noise_vs_real():
    # 4→2 is -33% raw but z=-0.816: noise, must not fire a "dropped" rec
    assert abs(_momentum_z(2, 4)) < 1.645
    # 40→24 is -40% at z=-2.0: statistically real
    assert _momentum_z(24, 40) <= -1.645
    # 5→0 (new activity) handled by the prior==0 branch, not z
    assert _momentum_z(5, 0) > 0
    assert _momentum_z(0, 0) == 0.0


# ── Wilson lower bound ───────────────────────────────────────────────────


def test_wilson_low_small_sample_honest():
    # 2 engagements / 10 impressions = 20% raw, ~5.7% lower bound
    low = _wilson_low(2, 10)
    assert low is not None and 0.03 < low < 0.10


def test_wilson_low_converges_on_big_n():
    low = _wilson_low(200, 1000)
    assert low is not None and abs(low - 0.2) < 0.05


def test_wilson_low_edge_cases():
    assert _wilson_low(0, 100) == 0.0
    assert _wilson_low(5, 0) is None
