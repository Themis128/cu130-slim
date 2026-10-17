"""Tests for app/services/monetization_executor.py — report→queue bridge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import app.services.monetization_executor as X
from app.models.content import Post, PostStatus
from app.services.monetization_report import MonetizationAction

ATHENS = ZoneInfo("Europe/Athens")


def _account(platform="linkedin", account_type="person", username="me"):
    return SimpleNamespace(
        id=uuid.uuid4(), platform=platform, account_type=account_type,
        username=username, account_id="acc123", status="active",
        meta_data={}, access_token_enc=b"x",
    )


def _team():
    return SimpleNamespace(id=uuid.uuid4(), name="Cloudless", owner_id=uuid.uuid4())


# ── slot resolution ───────────────────────────────────────────────────────


def test_resolve_slot_today_when_time_ahead():
    # freeze "now" to 08:00 Athens
    now = datetime.now(UTC).astimezone(ATHENS).replace(
        hour=8, minute=0, second=0, microsecond=0
    ).astimezone(UTC)
    a = MonetizationAction("linkedin", "post", "12:00", "image")
    slot = X._resolve_slot(a, now)
    local = slot.astimezone(ATHENS)
    assert (local.hour, local.minute) == (12, 0)
    assert local.date() == now.astimezone(ATHENS).date()


def test_resolve_slot_rolls_to_tomorrow_when_passed():
    now = datetime.now(UTC).astimezone(ATHENS).replace(
        hour=20, minute=0, second=0, microsecond=0
    ).astimezone(UTC)
    a = MonetizationAction("linkedin", "post", "09:30", "image")
    slot = X._resolve_slot(a, now)
    assert slot.astimezone(ATHENS).date() == (
        now.astimezone(ATHENS) + timedelta(days=1)
    ).date()


def test_resolve_slot_dead_night_remaps_to_baseline():
    now = datetime.now(UTC)
    a = MonetizationAction("linkedin", "post", "03:00", "image")
    slot = X._resolve_slot(a, now)
    local = slot.astimezone(ATHENS)
    assert local.hour == 9 and local.minute == 30  # linkedin baseline


def test_resolve_slot_missing_time_uses_platform_baseline():
    now = datetime.now(UTC)
    a = MonetizationAction("tiktok", "post", "", "video")
    local = X._resolve_slot(a, now).astimezone(ATHENS)
    assert (local.hour, local.minute) == (20, 0)


def test_resolve_slot_prefers_learned_hour_over_baseline():
    now = datetime.now(UTC)
    a = MonetizationAction("tiktok", "post", "", "video")
    local = X._resolve_slot(a, now, learned_hours={"tiktok": 15}).astimezone(ATHENS)
    assert (local.hour, local.minute) == (15, 0)


def test_resolve_slot_explicit_time_beats_learned():
    now = datetime.now(UTC)
    a = MonetizationAction("tiktok", "post", "21:00", "video")
    local = X._resolve_slot(a, now, learned_hours={"tiktok": 15}).astimezone(ATHENS)
    assert (local.hour, local.minute) == (21, 0)


# ── account resolution ────────────────────────────────────────────────────


class _Res:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items

    def first(self):
        return self._items[0] if self._items else None


class _Db:
    def __init__(self, accounts):
        self._accounts = accounts

    async def execute(self, stmt):
        return _Res(self._accounts)


@pytest.mark.asyncio
async def test_resolve_account_single():
    acc = _account("instagram")
    out = await X._resolve_account(_Db([acc]), uuid.uuid4(), "instagram", "x")
    assert out is acc


@pytest.mark.asyncio
async def test_resolve_account_none_when_no_accounts():
    out = await X._resolve_account(_Db([]), uuid.uuid4(), "tiktok", "x")
    assert out is None


@pytest.mark.asyncio
async def test_resolve_account_linkedin_corporate_vs_personal():
    personal = _account("linkedin", "person", "founder")
    org = _account("linkedin", "organization", "cloudless-gr")
    db = _Db([personal, org])
    corp = await X._resolve_account(
        db, uuid.uuid4(), "linkedin", "Post to the company page about audit"
    )
    assert corp is org
    founder = await X._resolve_account(
        db, uuid.uuid4(), "linkedin", "Founder story about shipping fast"
    )
    assert founder is personal
    # Brand copy that only says "Cloudless" — no corporate keywords — must
    # still route to the Company Page (brand posts → business accounts).
    brand = await X._resolve_account(
        db, uuid.uuid4(), "linkedin",
        "At Cloudless, we build websites, cloud systems, and AI tools",
    )
    assert brand is org


@pytest.mark.asyncio
async def test_resolve_account_facebook_person_vs_page():
    person = _account("facebook", "user", "themis")
    page = _account("facebook", "page", "cloudless.gr")
    db = _Db([page, person])
    assert (await X._resolve_account(db, uuid.uuid4(), "facebook", "raw build log")) is person
    assert (await X._resolve_account(db, uuid.uuid4(), "facebook", "brand audit CTA")) is page


# ── end-to-end scheduling ─────────────────────────────────────────────────


class _SeqDb:
    """db stub returning canned results in call order; records adds/commits."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def execute(self, stmt):
        return self._results.pop(0) if self._results else _Res([])

    async def scalar(self, stmt):
        return self._results.pop(0) if self._results else 0


