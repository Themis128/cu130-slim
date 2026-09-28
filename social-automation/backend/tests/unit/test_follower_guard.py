"""Guard tests for implausible LinkedIn member follower scrape readings.

Regression: the sidecar scrape occasionally reads a wrong page metric —
network size (~952k) or a sidebar counter ("15") — and persisting those
poisoned the series, producing "-826 (-98.2%)" digest deltas.
"""

from app.services.analytics_sync import _plausible_follower_count


def test_first_reading_accepted():
    assert _plausible_follower_count(None, 841) is True


def test_normal_growth_accepted():
    assert _plausible_follower_count(841, 845) is True
    assert _plausible_follower_count(20, 22) is True


def test_network_size_spike_rejected():
    # The observed failure: 841 -> 952822 (network size, not followers)
    assert _plausible_follower_count(841, 952_822) is False


def test_implausible_drop_rejected():
    # The observed failure: 952822 -> 15 slipped through the upward-only guard
    assert _plausible_follower_count(841, 15) is False
    assert _plausible_follower_count(500, 12) is False


def test_small_account_drops_allowed():
    # Sub-100 accounts fluctuate by single digits — don't over-guard
    assert _plausible_follower_count(20, 8) is True


def test_real_moderate_drop_accepted():
    assert _plausible_follower_count(841, 700) is True
