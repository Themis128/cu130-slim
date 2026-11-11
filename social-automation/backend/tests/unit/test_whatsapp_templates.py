"""Coverage for app.worker.tasks.whatsapp_templates — WABA template poller."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.slack_notifications as SN
import app.worker.tasks.whatsapp_templates as WT
from app.models.social_account import SocialAccount


class _ACM:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _DB:
    def __init__(self, rows):
        self._rows = rows
        self.commits = 0

    async def execute(self, stmt):
        return _Res(self._rows)

    async def commit(self):
        self.commits += 1


def _acct(meta, token_enc="enc"):
    a = SocialAccount(platform="whatsapp", meta_data=meta, access_token_enc=token_enc)
    a.id = "acc-1"
    return a


@pytest.mark.asyncio
async def test_poll_no_waba_skips(monkeypatch):
    db = _DB([_acct({"other": "x"})])
    monkeypatch.setattr(WT, "task_session", lambda: _ACM(db))
    fetch = AsyncMock()
    monkeypatch.setattr(WT, "_fetch_templates", fetch)
    out = await WT._poll()
    assert out["accounts"] == 1 and out["templates"] == 0 and out["errors"] == 0
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_decrypt_failure_counts_error(monkeypatch):
    db = _DB([_acct({"waba_id": "w1"})])
    monkeypatch.setattr(WT, "task_session", lambda: _ACM(db))
    monkeypatch.setattr(WT, "decrypt_token", lambda enc: None)
    out = await WT._poll()
    assert out["errors"] == 1


@pytest.mark.asyncio
async def test_poll_fetch_failure_counts_error(monkeypatch):
    db = _DB([_acct({"business_account_id": "w2"})])
    monkeypatch.setattr(WT, "task_session", lambda: _ACM(db))
    monkeypatch.setattr(WT, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(WT, "_fetch_templates", AsyncMock(return_value=None))
    out = await WT._poll()
    assert out["errors"] == 1


@pytest.mark.asyncio
async def test_poll_first_sight_records_silently_then_transition_alerts(monkeypatch):
    meta = {"waba_id": "w3", "whatsapp_templates": {"order_update|en": "APPROVED"}}
    acct = _acct(meta)
    db = _DB([acct])
    monkeypatch.setattr(WT, "task_session", lambda: _ACM(db))
    monkeypatch.setattr(WT, "decrypt_token", lambda enc: "tok")
    templates = [
        {"name": "order_update", "language": "en", "status": "REJECTED", "rejected_reason": "bad CTA"},
        {"name": "new_tpl", "language": "en", "status": "PENDING"},
    ]
    monkeypatch.setattr(WT, "_fetch_templates", AsyncMock(return_value=templates))
    slack = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", slack)

    out = await WT._poll()
    assert out["changes"] == 1 and out["templates"] == 2 and db.commits == 1
    assert acct.meta_data["whatsapp_templates"]["order_update|en"] == "REJECTED"
    assert acct.meta_data["whatsapp_templates"]["new_tpl|en"] == "PENDING"
    text = slack.await_args.args[0]
    assert "order_update" in text and "REJECTED" in text and "bad CTA" in text


@pytest.mark.asyncio
async def test_poll_removed_template_alerts(monkeypatch):
    meta = {"waba_id": "w4", "whatsapp_templates": {"gone|en": "APPROVED", "kept|en": "APPROVED"}}
    acct = _acct(meta)
    db = _DB([acct])
    monkeypatch.setattr(WT, "task_session", lambda: _ACM(db))
    monkeypatch.setattr(WT, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(
        WT,
        "_fetch_templates",
        AsyncMock(return_value=[{"name": "kept", "language": "en", "status": "APPROVED"}]),
    )
    slack = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", slack)

    out = await WT._poll()
    assert out["changes"] == 1
    assert "removed from WABA" in slack.await_args.args[0]


@pytest.mark.asyncio
async def test_poll_meta_as_json_string_and_no_changes(monkeypatch):
    acct = _acct(json.dumps({"waba_id": "w5", "whatsapp_templates": {"t|en": "APPROVED"}}))
    db = _DB([acct])
    monkeypatch.setattr(WT, "task_session", lambda: _ACM(db))
    monkeypatch.setattr(WT, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(
        WT,
        "_fetch_templates",
        AsyncMock(return_value=[{"name": "t", "language": "en", "status": "APPROVED"}]),
    )
    out = await WT._poll()
    assert out["changes"] == 0 and out["templates"] == 1 and db.commits == 0


@pytest.mark.asyncio
async def test_poll_slack_failure_swallowed(monkeypatch):
    acct = _acct({"waba_id": "w6", "whatsapp_templates": {"t|en": "PENDING"}})
    db = _DB([acct])
    monkeypatch.setattr(WT, "task_session", lambda: _ACM(db))
    monkeypatch.setattr(WT, "decrypt_token", lambda enc: "tok")
    monkeypatch.setattr(
        WT,
        "_fetch_templates",
        AsyncMock(return_value=[{"name": "t", "language": "en", "status": "APPROVED"}]),
    )
    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock(side_effect=RuntimeError("slack down")))
    out = await WT._poll()
    assert out["changes"] == 1


class _FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeHTTP:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        self.calls += 1
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_fetch_templates_paginates(monkeypatch):
    page1 = {
        "data": [{"name": "a", "status": "APPROVED"}],
        "paging": {"next": "https://graph.facebook.com/next-page"},
    }
    page2 = {"data": [{"name": "b", "status": "PENDING"}]}
    http = _FakeHTTP([_FakeResp(200, page1), _FakeResp(200, page2)])
    monkeypatch.setattr(WT.httpx, "AsyncClient", lambda **kw: http)
    out = await WT._fetch_templates("waba", "tok")
    assert [t["name"] for t in out] == ["a", "b"] and http.calls == 2


@pytest.mark.asyncio
async def test_fetch_templates_api_failure_returns_none(monkeypatch):
    http = _FakeHTTP([_FakeResp(500, text="boom")])
    monkeypatch.setattr(WT.httpx, "AsyncClient", lambda **kw: http)
    assert await WT._fetch_templates("waba", "tok") is None


def test_task_wrapper_success(monkeypatch):
    def _run(coro):
        coro.close()
        return {"accounts": 1}

    monkeypatch.setattr(WT, "run_async", _run)
    out = WT.poll_whatsapp_templates()
    assert out == {"accounts": 1}


def test_task_wrapper_error(monkeypatch):
    def _raise(coro):
        coro.close()
        raise RuntimeError("boom")

    monkeypatch.setattr(WT, "run_async", _raise)
    out = WT.poll_whatsapp_templates()
    assert out == {"error": "boom"}