@pytest.mark.asyncio
async def test_schedule_skips_messaging_and_missing_accounts():
    db = _SeqDb([_Res([SimpleNamespace(id=uuid.uuid4())]),   # owner
                 _Res([]),                                   # whatsapp never reaches account q
                 _Res([])])                                  # tiktok: no account
    team = _team()
    out = await X.schedule_report_actions(
        db, team,
        [MonetizationAction("whatsapp", "send broadcast", "10:00", "text"),
         MonetizationAction("tiktok", "video post", "20:00", "video")],
        "2026-10-13",
    )
    assert out[0]["status"] == "skipped" and "messaging" in out[0]["reason"]
    assert out[1]["status"] == "skipped" and out[1]["reason"] == "no active account"


@pytest.mark.asyncio
async def test_schedule_full_path_creates_scheduled_post(monkeypatch):
    acc = _account("linkedin")
    db = _SeqDb([
        _Res([SimpleNamespace(id=uuid.uuid4())]),  # owner
        _Res([acc]),                                # _resolve_account
        # existing-hash count → db.scalar returns 0 via default
    ])
    monkeypatch.setattr(X, "_generate_copy", AsyncMock(return_value=("copy", ["#a"], 91)))
    monkeypatch.setattr(X, "_generate_media", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=(None, None, "")),
    )
    team = _team()
    out = await X.schedule_report_actions(
        db, team, [MonetizationAction("linkedin", "B2B post", "23:00", "image")],
        "2026-10-13",
    )
    assert out[0]["status"] == "scheduled"
    post = next(o for o in db.added if isinstance(o, Post))
    assert post.status == PostStatus.SCHEDULED
    assert post.scheduled_at is not None
    assert post.media_ids  # never posts without media
    assert post.meta_data["source"] == "monetization-report"


@pytest.mark.asyncio
async def test_schedule_drafts_when_media_fails(monkeypatch):
    acc = _account("instagram")
    db = _SeqDb([
        _Res([SimpleNamespace(id=uuid.uuid4())]),
        _Res([acc]),
    ])
    monkeypatch.setattr(X, "_generate_copy", AsyncMock(return_value=("copy", [], 80)))
    monkeypatch.setattr(X, "_generate_media", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=(None, None, "")),
    )
    out = await X.schedule_report_actions(
        db, _team(),
        [MonetizationAction("instagram", "grid post", "23:55", "image")],
        "2026-10-13",
    )
    assert out[0]["status"] == "drafted"
    assert "media" in out[0]["reason"]


@pytest.mark.asyncio
async def test_schedule_idempotent_skips_repeat(monkeypatch):
    acc = _account("linkedin")
    db = _SeqDb([
        _Res([SimpleNamespace(id=uuid.uuid4())]),
        _Res([acc]),
    ])

    # db.scalar must return 1 for the hash-existence check
    async def scalar_one(stmt):
        return 1
    db.scalar = scalar_one  # type: ignore[assignment]
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=(None, None, "")),
    )
    out = await X.schedule_report_actions(
        db, _team(), [MonetizationAction("linkedin", "post", "12:00", "text")],
        "2026-10-13",
    )
    assert out[0]["status"] == "skipped" and out[0]["reason"] == "already scheduled"


def test_action_hash_stable():
    a = MonetizationAction("LinkedIn", "  Post X  ", "12:00", "text")
    # platform is stored lowercase by the report parser; hash must be stable
    a.platform = a.platform.lower()
    assert X._action_hash("2026-10-13", a) == X._action_hash("2026-10-13", a)
    b = MonetizationAction("linkedin", "post x", "12:00", "text")
    assert X._action_hash("2026-10-13", a) == X._action_hash("2026-10-13", b)
    assert X._action_hash("2026-10-14", a) != X._action_hash("2026-10-13", a)
