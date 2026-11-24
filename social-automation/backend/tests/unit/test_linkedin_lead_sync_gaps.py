"""Coverage for linkedin_lead_sync webhook edge paths."""

import hashlib as _hl
import hmac as _hm
import json as _json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import linkedin_lead_sync as LSYNC


def _sig(secret: str, body: bytes) -> str:
    return _hm.new(secret.encode(), b"hmacsha256=" + body, _hl.sha256).hexdigest()


class _Req:
    def __init__(self, body: bytes, sig: str = "", query: dict | None = None):
        self._body = body
        self.headers = {"x-li-signature": sig} if sig else {}
        self.query_params = query or {}

    async def body(self):
        return self._body


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *a):
        return False


class _DB:
    async def execute(self, q):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def add(self, obj):
        pass

    async def commit(self):
        pass


def _payload(**kw):
    base = {
        "type": "LEAD_ACTION",
        "leadGenFormResponse": "urn:li:leadGenFormResponse:resp-1",
        "leadAction": "CREATED",
        "occurredAt": 1700000000000,
        "leadType": "SPONSORED",
    }
    base.update(kw)
    return base


def _wire(monkeypatch, route=None, fetch=None):
    import app.worker.tasks.linkedin_lead_boost as LB

    monkeypatch.setattr(LB, "route_lead_response", route or AsyncMock(return_value=True))
    monkeypatch.setattr(LSYNC, "_fetch_response", fetch or AsyncMock(return_value={"id": "r"}))
    monkeypatch.setattr("app.worker._async.task_session", lambda: _AsyncCM(_DB()))
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock())


# ── challenge + signature helpers ───────────────────────────────────────


@pytest.mark.asyncio
async def test_challenge_missing_code_or_secret(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)
    req = SimpleNamespace(query_params={"challengeCode": "x"})
    out = await LSYNC.lead_sync_challenge(req)
    assert out.status_code == 400
    req2 = SimpleNamespace(query_params={})
    out2 = await LSYNC.lead_sync_challenge(req2)
    assert out2.status_code == 400


def test_signature_ok_dev_accept_without_secret(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)
    req = SimpleNamespace(headers={})
    assert LSYNC._signature_ok(req, b"{}") is True
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "shh")
    assert LSYNC._signature_ok(SimpleNamespace(headers={}), b"{}") is False
    good = _sig("shh", b"{}")
    assert LSYNC._signature_ok(SimpleNamespace(headers={"x-li-signature": good}), b"{}") is True


# ── _response_ids filters ───────────────────────────────────────────────


def test_response_ids_skips_non_dict_and_wrong_type():
    items = LSYNC._response_ids(["junk", {"type": "OTHER"}, {"leadGenFormResponse": "urn:li:leadGenFormResponse:z"}])
    assert len(items) == 1
    assert items[0]["id"] == "z"
    # adFormResponse urns also accepted
    items2 = LSYNC._response_ids({"leadGenFormResponse": "urn:li:adFormResponse:q9", "occurredAt": 5})
    assert items2[0]["id"] == "q9"


# ── _fetch_response ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_response_ok_and_edges(monkeypatch):
    import httpx

    adv = __import__("app.services.linkedin_advanced", fromlist=["x"])
    monkeypatch.setattr(adv, "_ctx", AsyncMock(return_value=("tok", "urn:li:sponsoredAccount:1")))
    monkeypatch.setattr(adv, "REST", "https://api.linkedin.com/rest")
    monkeypatch.setattr(adv, "HDRS", {})

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return SimpleNamespace(status_code=200, json=lambda: {"results": {"r1": {"id": "r1"}}})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    out = await LSYNC._fetch_response("r1", "urn:li:sponsoredAccount:1", "SPONSORED")
    assert out == {"id": "r1"}

    # ctx missing → None
    monkeypatch.setattr(adv, "_ctx", AsyncMock(return_value=None))
    assert await LSYNC._fetch_response("r1", "u", "t") is None


@pytest.mark.asyncio
async def test_fetch_response_http_error_and_empty_results(monkeypatch):
    import httpx

    adv = __import__("app.services.linkedin_advanced", fromlist=["x"])
    monkeypatch.setattr(adv, "_ctx", AsyncMock(return_value=("tok", "urn:li:organization:1")))
    monkeypatch.setattr(adv, "REST", "https://api.linkedin.com/rest")
    monkeypatch.setattr(adv, "HDRS", {})

    class _Client:
        status = 500

        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return SimpleNamespace(status_code=_Client.status, json=lambda: {"results": {}})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert await LSYNC._fetch_response("r1", "u", "t") is None
    _Client.status = 200
    # empty results → next(iter(...), None) → None
    assert await LSYNC._fetch_response("r1", "u", "t") is None


# ── webhook edge paths ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_bad_json_and_no_lead_response(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)
    _wire(monkeypatch)
    out = await LSYNC.lead_sync_webhook(_Req(b"{not json"))
    assert b"bad-json" in out.body
    out2 = await LSYNC.lead_sync_webhook(_Req(_json.dumps({"foo": "bar"}).encode()))
    assert b"no-leadFormResponse" in out2.body


@pytest.mark.asyncio
async def test_webhook_deleted_action_and_fetch_failure(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)
    _wire(monkeypatch, fetch=AsyncMock(return_value=None))
    body = _json.dumps([_payload(leadAction="DELETED"), _payload(leadGenFormResponse="urn:li:leadGenFormResponse:r2")]).encode()
    out = await LSYNC.lead_sync_webhook(_Req(body))
    data = _json.loads(out.body)
    assert data["received"] == 2
    assert data["routed"] == 0
    assert any("fetch failed" in e for e in data["errors"])


@pytest.mark.asyncio
async def test_webhook_route_exception_recorded(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)
    _wire(monkeypatch, route=AsyncMock(side_effect=RuntimeError("route boom")))
    body = _json.dumps(_payload()).encode()
    out = await LSYNC.lead_sync_webhook(_Req(body))
    data = _json.loads(out.body)
    assert data["routed"] == 0
    # Exception detail stays in server logs; the API response is generic.
    assert any("route failed" in e for e in data["errors"])


@pytest.mark.asyncio
async def test_webhook_dedup_skips_seen(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)

    class _SeenDB(_DB):
        async def execute(self, q):
            return SimpleNamespace(scalar_one_or_none=lambda: "already")

    import app.worker.tasks.linkedin_lead_boost as LB

    monkeypatch.setattr(LB, "route_lead_response", AsyncMock(return_value=True))
    monkeypatch.setattr(LSYNC, "_fetch_response", AsyncMock(return_value={"id": "r"}))
    monkeypatch.setattr("app.worker._async.task_session", lambda: _AsyncCM(_SeenDB()))
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock())
    body = _json.dumps(_payload()).encode()
    out = await LSYNC.lead_sync_webhook(_Req(body))
    data = _json.loads(out.body)
    assert data["deduped"] == 1 and data["routed"] == 0


@pytest.mark.asyncio
async def test_webhook_route_false_not_counted(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)
    _wire(monkeypatch, route=AsyncMock(return_value=False))
    body = _json.dumps(_payload()).encode()
    out = await LSYNC.lead_sync_webhook(_Req(body))
    data = _json.loads(out.body)
    assert data["received"] == 1 and data["routed"] == 0 and data["errors"] == []
