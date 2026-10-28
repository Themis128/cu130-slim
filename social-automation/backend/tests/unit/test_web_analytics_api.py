"""Tests for app/api/web_analytics.py — config CRUD, webhook ingest, summary."""
from __future__ import annotations

import hashlib
import hmac
import uuid
from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.web_analytics as wa


class _Res:
    def __init__(self, *, scalar=None, one_or_none=None, rows=None, scalars=None):
        self._scalar = scalar
        self._one_or_none = one_or_none
        self._rows = rows
        self._scalars = scalars

    def scalar_one_or_none(self):
        return self._one_or_none

    def scalar(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars or [])

    def all(self):
        return self._rows or []


class _DB:
    def __init__(self, results, *, get_result=None):
        self._q = deque(results)
        self._get = get_result
        self.added = []
        self.deleted = []

    async def execute(self, q):
        return self._q.popleft()

    async def get(self, model, key):
        return self._get

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        now = datetime.now(UTC)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        for attr in ("created_at", "updated_at"):
            if getattr(obj, attr, None) is None:
                setattr(obj, attr, now)

    async def delete(self, obj):
        self.deleted.append(obj)


def _user():
    return SimpleNamespace(id=uuid.uuid4())


def _team():
    return SimpleNamespace(id=uuid.uuid4())


