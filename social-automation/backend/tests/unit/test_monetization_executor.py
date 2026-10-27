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


@pytest.mark.asyncio
async def test_schedule_video_only_platform_drafts_without_media_gen(monkeypatch):
    """TikTok/video actions must not schedule an image post — the platform
    only accepts video, so the executor drafts with an explicit reason and
    never calls the image pipeline."""
    acc = _account("tiktok")
    db = _SeqDb([
        _Res([SimpleNamespace(id=uuid.uuid4())]),  # owner
        _Res([acc]),                                # _resolve_account
    ])
    media = AsyncMock(return_value=uuid.uuid4())
    monkeypatch.setattr(X, "_generate_copy", AsyncMock(return_value=("copy", [], 90)))
    monkeypatch.setattr(X, "_generate_media", media)
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=(None, None, "")),
    )
    out = await X.schedule_report_actions(
        db, _team(), [MonetizationAction("tiktok", "short clip", "20:00", "video")],
        "2026-10-27",
    )
    assert out[0]["status"] == "drafted"
    assert "video" in out[0]["reason"]
    media.assert_not_called()  # image pipeline never invoked for video-only
    post = next(o for o in db.added if isinstance(o, Post))
    assert post.status == PostStatus.DRAFT
    assert post.media_ids == []


def test_resolve_slot_target_date_pins_day():
    """target_date puts the slot on that Athens day even when today's same
    hour hasn't passed yet (strategy brief = tomorrow's playbook)."""
    now = datetime.now(UTC).astimezone(ATHENS).replace(
        hour=8, minute=0, second=0, microsecond=0
    ).astimezone(UTC)
    tomorrow = (now.astimezone(ATHENS) + timedelta(days=1)).date()
    a = MonetizationAction("linkedin", "post", "09:30", "image")
    slot = X._resolve_slot(a, now, target_date=tomorrow).astimezone(ATHENS)
    assert slot.date() == tomorrow
    assert (slot.hour, slot.minute) == (9, 30)


@pytest.mark.asyncio
async def test_schedule_source_and_dedup_field_tagging(monkeypatch):
    """Non-default source/dedup_field let other reports (strategy brief)
    share the executor without colliding on monetization keys."""
    acc = _account("linkedin")
    db = _SeqDb([
        _Res([SimpleNamespace(id=uuid.uuid4())]),
        _Res([acc]),
    ])
    monkeypatch.setattr(X, "_generate_copy", AsyncMock(return_value=("copy", ["#a"], 91)))
    monkeypatch.setattr(X, "_generate_media", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=(None, None, "")),
    )
    out = await X.schedule_report_actions(
        db, _team(), [MonetizationAction("linkedin", "B2B post", "23:00", "image")],
        "2026-10-27", source="strategy-brief", dedup_field="strategy_action",
    )
    assert out[0]["status"] == "scheduled"
    post = next(o for o in db.added if isinstance(o, Post))
    assert post.meta_data["source"] == "strategy-brief"
    assert "strategy_action" in post.meta_data
    assert "monetization_action" not in post.meta_data


@pytest.mark.asyncio
async def test_schedule_max_actions_param(monkeypatch):
    """max_actions caps the run — strategy briefs allow more than the
    monetization default of 4."""
    acc = _account("linkedin")
    db = _SeqDb([
        _Res([SimpleNamespace(id=uuid.uuid4())]),
        _Res([acc]),
        _Res([acc]),
    ])
    monkeypatch.setattr(X, "_generate_copy", AsyncMock(return_value=("copy", [], 90)))
    monkeypatch.setattr(X, "_generate_media", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=(None, None, "")),
    )
    actions = [
        MonetizationAction("linkedin", f"distinct post number {i}", f"1{i}:00", "text")
        for i in range(6)
    ]
    out = await X.schedule_report_actions(
        db, _team(), actions, "2026-10-27", max_actions=2,
    )
    assert len(out) == 2


@pytest.mark.asyncio
async def test_schedule_slot_conflict_hops_and_low_compliance_drafts(monkeypatch):
    """Taken slots shift +30min (covers hop loop); compliance <3 drops to
    DRAFT even with media (covers the compliance gate branch)."""
    acc = _account("linkedin")
    db = _SeqDb([
        _Res([SimpleNamespace(id=uuid.uuid4())]),  # owner
        _Res([acc]),
    ])
    monkeypatch.setattr(X, "_generate_copy", AsyncMock(return_value=("copy", [], 91)))
    monkeypatch.setattr(X, "_generate_media", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=({"name": "b"}, {"v": 1}, "ctx")),
    )
    monkeypatch.setattr(
        "app.services.brand_compliance.score_brand_compliance",
        AsyncMock(return_value={"score": 2.0}),
    )
    hops = iter([True, False])  # first slot taken, second free
    requested: list[datetime] = []

    async def _taken(*a, **k):
        requested.append(a[3])  # the `when` arg _slot_taken probed
        return next(hops)

    monkeypatch.setattr(X, "_slot_taken", _taken)
    out = await X.schedule_report_actions(
        db, _team(), [MonetizationAction("linkedin", "post", "23:00", "image")],
        "2026-10-27",
    )
    assert out[0]["status"] == "drafted"
    assert "compliance" in out[0]["reason"]
    post = next(o for o in db.added if isinstance(o, Post))
    assert post.status == PostStatus.DRAFT
    # slot-hop evidence: the second probe must be exactly +30min after the
    # first, and the entry records the accepted (shifted) slot — drafted
    # posts store no scheduled_at, so this is the observable proof.
    assert len(requested) == 2
    assert requested[1] - requested[0] == timedelta(minutes=30)
    assert out[0]["scheduled_at"] == requested[1].astimezone(ATHENS).strftime(
        "%Y-%m-%d %H:%M %Z"
    )


def test_action_hash_stable():
    a = MonetizationAction("LinkedIn", "  Post X  ", "12:00", "text")
    # platform is stored lowercase by the report parser; hash must be stable
    a.platform = a.platform.lower()
    assert X._action_hash("2026-10-13", a) == X._action_hash("2026-10-13", a)
    b = MonetizationAction("linkedin", "post x", "12:00", "text")
    assert X._action_hash("2026-10-13", a) == X._action_hash("2026-10-13", b)
    assert X._action_hash("2026-10-14", a) != X._action_hash("2026-10-13", a)
