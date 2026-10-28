"""Unit tests for recurring post scheduling logic."""
from datetime import UTC, datetime, timedelta

from app.models.content import RecurrencePattern
from app.worker.tasks.recurring import _next_recurrence_dt


def test_next_recurrence_daily():
    base = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    result = _next_recurrence_dt(RecurrencePattern.DAILY, 3, base)
    assert result == base + timedelta(days=3)


def test_next_recurrence_weekly():
    base = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    result = _next_recurrence_dt(RecurrencePattern.WEEKLY, 2, base)
    assert result == base + timedelta(weeks=2)


def test_next_recurrence_monthly():
    base = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    result = _next_recurrence_dt(RecurrencePattern.MONTHLY, 1, base)
    assert result == base + timedelta(days=30)


def test_next_recurrence_none_returns_base():
    base = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    result = _next_recurrence_dt(RecurrencePattern.NONE, 1, base)
    assert result == base


def test_recurrence_pattern_values():
    assert RecurrencePattern.NONE.value == "none"
    assert RecurrencePattern.DAILY.value == "daily"
    assert RecurrencePattern.WEEKLY.value == "weekly"
    assert RecurrencePattern.MONTHLY.value == "monthly"
