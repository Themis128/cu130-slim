"""Follower-growth baseline handling for the change-log snapshots (#307)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import analytics

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=7)


def test_baseline_before_window_sets_change_and_start_point():
    snaps = [(NOW - timedelta(days=2), 105)]
    current, change, series = analytics._build_follower_series(snaps, 100, SINCE, NOW)
    assert current == 105
    assert change == 5
    assert series[0].date == SINCE.strftime("%Y-%m-%d")
    assert series[0].followers == 100
    assert [p.followers for p in series] == [100, 105, 105]
    assert series[-1].date == "2026-10-05"


def test_baseline_only_gives_flat_series_not_unsynced():
    current, change, series = analytics._build_follower_series([], 100, SINCE, NOW)
    assert (current, change) == (100, 0)
    assert [p.followers for p in series] == [100, 100]
    assert series[0].date == "2026-09-28"
    assert series[-1].date == "2026-10-05"


def test_no_baseline_uses_first_in_window_row():
    snaps = [(NOW - timedelta(days=5), 90), (NOW, 95)]
    current, change, series = analytics._build_follower_series(snaps, None, SINCE, NOW)
    assert (current, change) == (95, 5)
    assert [p.followers for p in series] == [90, 95]


def test_no_snapshots_returns_none():
    assert analytics._build_follower_series([], None, SINCE, NOW) is None


def test_naive_timestamps_are_treated_as_utc():
    snaps = [(datetime(2026, 10, 1, 23, 30), 7)]
    _, _, series = analytics._build_follower_series(snaps, None, SINCE, NOW)
    assert series[0].date == "2026-10-01"


def _result(rows=None, scalars=None):
    res = MagicMock()
    res.all.return_value = rows or []
    res.scalars.return_value.all.return_value = scalars or []
    return res


@pytest.mark.asyncio
async def test_endpoint_uses_pre_window_baseline_without_live_calls():
    changed = SimpleNamespace(id=uuid.uuid4(), platform="twitter", username="a")
    quiet = SimpleNamespace(id=uuid.uuid4(), platform="linkedin", username="b")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        _result(scalars=[changed, quiet]),
        _result(rows=[(changed.id, datetime.now(UTC) - timedelta(days=2), 105)]),
        _result(rows=[(changed.id, 100), (quiet.id, 40)]),
    ])
    live = AsyncMock(return_value=999)
    with patch.object(analytics, "_team_for_user", new=AsyncMock(
        return_value=SimpleNamespace(id=uuid.uuid4())
    )), patch.object(analytics, "_follower_count", new=live):
        out = await analytics.get_follower_counts(
            team_id=uuid.uuid4(), days=7, current_user=MagicMock(), db=db
        )

    live.assert_not_called()
    by_account = {s.account: s for s in out}
    assert by_account["a"].current == 105
    assert by_account["a"].change == 5
    assert by_account["a"].series[0].followers == 100
    assert by_account["b"].current == 40
    assert by_account["b"].change == 0
    assert len(by_account["b"].series) == 2


@pytest.mark.asyncio
async def test_endpoint_live_fallback_only_for_never_synced():
    fresh = SimpleNamespace(id=uuid.uuid4(), platform="threads", username="c")
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        _result(scalars=[fresh]),
        _result(rows=[]),
        _result(rows=[]),
    ])
    live = AsyncMock(return_value=12)
    with patch.object(analytics, "_team_for_user", new=AsyncMock(
        return_value=SimpleNamespace(id=uuid.uuid4())
    )), patch.object(analytics, "_follower_count", new=live):
        out = await analytics.get_follower_counts(
            team_id=uuid.uuid4(), days=7, current_user=MagicMock(), db=db
        )
    live.assert_awaited_once()
    assert out[0].current == 12
    assert out[0].change == 0
