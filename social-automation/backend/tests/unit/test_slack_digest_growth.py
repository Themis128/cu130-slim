"""Regression tests for _growth_stats window baselines.

Regression: prior-window follower readings must not seed the current
baseline — otherwise current growth includes prior-window gains
(reported by Devin Review on PR #132).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.slack_digest import _growth_stats


def _execute_result(rows):
    res = MagicMock()
    res.all.return_value = rows
    return res


@pytest.mark.asyncio
async def test_prior_window_readings_do_not_inflate_current_growth():
    """Prior 100→120, current 120→130 must report delta +10, not +30."""
    now = datetime.now(UTC)
    since = now - timedelta(days=30)
    prev_since = now - timedelta(days=60)
    snapshots = [
        ("linkedin", 100, prev_since + timedelta(days=5), "acct-1", "@me"),
        ("linkedin", 120, since - timedelta(days=1), "acct-1", "@me"),
        ("linkedin", 120, since + timedelta(days=1), "acct-1", "@me"),
        ("linkedin", 130, now - timedelta(hours=1), "acct-1", "@me"),
    ]
    db = AsyncMock()
    db.execute.side_effect = [
        _execute_result(snapshots),  # follower snapshots
        _execute_result([]),  # funnel
        _execute_result([]),  # audience events
    ]

    growth = await _growth_stats(
        db, "team-1", since, prev_since=prev_since, days=30
    )

    (row,) = growth["followers"]
    assert row["start"] == 120
    assert row["end"] == 130
    assert row["delta"] == 10
    assert row["prev_delta"] == 20


@pytest.mark.asyncio
async def test_account_without_current_window_readings_is_skipped():
    """An account with only prior-window snapshots claims no growth."""
    now = datetime.now(UTC)
    since = now - timedelta(days=30)
    prev_since = now - timedelta(days=60)
    snapshots = [
        ("twitter", 50, prev_since + timedelta(days=5), "acct-2", "@t"),
        ("twitter", 55, since - timedelta(days=1), "acct-2", "@t"),
    ]
    db = AsyncMock()
    db.execute.side_effect = [
        _execute_result(snapshots),
        _execute_result([]),
        _execute_result([]),
    ]

    growth = await _growth_stats(
        db, "team-1", since, prev_since=prev_since, days=30
    )

    # Helper returns {} when there is nothing to report.
    assert growth.get("followers", []) == []
