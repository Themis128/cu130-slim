"""Unit tests for the token-refresh skip decision (pure helper, no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.worker.tasks.token_refresh import (
    MIN_REFRESH_INTERVAL,
    _skip_for_recent_update,
)

NOW = datetime(2026, 9, 23, 6, 15, 0, tzinfo=UTC)


def test_expired_token_never_skipped():
    """A dead token must always be retried — updated_at is bumped by
    unrelated writes and must not starve it."""
    expired = NOW - timedelta(hours=1)
    recent_update = NOW - timedelta(minutes=5)
    assert _skip_for_recent_update(expired, recent_update, NOW) is False


def test_token_expiring_within_cycle_not_skipped():
    """The 2026-09-23 regression: a token with 1.2s of validity left was
    skipped because an unrelated write bumped updated_at — it then 401'd
    for ~1h until the next run. Expiring-before-next-run must refresh now."""
    expires_in_one_second = NOW + timedelta(seconds=1.2)
    recent_update = NOW - timedelta(minutes=9)
    assert _skip_for_recent_update(expires_in_one_second, recent_update, NOW) is False


def test_token_expiring_just_before_next_run_not_skipped():
    expires_soon = NOW + MIN_REFRESH_INTERVAL - timedelta(minutes=1)
    recent_update = NOW - timedelta(minutes=30)
    assert _skip_for_recent_update(expires_soon, recent_update, NOW) is False


def test_healthy_recently_refreshed_token_skipped():
    """Normal case: token refreshed this hour, valid for hours → skip."""
    expires_later = NOW + timedelta(hours=2)
    recent_update = NOW - timedelta(minutes=30)
    assert _skip_for_recent_update(expires_later, recent_update, NOW) is True


def test_healthy_token_old_update_not_skipped():
    """Recently-updated is the throttle — an untouched account refreshes."""
    expires_later = NOW + timedelta(hours=2)
    old_update = NOW - timedelta(hours=2)
    assert _skip_for_recent_update(expires_later, old_update, NOW) is False


def test_no_expiry_not_skipped():
    assert _skip_for_recent_update(None, NOW - timedelta(minutes=5), NOW) is False


def test_no_update_timestamp_not_skipped():
    expires_later = NOW + timedelta(hours=2)
    assert _skip_for_recent_update(expires_later, None, NOW) is False


def test_naive_updated_at_treated_as_utc():
    expires_later = NOW + timedelta(hours=2)
    naive_recent = (NOW - timedelta(minutes=10)).replace(tzinfo=None)
    assert _skip_for_recent_update(expires_later, naive_recent, NOW) is True
