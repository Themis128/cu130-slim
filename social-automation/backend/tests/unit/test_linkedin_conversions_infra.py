"""LinkedIn conversions infra — rule audit task + Matched Audiences sync."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import app.services.espocrm_client as ESPO
import app.services.slack_notifications as SN
from app.services import linkedin_advanced as ADV
from app.worker.tasks import linkedin_conversion_audit as CA
from app.worker.tasks import linkedin_dmp_sync as DS


class _Resp:
    def __init__(self, status=200, data=None, text=None, headers=None):
        self.status_code = status
        self._data = data if data is not None else {}
        self.text = text if text is not None else json.dumps(self._data)
        self.headers = headers or {}

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params))
        return self._handler("GET", url, params=params)

    async def post(self, url, json=None, params=None, headers=None):
        self.calls.append(("POST", url, json))
        return self._handler("POST", url, json=json, params=params)


def _rules(names=("audit_booked", "lead_created")):
    return {
        "elements": [
            {
                "name": n,
                "id": 100 + i,
                "enabled": True,
                "conversionMethod": "CONVERSIONS_API",
                "type": "LEAD",
            }
            for i, n in enumerate(names)
        ]
    }


@pytest.mark.asyncio
async def test_dmp_segment_payload_has_account_and_sourceplatform(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "urn")))
    seen = {}

    def h(method, url, **kw):
        seen["json"] = kw.get("json")
        return _Resp(201, headers={"x-restli-id": "seg-9"})

    monkeypatch.setattr(ADV.httpx, "AsyncClient", lambda *a, **k: _FakeClient(h))
    out = await ADV.create_dmp_segment("seg", "desc")
    assert out["urn"] == "seg-9"
    p = seen["json"]
    assert p["account"] == "urn:li:sponsoredAccount:512642510"
    assert p["type"] == "USER" and p["sourcePlatform"] == "DIRECT_API"


@pytest.mark.asyncio
async def test_upload_dmp_users_element_shape(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "urn")))
    seen = {}

    def h(method, url, **kw):
        seen["url"] = url
        seen["json"] = kw.get("json")
        return _Resp(204)

    monkeypatch.setattr(ADV.httpx, "AsyncClient", lambda *a, **k: _FakeClient(h))
    ids = [{"idType": "SHA256_EMAIL", "idValue": "ab"}, {"idType": "SHA256_EMAIL", "idValue": "cd"}]
    out = await ADV.upload_dmp_users("urn:li:adSegment:42", [ids])
    assert out["ok"]
    assert seen["url"].endswith("/rest/dmpSegments/42/users")
    el = seen["json"]["elements"][0]
    assert el["action"] == "ADD"
    assert el["userIds"] == ids


@pytest.mark.asyncio
async def test_conversion_audit_clean(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "o")))
    monkeypatch.setenv("LINKEDIN_CONVERSION_RULE", "urn:lla:llaPartnerConversion:100")
    monkeypatch.setenv("LINKEDIN_CONVERSION_RULE_LEAD_CREATED", "urn:lla:llaPartnerConversion:101")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(lambda m, u, **kw: _Resp(200, _rules())))
    alert = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", alert)
    out = await CA._audit()
    assert out["ok"] is True and out["problems"] == []
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_conversion_audit_missing_rule_alerts(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "o")))
    monkeypatch.setenv("LINKEDIN_CONVERSION_RULE", "urn:lla:llaPartnerConversion:999")
    monkeypatch.setenv("LINKEDIN_CONVERSION_RULE_LEAD_CREATED", "urn:lla:llaPartnerConversion:101")
    # only audit_booked exists, and its id doesn't match env
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: _FakeClient(lambda m, u, **kw: _Resp(200, _rules(("audit_booked",)))),
    )
    alert = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", alert)
    out = await CA._audit()
    assert out["ok"] is False
    assert any("lead_created: rule missing" in p for p in out["problems"])
    assert any("audit_booked: env URN id" in p for p in out["problems"])
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_dmp_sync_pending_grant_skips(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "o")))
    monkeypatch.setattr(DS, "_redis", lambda: None)
    monkeypatch.setattr(
        ADV,
        "create_dmp_segment",
        AsyncMock(return_value={"error": 403, "body": "denied"}),
    )
    out = await DS._sync()
    assert out["skip"] == "matched-audiences-not-granted"


@pytest.mark.asyncio
async def test_dmp_sync_uploads_hashed_emails(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "o")))
    fake_r = SimpleNamespace(get=lambda k: "urn:li:adSegment:7", set=lambda *a, **k: True, delete=lambda k: None)
    monkeypatch.setattr(DS, "_redis", lambda: fake_r)
    monkeypatch.setattr(
        ESPO,
        "list_leads_for_campaign",
        AsyncMock(return_value=[{"emailAddress": "  A@B.com "}, {"emailAddress": "bad"}, {}]),
    )
    upload = AsyncMock(return_value={"status": 204, "ok": True})
    monkeypatch.setattr(ADV, "upload_dmp_users", upload)
    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock())
    out = await DS._sync()
    assert out["uploaded"] == 1 and out["leads"] == 3
    sent = upload.await_args.args[1][0]
    assert sent == [{"idType": "SHA256_EMAIL", "idValue": hashlib.sha256(b"a@b.com").hexdigest()}]
    assert "A@B.com" not in json.dumps(upload.await_args.args)


@pytest.mark.asyncio
async def test_dmp_sync_disabled_flag_skips(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=None))
    out = await DS._sync()
    assert out["skip"] == "linkedin-advanced-disabled"


@pytest.mark.asyncio
async def test_dmp_sync_segment_create_caches_urn(monkeypatch):
    """No cached URN → create_dmp_segment success → Redis set + upload proceeds."""
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "o")))
    sets = []
    fake_r = SimpleNamespace(
        get=lambda k: None,
        set=lambda k, v: sets.append((k, v)),
        delete=lambda k: None,
    )
    monkeypatch.setattr(DS, "_redis", lambda: fake_r)
    create = AsyncMock(return_value={"urn": "urn:li:adSegment:9"})
    monkeypatch.setattr(ADV, "create_dmp_segment", create)
    monkeypatch.setattr(ESPO, "list_leads_for_campaign", AsyncMock(return_value=[]))
    out = await DS._sync()
    assert sets == [(DS.SEGMENT_REDIS_KEY, "urn:li:adSegment:9")]
    assert out["skip"] == "no-lead-emails" and out["segment"] == "urn:li:adSegment:9"


@pytest.mark.asyncio
async def test_dmp_sync_upload_failure_breaks_without_alert(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "o")))
    fake_r = SimpleNamespace(get=lambda k: "urn:li:adSegment:7", set=lambda *a: True, delete=lambda k: None)
    monkeypatch.setattr(DS, "_redis", lambda: fake_r)
    monkeypatch.setattr(
        ESPO,
        "list_leads_for_campaign",
        AsyncMock(return_value=[{"emailAddress": "a@b.com"}, {"emailAddress": "c@d.com"}]),
    )
    monkeypatch.setattr(ADV, "upload_dmp_users", AsyncMock(return_value={"status": 500, "ok": False, "error": "x"}))
    alert = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", alert)
    out = await DS._sync()
    assert out["uploaded"] == 0 and out["status"] == 500
    alert.assert_not_awaited()  # partial/failed upload never alerts success


@pytest.mark.asyncio
async def test_dmp_sync_404_evicts_segment_cache(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "o")))
    deleted = []
    fake_r = SimpleNamespace(
        get=lambda k: "urn:li:adSegment:gone",
        set=lambda *a: True,
        delete=lambda k: deleted.append(k),
    )
    monkeypatch.setattr(DS, "_redis", lambda: fake_r)
    monkeypatch.setattr(ESPO, "list_leads_for_campaign", AsyncMock(return_value=[{"emailAddress": "a@b.com"}]))
    monkeypatch.setattr(ADV, "upload_dmp_users", AsyncMock(return_value={"status": 404, "ok": False}))
    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock())
    out = await DS._sync()
    assert deleted == [DS.SEGMENT_REDIS_KEY]
    assert out["status"] == 404


@pytest.mark.asyncio
async def test_conversion_audit_disabled_flag_skips(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=None))
    out = await CA._audit()
    assert out["skip"] == "linkedin-advanced-disabled"


@pytest.mark.asyncio
async def test_conversion_audit_http_error_alerts(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "o")))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(lambda m, u, **kw: _Resp(500, {})))
    alert = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", alert)
    out = await CA._audit()
    assert out["error"] == 500
    alert.assert_awaited_once()
