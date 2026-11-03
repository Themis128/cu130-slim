"""Unit tests: x_webhook, funnel API, ads API, skool actor, log_intel,
telegram MCP send, research_doc model."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

# ── x_webhook ───────────────────────────────────────────────────────────────
from app.api import x_webhook as XW


@pytest.mark.asyncio
async def test_crc_missing_token_and_secret(monkeypatch):
    assert (await XW.crc_challenge("")).status_code == 400
    monkeypatch.delenv("TWITTER_API_SECRET", raising=False)
    assert (await XW.crc_challenge("tok")).status_code == 500


@pytest.mark.asyncio
async def test_crc_signs_token(monkeypatch):
    monkeypatch.setenv("TWITTER_API_SECRET", "sec")
    out = await XW.crc_challenge("tok")
    expected = base64.b64encode(hmac.new(b"sec", b"tok", hashlib.sha256).digest()).decode()
    assert out == {"response_token": "sha256=" + expected}


class _Req:
    def __init__(self, payload=None, bad=False):
        self._p, self._bad = payload, bad

    async def json(self):
        if self._bad:
            raise ValueError("bad")
        return self._p


@pytest.mark.asyncio
async def test_event_ingest(caplog):
    bad = await XW.event_ingest(_Req(bad=True))
    assert bad.status_code == 400
    caplog.set_level("INFO", logger=XW.logger.name)
    ok = await XW.event_ingest(_Req({"direct_message_events": [{}, {}], "follow_events": []}))
    assert ok.status_code == 200 and json.loads(ok.body) == {"ok": True}
    assert "dm_events: 2 event(s)" in caplog.text
    assert "follow_events" not in caplog.text


# ── funnel API ──────────────────────────────────────────────────────────────
from app.api import funnel as F  # noqa: E402


def test_ingest_token(monkeypatch):
    monkeypatch.setattr(F, "get_settings", lambda: SimpleNamespace(FUNNEL_INGEST_TOKEN=" t0k "))
    F._check_ingest_token("t0k")
    with pytest.raises(HTTPException) as e:
        F._check_ingest_token("nope")
    assert e.value.status_code == 401
    monkeypatch.setattr(F, "get_settings", lambda: SimpleNamespace(FUNNEL_INGEST_TOKEN=None))
    F._check_ingest_token(None)  # no token configured → open


def _db():
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.scalar = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_ingest_links_by_email(monkeypatch):
    monkeypatch.setattr(F, "get_settings", lambda: SimpleNamespace(FUNNEL_INGEST_TOKEN=""))
    db = _db()
    lead = uuid.uuid4()
    db.scalar.return_value = lead
    post = uuid.uuid4()
    body = F.ConversionIn(email=" Buyer@X.com ", post_id=post, value=49.0, provider="polar", meta_data={"order": 1})
    out = await F.ingest_conversion(body, db, None)
    assert out["lead_id"] == str(lead) and out["linked"] is True
    params = db.execute.await_args.args[1]
    assert params["email"] == "buyer@x.com" and params["post_id"] == str(post)
    assert json.loads(params["meta_data"]) == {"order": 1}
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_explicit_lead_and_unlinked(monkeypatch):
    monkeypatch.setattr(F, "get_settings", lambda: SimpleNamespace(FUNNEL_INGEST_TOKEN=""))
    db = _db()
    lead = uuid.uuid4()
    out = await F.ingest_conversion(F.ConversionIn(lead_id=lead), db, None)
    assert out["lead_id"] == str(lead)
    db.scalar.assert_not_awaited()
    params = db.execute.await_args.args[1]
    assert params["email"] is None and params["post_id"] is None and params["meta_data"] is None

    db = _db()
    db.scalar.return_value = None
    out = await F.ingest_conversion(F.ConversionIn(email="nobody@x.com"), db, None)
    assert out == {"id": out["id"], "lead_id": None, "linked": False}


@pytest.mark.asyncio
async def test_funnel_state():
    db = MagicMock()
    tiers = MagicMock()
    tiers.fetchall.return_value = [SimpleNamespace(tier="hot", count=2), SimpleNamespace(tier="cold", count=5)]

    class _Row(tuple):
        campaign = "spring"
        count = 3

    convs = MagicMock()
    convs.fetchall.return_value = [_Row(("spring", 3, 120))]
    totals = MagicMock()
    totals.one.return_value = (3, 120)
    db.execute = AsyncMock(side_effect=[tiers, convs, totals])
    out = await F.funnel_state(db, None)
    assert out["leads"] == {"hot": 2, "cold": 5}
    assert out["conversions"] == [{"campaign": "spring", "count": 3, "value": 120.0}]
    assert out["total_conversions"] == 3 and out["total_value"] == 120.0 and out["at"]


# ── ads API ─────────────────────────────────────────────────────────────────
from app.api import ads as ADS  # noqa: E402


@pytest.mark.asyncio
async def test_ads_status_and_snapshot(monkeypatch):
    import app.services.linkedin_ads as la

    monkeypatch.setattr(la, "ad_accounts", AsyncMock(return_value=[{"id": i} for i in range(7)]))
    user = SimpleNamespace(team_id="t1")
    out = await ADS.linkedin_ads_status(MagicMock(), user)
    assert out["accounts"] == 7 and len(out["detail"]) == 5

    monkeypatch.setattr(la, "ad_accounts", AsyncMock(side_effect=RuntimeError("403")))
    with pytest.raises(HTTPException) as e:
        await ADS.linkedin_ads_status(MagicMock(), user)
    assert e.value.status_code == 502 and "403" in e.value.detail

    snap = AsyncMock(return_value={"campaigns": 1})
    monkeypatch.setattr(la, "snapshot", snap)
    db = MagicMock()
    db.commit = AsyncMock()
    assert await ADS.trigger_snapshot(db, user) == {"campaigns": 1}
    assert snap.await_args.args[1] == "t1"
    db.commit.assert_awaited_once()


# ── skool actor ─────────────────────────────────────────────────────────────
from app.services.engagement_actors import skool_actor as SK  # noqa: E402


@pytest.mark.asyncio
async def test_skool_actor_paths(monkeypatch):
    import app.services.skool_sync as ss

    assert await SK.act(SimpleNamespace(meta_data=None), None) == {"ok": False, "skip": True, "error": "no body"}
    action = SimpleNamespace(meta_data={"title": "T", "body": "B"})
    post = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ss, "post_to_community", post)
    assert await SK.act(action, None) == {"ok": True}
    post.assert_awaited_once_with("T", "B")

    monkeypatch.setattr(ss, "post_to_community", AsyncMock(return_value={"ok": False, "error": "no session"}))
    assert await SK.act(action, None) == {"ok": False, "skip": True, "error": "no session"}
    monkeypatch.setattr(ss, "post_to_community", AsyncMock(return_value={}))
    assert (await SK.act(action, None))["error"] == "?"
    monkeypatch.setattr(ss, "post_to_community", AsyncMock(side_effect=RuntimeError("x" * 300)))
    out = await SK.act(action, None)
    assert out["ok"] is False and len(out["error"]) == 120 and "skip" not in out


# ── log_intel ───────────────────────────────────────────────────────────────
from app.services import log_intel as LI  # noqa: E402


class _Loki:
    def __init__(self, status=200, payload=None):
        self.status, self.payload, self.params = status, payload or {}, None

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        self.params = params
        return SimpleNamespace(status_code=self.status, text="err", json=lambda: self.payload)


@pytest.mark.asyncio
async def test_query_loki(monkeypatch):
    payload = {
        "data": {
            "result": [
                {"stream": {"container_name": "api"}, "values": [["1", "error A" * 50]]},
                {"stream": {"service": "worker"}, "values": [["2", "failed B"]]},
                {"stream": {}, "values": [["3", "warn C"]]},
            ]
        }
    }
    loki = _Loki(payload=payload)
    monkeypatch.setattr(LI.httpx, "AsyncClient", loki)
    lines = await LI._query_loki(5)
    assert lines[0].startswith("[api] ") and len(lines[0]) == len("[api] ") + 200
    assert lines[1] == "[worker] failed B" and lines[2] == "[?] warn C"
    assert loki.params["end"] - loki.params["start"] == int(5 * 60 * 1e9)

    monkeypatch.setattr(LI.httpx, "AsyncClient", _Loki(status=503))
    assert await LI._query_loki(5) == []
    monkeypatch.setattr(LI.httpx, "AsyncClient", _Loki(payload={"data": {"result": None}}))
    assert await LI._query_loki(5) == []


@pytest.mark.asyncio
async def test_triage_logs(monkeypatch):
    monkeypatch.setattr(LI, "_query_loki", AsyncMock(return_value=[]))
    assert await LI.triage_logs() == {"lines": 0, "summary": "no errors/warnings in window"}
    monkeypatch.setattr(LI, "_query_loki", AsyncMock(return_value=["[api] error"]))
    call = AsyncMock(return_value="P1 api errors")
    monkeypatch.setattr(LI, "llm_call", call)
    assert await LI.triage_logs() == {"lines": 1, "summary": "P1 api errors"}
    expect = call.await_args.kwargs["expect"]
    assert expect("x" * 21) and not expect("short")
    monkeypatch.setattr(LI, "llm_call", AsyncMock(side_effect=RuntimeError("dmr down")))
    assert (await LI.triage_logs())["summary"] == "triage failed: dmr down"


# ── telegram MCP send ───────────────────────────────────────────────────────
from app.services import telegram_notify as TN  # noqa: E402


class _Mcp:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        if self.fail:
            raise ConnectionError("mcp down")
        self.calls.append((headers, json))
        return SimpleNamespace(headers={"mcp-session-id": "sid-1"})


@pytest.mark.asyncio
async def test_send_telegram(monkeypatch):
    mcp = _Mcp()
    monkeypatch.setattr(TN.httpx, "AsyncClient", mcp)
    assert await TN.send_telegram("hello", chat="ops") is True
    methods = [j["method"] for _, j in mcp.calls]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]
    assert "Mcp-Session-Id" not in mcp.calls[0][0]
    assert mcp.calls[2][0]["Mcp-Session-Id"] == "sid-1"
    assert mcp.calls[2][1]["params"]["arguments"] == {"chat_id": "ops", "message": "hello"}

    monkeypatch.setattr(TN.httpx, "AsyncClient", _Mcp(fail=True))
    assert await TN.send_telegram("x") is False


# ── research_doc model ──────────────────────────────────────────────────────


def test_research_doc_model():
    from app.models.research_doc import ResearchDoc

    assert ResearchDoc.__tablename__ == "research_docs"
    cols = ResearchDoc.__table__.c
    assert {"id", "team_id", "title", "content", "doc_type", "sources", "meta_data"} <= set(cols.keys())
    assert cols["created_at"].default.arg(None).tzinfo is not None
    assert cols["updated_at"].onupdate.arg(None).tzinfo is not None
    doc = ResearchDoc(team_id=uuid.uuid4(), title="t")
    assert doc.title == "t"
