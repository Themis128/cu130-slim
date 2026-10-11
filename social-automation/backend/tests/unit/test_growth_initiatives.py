"""Unit tests for app/services/growth_initiatives.py."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.growth_initiatives as G


def _res(all_=None, scalar=None):
    return SimpleNamespace(
        all=lambda: all_ or [],
        scalar_one_or_none=lambda: scalar)


def _db(*results):
    added = []
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=list(results)),
        add=added.append,
        commit=AsyncMock())
    db._added = added
    return db


# ── record_initiative_event ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_initiative_event():
    db = _db()
    ev = await G.record_initiative_event(
        db, uuid.uuid4(), "linkedin_page_invite",
        platform="linkedin", account_id=uuid.uuid4(),
        units=40, note="batch 1", credits_left=60, declined=2,
        monthly_cap=150)
    assert db._added == [ev]
    db.commit.assert_awaited_once()
    assert ev.event_type == "linkedin_page_invite"
    assert ev.meta_data == {"units": 40, "note": "batch 1",
                          "credits_left": 60, "declined": 2,
                          "monthly_cap": 150}

    # minimal — optional meta keys omitted
    ev = await G.record_initiative_event(
        db, uuid.uuid4(), "growth_initiative", platform="x")
    assert ev.meta_data == {"units": 1}


# ── follower lookups ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_followers_lookups():
    db = _db(_res(scalar=42))
    assert await G._followers_at_or_before(
        db, uuid.uuid4(), datetime.now(UTC)) == 42
    db = _db(_res(scalar=None), _res(scalar=None))
    assert await G._followers_latest(db, uuid.uuid4()) is None
    assert await G._followers_at_or_before(
        db, uuid.uuid4(), datetime.now(UTC)) is None


# ── initiative_summary ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initiative_summary_empty():
    db = _db(_res(all_=[]), _res(all_=[]))
    assert await G.initiative_summary(db, uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_initiative_summary_no_account():
    row = SimpleNamespace(
        event_type="growth_initiative", platform="linkedin",
        social_account_id=None, events=2, units=30,
        units_this_month=30, declined=0,
        first_at=datetime.now(UTC), last_at=datetime.now(UTC))
    db = _db(_res(all_=[row]), _res(all_=[]))
    out = await G.initiative_summary(db, uuid.uuid4())
    i = out[0]
    assert i["initiative"] == "Custom growth initiative"
    assert i["followers_start"] is None and i["accepted_est"] is None
    assert i["pending_est"] is None and i["conversion_pct"] is None
    # non-linkedin type → no credit math, cap shown anyway
    assert i["monthly_cap"] == G.DEFAULT_MONTHLY_CREDIT_CAP
    assert i["credits_left"] is None


@pytest.mark.asyncio
async def test_initiative_summary_with_followers():
    acct = uuid.uuid4()
    row = SimpleNamespace(
        event_type="linkedin_page_invite", platform="linkedin",
        social_account_id=acct, events=1, units=50,
        units_this_month=50, declined=5,
        first_at=datetime.now(UTC) - timedelta(days=2),
        last_at=datetime.now(UTC))
    db = _db(
        _res(all_=[row]),
        _res(all_=[("linkedin_page_invite", {"units": 50})]),
        _res(scalar=300),   # followers at start
        _res(scalar=330),   # followers now
    )
    out = await G.initiative_summary(db, uuid.uuid4())
    i = out[0]
    assert i["followers_start"] == 300 and i["followers_now"] == 330
    assert i["followers_delta"] == 30
    # accepted = min(delta, units) = 30; conv = 60%; pending = 50-30-5
    assert i["accepted_est"] == 30 and i["conversion_pct"] == 60.0
    assert i["pending_est"] == 15
    # credits_left absent in meta → computed: cap - (units_month - accepted)
    assert i["credits_left"] == 100 - (50 - 30)


@pytest.mark.asyncio
async def test_initiative_summary_meta_overrides():
    acct = uuid.uuid4()
    row = SimpleNamespace(
        event_type="linkedin_page_invite", platform="linkedin",
        social_account_id=acct, events=2, units=10,
        units_this_month=10, declined=0,
        first_at=datetime.now(UTC), last_at=datetime.now(UTC))
    # meta carries credits_left + monthly_cap — used directly, no math
    db = _db(
        _res(all_=[row]),
        _res(all_=[
            ("linkedin_page_invite", {"units": 10}),
            ("linkedin_page_invite",
             {"units": 10, "credits_left": 42, "monthly_cap": 250}),
        ]),
        _res(scalar=None),  # no snapshot → all follower fields None
        _res(scalar=None),
    )
    out = await G.initiative_summary(db, uuid.uuid4())
    i = out[0]
    assert i["credits_left"] == 42 and i["monthly_cap"] == 250
    assert i["accepted_est"] is None and i["credits_left"] == 42
    assert i["social_account_id"] == str(acct)


@pytest.mark.asyncio
async def test_initiative_summary_accepted_capped_and_sort():
    acct = uuid.uuid4()
    r1 = SimpleNamespace(
        event_type="linkedin_page_invite", platform="linkedin",
        social_account_id=acct, events=1, units=10,
        units_this_month=10, declined=0,
        first_at=datetime(2026, 9, 1, tzinfo=UTC),
        last_at=datetime(2026, 9, 2, tzinfo=UTC))
    r2 = SimpleNamespace(
        event_type="growth_initiative", platform="x",
        social_account_id=None, events=1, units=5,
        units_this_month=5, declined=0,
        first_at=datetime(2026, 9, 5, tzinfo=UTC),
        last_at=datetime(2026, 9, 10, tzinfo=UTC))
    db = _db(
        _res(all_=[r1, r2]),
        _res(all_=[]),
        _res(scalar=100), _res(scalar=200),  # delta 100 > units 10 → capped
    )
    out = await G.initiative_summary(db, uuid.uuid4())
    # sorted by last_at desc → growth_initiative first
    assert out[0]["event_type"] == "growth_initiative"
    li = out[1]
    assert li["accepted_est"] == 10  # capped at units
    assert li["conversion_pct"] == 100.0
    assert li["pending_est"] == 0
    # delta exceeded units → credits never go below 0
    assert li["credits_left"] == 100  # 100 - (10-10)
