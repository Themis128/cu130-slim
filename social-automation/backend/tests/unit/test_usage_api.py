"""Tests for app/api/usage.py — usage + 12-month history endpoints."""
from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.usage import _month_start, get_usage, get_usage_history


class _Res:
    def __init__(self, *, scalar=None, one_or_none=None, rows=None):
        self._scalar = scalar
        self._one_or_none = one_or_none
        self._rows = rows or []

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._one_or_none

    def __iter__(self):
        return iter(self._rows)


class _DB:
    def __init__(self, results):
        self._q = deque(results)

    async def execute(self, q):
        return self._q.popleft()


def test_month_start():
    out = _month_start(datetime(2026, 5, 17, 13, 45, 30, tzinfo=UTC))
    assert out == datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_get_usage():
    db = _DB([
        _Res(one_or_none="pro"),
        _Res(scalar=7),
        _Res(scalar=12),
        _Res(scalar=3),
    ])
    out = await get_usage(uuid.uuid4(), SimpleNamespace(), db)
    assert out["plan_tier"] == "pro"
    assert out["usage"]["ai_calls_per_month"]["used"] == 7
    assert out["usage"]["posts_per_month"]["used"] == 12
    assert out["usage"]["social_accounts"]["used"] == 3
    assert out["usage"]["posts_per_month"]["limit"] > 0


@pytest.mark.asyncio
async def test_get_usage_unknown_team_defaults_free():
    db = _DB([
        _Res(one_or_none=None),
        _Res(scalar=0),
        _Res(scalar=0),
        _Res(scalar=0),
    ])
    out = await get_usage(uuid.uuid4(), SimpleNamespace(), db)
    assert out["plan_tier"] == "free"


@pytest.mark.asyncio
async def test_get_usage_history():
    now = datetime.now(UTC)
    post_row = SimpleNamespace(y=now.year, m=now.month, cnt=4)
    ai_row = SimpleNamespace(y=now.year, m=now.month, cnt=9)
    db = _DB([_Res(rows=[post_row]), _Res(rows=[ai_row])])
    out = await get_usage_history(uuid.uuid4(), SimpleNamespace(), db)
    assert len(out) == 12
    this_month = f"{now.year:04d}-{now.month:02d}"
    last = out[-1]
    assert last["month"] == this_month
    assert last["posts"] == 4
    assert last["ai_calls"] == 9
    assert all(entry["month"].count("-") == 1 for entry in out)


@pytest.mark.asyncio
async def test_get_usage_history_year_boundary():
    # Months before January roll into the previous year — verify months
    # list construction produces 12 sequential months.
    db = _DB([_Res(rows=[]), _Res(rows=[])])
    out = await get_usage_history(uuid.uuid4(), SimpleNamespace(), db)
    assert len(out) == 12
    parsed = [tuple(map(int, e["month"].split("-"))) for e in out]
    # strictly increasing (year, month) tuples, wrapping at Dec->Jan
    for (y1, m1), (y2, m2) in zip(parsed, parsed[1:]):
        assert (y2, m2) == ((y1, m1 + 1) if m1 < 12 else (y1 + 1, 1))