def _config(**kw):
    base = dict(
        id=uuid.uuid4(), team_id=uuid.uuid4(), domain="cloudless.gr",
        webhook_secret="sec", ga4_enabled=False, ga4_measurement_id=None,
        ga4_api_secret=None, plausible_enabled=False, plausible_domain=None,
        plausible_api_url=None, plausible_api_key=None, meta_capi_enabled=False,
        meta_pixel_id=None, meta_capi_access_token=None, meta={},
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _body(**kw):
    base = dict(
        domain="new.io", webhook_secret=None, ga4_enabled=False,
        ga4_measurement_id=None, ga4_api_secret=None, plausible_enabled=False,
        plausible_domain=None, plausible_api_url=None, plausible_api_key=None,
        meta_capi_enabled=False, meta_pixel_id=None,
        meta_capi_access_token=None, meta=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── helpers ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_team_for_user():
    team = _team()
    db = _DB([], get_result=team)
    assert await wa._get_team_for_user(db, uuid.uuid4()) is team
    db2 = _DB([], get_result=None)
    with pytest.raises(HTTPException) as e:
        await wa._get_team_for_user(db2, uuid.uuid4())
    assert e.value.status_code == 404


def test_verify_webhook_signature():
    body = b'{"e":1}'
    sig = hmac.new(b"sec", body, hashlib.sha256).hexdigest()
    assert wa._verify_webhook_signature(body, "sec", sig)
    assert wa._verify_webhook_signature(body, "sec", f"sha256={sig}")
    assert not wa._verify_webhook_signature(body, "sec", None)
    assert not wa._verify_webhook_signature(body, "sec", "bad")


def test_env_fallback_config(monkeypatch):
    tid = str(uuid.uuid4())
    settings = SimpleNamespace(
        CLOUDLESS_WEB_ANALYTICS_SECRET="sec",
        CLOUDLESS_WEB_ANALYTICS_TEAM_ID=tid,
        CLOUDLESS_WEB_ANALYTICS_DOMAIN="cloudless.gr",
        GA4_MEASUREMENT_ID="G-1", GA4_API_SECRET="gs",
        PLAUSIBLE_DOMAIN="p.io", PLAUSIBLE_API_URL="u", PLAUSIBLE_API_KEY="k",
        META_PIXEL_ID="px", META_CAPI_ACCESS_TOKEN="mt",
    )
    monkeypatch.setattr(wa, "settings", settings)
    cfg = wa._env_fallback_config("cloudless.gr")
    assert cfg is not None and cfg.team_id == uuid.UUID(tid)
    assert cfg.ga4_enabled and cfg.plausible_enabled and cfg.meta_capi_enabled

    # wrong domain
    assert wa._env_fallback_config("other.io") is None
    # missing secret
    monkeypatch.setattr(wa, "settings", SimpleNamespace(
        **{**settings.__dict__, "CLOUDLESS_WEB_ANALYTICS_SECRET": ""}))
    assert wa._env_fallback_config("cloudless.gr") is None
    # bad team id
    monkeypatch.setattr(wa, "settings", SimpleNamespace(
        **{**settings.__dict__, "CLOUDLESS_WEB_ANALYTICS_TEAM_ID": "not-uuid"}))
    assert wa._env_fallback_config("cloudless.gr") is None


# ── webhook ──────────────────────────────────────────────────────────


class _Req:
    def __init__(self, body=b"body", sig="sig", ua="ua", ip="1.2.3.4"):
        self._body = body
        self.headers = {"x-webhook-signature": sig, "user-agent": ua}
        self.client = SimpleNamespace(host=ip) if ip else None

    async def body(self):
        return self._body


@pytest.mark.asyncio
async def test_receive_event_missing_signature():
    req = SimpleNamespace(headers={}, body=None, client=None)
    data = SimpleNamespace(domain="d")
    with pytest.raises(HTTPException) as e:
        await wa.receive_cloudless_event(req, data, _DB([]))
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_receive_event_domain_not_configured(monkeypatch):
    monkeypatch.setattr(wa, "_env_fallback_config", lambda d: None)
    req = _Req()
    data = SimpleNamespace(domain="unknown.io")
    db = _DB([_Res(one_or_none=None)])
    with pytest.raises(HTTPException) as e:
        await wa.receive_cloudless_event(req, data, db)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_receive_event_bad_signature(monkeypatch):
    monkeypatch.setattr(wa, "_verify_webhook_signature", lambda b, s, sig: False)
    req = _Req()
    data = SimpleNamespace(domain="d.io")
    db = _DB([_Res(one_or_none=_config())])
    with pytest.raises(HTTPException) as e:
        await wa.receive_cloudless_event(req, data, db)
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_receive_event_ok(monkeypatch):
    monkeypatch.setattr(wa, "_verify_webhook_signature", lambda b, s, sig: True)
    event = SimpleNamespace(id=uuid.uuid4(), event_name="page_view", forwarded={})
    monkeypatch.setattr(wa, "ingest_event", AsyncMock(return_value=event))
    cfg = _config()
    db = _DB([_Res(one_or_none=cfg)])
    data = SimpleNamespace(
        domain="d.io", event="page_view", payload={}, session_id=None,
        visitor_id=None, path="/", referrer=None, locale="en",
    )
    out = await wa.receive_cloudless_event(_Req(ip=None), data, db)
    assert out.event_name == "page_view"
    assert db.added == [event]


# ── config CRUD ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_configs():
    c1, c2 = _config(), _config()
    team = _team()
    db = _DB([_Res(scalars=[c1, c2])], get_result=team)
    out = await wa.list_configs(uuid.uuid4(), _user(), db)
    assert len(out) == 2


@pytest.mark.asyncio
async def test_create_config_conflict_and_ok():
    team = _team()
    db = _DB([_Res(one_or_none=_config())], get_result=team)
    with pytest.raises(HTTPException) as e:
        await wa.create_config(_body(), uuid.uuid4(), _user(), db)
    assert e.value.status_code == 409

    db2 = _DB([_Res(one_or_none=None)], get_result=team)
    await wa.create_config(
        _body(webhook_secret="mine", meta={"a": 1}), uuid.uuid4(), _user(), db2)
    assert db2.added and db2.added[0].webhook_secret == "mine"

    # generated secret path
    db3 = _DB([_Res(one_or_none=None)], get_result=team)
    await wa.create_config(_body(), uuid.uuid4(), _user(), db3)
    assert db3.added[0].webhook_secret  # sha256 fallback


@pytest.mark.asyncio
async def test_update_config_404_and_ok():
    team = _team()
    db = _DB([_Res(one_or_none=None)], get_result=team)
    with pytest.raises(HTTPException) as e:
        await wa.update_config("cid", _body(), uuid.uuid4(), _user(), db)
    assert e.value.status_code == 404

    cfg = _config(webhook_secret="old")
    db2 = _DB([_Res(one_or_none=cfg)], get_result=team)
    await wa.update_config(
        "cid", _body(domain="new.io", webhook_secret="newsec", meta={"k": "v"}),
        uuid.uuid4(), _user(), db2)
    assert cfg.webhook_secret == "newsec" and cfg.domain == "new.io"
    assert cfg.meta == {"k": "v"} and cfg.updated_at is not None

    # no secret in body -> keeps old
    cfg2 = _config(webhook_secret="keep")
    db3 = _DB([_Res(one_or_none=cfg2)], get_result=team)
    await wa.update_config("cid", _body(), uuid.uuid4(), _user(), db3)
    assert cfg2.webhook_secret == "keep"


@pytest.mark.asyncio
async def test_delete_config_404_and_ok():
    team = _team()
    db = _DB([_Res(one_or_none=None)], get_result=team)
    with pytest.raises(HTTPException):
        await wa.delete_config("cid", uuid.uuid4(), _user(), db)

    cfg = _config()
    db2 = _DB([_Res(one_or_none=cfg)], get_result=team)
    await wa.delete_config("cid", uuid.uuid4(), _user(), db2)
    assert db2.deleted == [cfg]


@pytest.mark.asyncio
async def test_get_summary():
    team = _team()
    db = _DB([
        _Res(scalar=3),
        _Res(rows=[("page_view", 2), ("click", 1)]),
    ], get_result=team)
    out = await wa.get_summary(uuid.uuid4(), 30, _user(), db)
    assert out.total_events == 3
    assert out.top_events[0].event_name == "page_view"
    assert out.top_events[0].count == 2
