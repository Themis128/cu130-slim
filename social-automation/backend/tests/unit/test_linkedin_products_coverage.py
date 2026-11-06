"""Coverage for LinkedIn advanced products + engagement-score internals."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import calcom_webhook as CW
from app.services import linkedin_ad_library as AL
from app.services import linkedin_advanced as ADV
from app.services import linkedin_events as EV
from app.worker.tasks import linkedin_engagement_score as S
from app.worker.tasks import linkedin_lead_boost as LB


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *a):
        return False


class _Resp:
    def __init__(self, status=200, data=None, text=None, headers=None):
        self.status_code = status
        self._data = data if data is not None else {}
        self.text = text if text is not None else json.dumps(self._data)
        self.headers = headers or {}

    def json(self):
        return self._data


class _FakeClient:
    """httpx.AsyncClient stand-in routed by a handler callback."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        self.calls.append(("GET", url))
        return self._handler("GET", url, params=params)

    async def post(self, url, json=None):
        self.calls.append(("POST", url))
        return self._handler("POST", url, json=json)


def _patch_client(monkeypatch, module, handler):
    """httpx.AsyncClient -> fake; returns the client instance for call inspection."""
    import httpx

    client = _FakeClient(handler)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: client)
    return client


def _result(first=None, all_=None):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: first, all=lambda: all_ or []),
        scalar_one_or_none=lambda: first,
    )


# ── linkedin_advanced ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adv_ctx_disabled_and_enabled(monkeypatch):
    monkeypatch.setattr(ADV, "ENABLED", False)
    assert await ADV._ctx() is None
    monkeypatch.setattr(ADV, "ENABLED", True)
    db = SimpleNamespace(execute=AsyncMock(return_value=_result(None)))
    monkeypatch.setattr(ADV, "task_session", lambda: _AsyncCM(db))
    assert await ADV._ctx() is None


@pytest.mark.asyncio
async def test_adv_ctx_returns_token(monkeypatch):
    monkeypatch.setattr(ADV, "ENABLED", True)
    acc = SimpleNamespace(access_token_enc=b"x", account_id="123")
    db = SimpleNamespace(execute=AsyncMock(return_value=_result(acc)))
    monkeypatch.setattr(ADV, "task_session", lambda: _AsyncCM(db))
    monkeypatch.setattr(ADV, "decrypt_token", lambda b: "tok")
    assert await ADV._ctx() == ("tok", "urn:li:organization:123")


@pytest.mark.asyncio
async def test_lead_form_responses(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=None))
    assert (await ADV.lead_form_responses("urn:li:leadForm:9"))["skip"]
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "urn")))
    _patch_client(monkeypatch, ADV, lambda m, u, **k: _Resp(200, {"elements": [{"a": 1}]}))
    out = await ADV.lead_form_responses("urn:li:leadForm:9")
    assert out["responses"] == [{"a": 1}] and out["status"] == 200


@pytest.mark.asyncio
async def test_stream_conversion(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=None))
    assert (await ADV.stream_conversion(conversion_rule="r", happened_at_ms=1))["skip"]
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "urn")))
    seen = {}

    def h(m, u, **k):
        seen["json"] = k.get("json")
        return _Resp(201, {})

    _patch_client(monkeypatch, ADV, h)
    out = await ADV.stream_conversion(conversion_rule="audit_booked", happened_at_ms=7, email_sha256="e", lead_id="l")
    assert out["ok"] and seen["json"]["user"] == {"email": {"sha256": "e"}, "leadId": "l"}


@pytest.mark.asyncio
async def test_dmp_segment_and_users(monkeypatch):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "urn")))
    _patch_client(monkeypatch, ADV, lambda m, u, **k: _Resp(201, headers={"x-restli-id": "seg1"}))
    assert (await ADV.create_dmp_segment("n"))["urn"] == "seg1"
    _patch_client(monkeypatch, ADV, lambda m, u, **k: _Resp(403, text="denied"))
    assert (await ADV.create_dmp_segment("n"))["error"] == 403
    _patch_client(monkeypatch, ADV, lambda m, u, **k: _Resp(204, {}))
    assert (await ADV.upload_dmp_users("urn:li:adSegment:1", [{"a": 1}]))["ok"]
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=None))
    assert (await ADV.create_dmp_segment("n"))["skip"]
    assert (await ADV.upload_dmp_users("u", []))["skip"]


# ── linkedin_events ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_org_event_paths(monkeypatch):
    acc = SimpleNamespace(access_token_enc=b"x", account_id="123")
    db = SimpleNamespace(execute=AsyncMock(return_value=_result(acc)))
    monkeypatch.setattr(EV, "task_session", lambda: _AsyncCM(db))
    monkeypatch.setattr(EV, "decrypt_token", lambda b: "tok")
    _patch_client(monkeypatch, EV, lambda m, u, **k: _Resp(201, {"id": "e1"}))
    out = await EV.create_org_event("n", 1, 2, description="d", event_url="https://x")
    assert out["urn"] == "e1"
    _patch_client(monkeypatch, EV, lambda m, u, **k: _Resp(200, {"elements": [{"e": 1}]}))
    out = await EV.list_org_events()
    assert out["events"] == [{"e": 1}]
    _patch_client(monkeypatch, EV, lambda m, u, **k: _Resp(500, text="err"))
    assert (await EV.create_org_event("n", 1, 2))["error"] == 500
    db.execute = AsyncMock(return_value=_result(None))
    assert (await EV.create_org_event("n", 1, 2))["skip"] == "no-linkedin-org-account"


# ── linkedin_ad_library ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ad_library_search(monkeypatch):
    monkeypatch.setattr(AL, "_token", AsyncMock(return_value="tok"))
    seen = {}

    def h(m, u, **k):
        seen.update(k.get("params") or {})
        return _Resp(200, {"elements": [{"ad": 1}]})

    _patch_client(monkeypatch, AL, h)
    out = await AL.search_ads(advertiser="Cloudless", keyword="cloud")
    assert out["ads"] == [{"ad": 1}] and seen["advertisers"] == "List(Cloudless)"
    _patch_client(monkeypatch, AL, lambda m, u, **k: _Resp(403, text="no"))
    assert (await AL.search_ads())["error"] == 403


# ── engagement_score helpers + main loop ─────────────────────────────────


@pytest.mark.asyncio
async def test_social_metadata_paths(monkeypatch):
    async def mk(handler):
        return _FakeClient(handler)

    c = await mk(lambda m, u, **k: _Resp(200, {"elements": [{"likeCount": 3}]}))
    assert (await S._social_metadata(c, "u")) == {"likeCount": 3}
    c = await mk(lambda m, u, **k: _Resp(404, {}))
    assert await S._social_metadata(c, "u") is None
    calls = {"n": 0}

    def h(m, u, **k):
        calls["n"] += 1
        return _Resp(500 if calls["n"] < 3 else 200, {"likesSummary": {"totalLikes": 5}})

    c = await mk(h)
    assert (await S._social_metadata(c, "u"))["likeCount"] == 5
    c = await mk(lambda m, u, **k: _Resp(500, {}))
    assert await S._social_metadata(c, "u") is None


@pytest.mark.asyncio
async def test_comments_paths(monkeypatch):
    c = _FakeClient(lambda m, u, **k: _Resp(200, {"elements": [{"a": 1}]}))
    assert (await S._comments(c, "u")) == [{"a": 1}]
    calls = {"n": 0}

    def h(m, u, **k):
        calls["n"] += 1
        return _Resp(403 if calls["n"] == 1 else 200, {"elements": [{"b": 2}]})

    c = _FakeClient(h)
    assert (await S._comments(c, "u")) == [{"b": 2}] and calls["n"] == 2
    c = _FakeClient(lambda m, u, **k: _Resp(500, {}))
    assert await S._comments(c, "u") == []


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_score_main_loop_winners_and_commenters(monkeypatch):
    acc = SimpleNamespace(id="acc1", access_token_enc=b"x", team_id="t")
    tgt, post = SimpleNamespace(platform_post_id="urn:1"), SimpleNamespace(id="p1")
    exec_results = [
        _result(acc),
        SimpleNamespace(all=lambda: [(tgt, post)]),
        SimpleNamespace(scalar_one_or_none=lambda: None),
    ]
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=exec_results),
        get=AsyncMock(return_value=SimpleNamespace(meta_data={})),
        add=lambda o: None,
        commit=AsyncMock(),
    )
    monkeypatch.setattr(S, "task_session", lambda: _AsyncCM(db))
    import app.core.security as sec

    monkeypatch.setattr(sec, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(S, "_social_metadata", AsyncMock(return_value={"likeCount": 50, "impressionCount": 10}))
    monkeypatch.setattr(S, "_comments", AsyncMock(return_value=[{"actor": "u1", "message": {"text": "hi"}}]))
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(lambda *a, **k: _Resp(200, {})))
    out = await S._score_linkedin_posts()
    assert out["scored"] == 1 and out["commenters_added"] == 1


@pytest.mark.asyncio
async def test_score_no_stats(monkeypatch):
    acc = SimpleNamespace(id="a", access_token_enc=b"x")
    exec_results = [
        _result(acc),
        SimpleNamespace(all=lambda: [(SimpleNamespace(platform_post_id="u"), SimpleNamespace(id="p"))]),
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=exec_results))
    monkeypatch.setattr(S, "task_session", lambda: _AsyncCM(db))
    import app.core.security as sec

    monkeypatch.setattr(sec, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(S, "_social_metadata", AsyncMock(return_value=None))
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(lambda *a, **k: _Resp(200, {})))
    out = await S._score_linkedin_posts()
    assert out["scored"] == 0 and out["skip"] == "no-post-stats"


# ── linkedin_lead_boost ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_leads_enabled(monkeypatch):
    monkeypatch.setattr(LB, "ENABLED", True)
    acc = SimpleNamespace(team_id="t1")
    db = SimpleNamespace(execute=AsyncMock(return_value=_result(acc)))
    monkeypatch.setattr(LB, "task_session", lambda: _AsyncCM(db))
    import app.services.linkedin_ads as LADS

    monkeypatch.setattr(LADS, "ad_accounts", AsyncMock(return_value=[{"id": "a1"}]))
    monkeypatch.setattr(LADS, "lead_gen_forms", AsyncMock(return_value=[{"id": "f1"}]))
    monkeypatch.setattr(LADS, "lead_form_responses", AsyncMock(return_value=[{"id": "r1"}]))
    import app.services.slack_notifications as SN

    alert = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", alert)
    out = await LB._route_linkedin_leads()
    assert out["routed"] == 1 and alert.called
    monkeypatch.setattr(LADS, "ad_accounts", AsyncMock(return_value=[]))
    assert (await LB._route_linkedin_leads())["routed"] == 0


@pytest.mark.asyncio
async def test_boost_winners(monkeypatch):
    monkeypatch.setattr(LB, "ENABLED", True)
    winners = [SimpleNamespace(meta_data={}), SimpleNamespace(meta_data={})]
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_result(all_=winners)),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(LB, "task_session", lambda: _AsyncCM(db))
    import app.services.slack_notifications as SN

    alert = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", alert)
    out = await LB._boost_linkedin_winners()
    assert out["boosted"] == 2 and alert.called
    assert winners[0].meta_data["boost_queued"] is True
    monkeypatch.setattr(LB, "ENABLED", False)
    assert (await LB._boost_linkedin_winners())["skip"]
    assert (await LB._route_linkedin_leads())["skip"]


def test_boost_task_wrappers(monkeypatch):
    monkeypatch.setattr(LB, "run_async", lambda c: {"ok": 1})
    assert LB.route_linkedin_leads() == {"ok": 1}
    assert LB.boost_linkedin_winners() == {"ok": 1}


# ── calcom_webhook tail ──────────────────────────────────────────────────


def test_verify_sig_edge(monkeypatch):
    monkeypatch.setenv("CALCOM_WEBHOOK_SECRET", "s")
    assert CW._verify_sig(b"body", None) is False
    import hashlib
    import hmac

    good = hmac.new(b"s", b"body", hashlib.sha256).hexdigest()
    assert CW._verify_sig(b"body", good) is True


@pytest.mark.asyncio
async def test_calcom_bad_sig_and_non_booking(monkeypatch):
    monkeypatch.setenv("CALCOM_WEBHOOK_SECRET", "s")
    req = SimpleNamespace(
        body=AsyncMock(return_value=b"{}"),
        headers={"x-cal-signature-256": "bad"},
        json=AsyncMock(return_value={}),
    )
    with pytest.raises(Exception) as ei:
        await CW.calcom_booking(req)
    assert "401" in str(ei.value)
    monkeypatch.setenv("CALCOM_WEBHOOK_SECRET", "")
    req2 = SimpleNamespace(
        body=AsyncMock(return_value=b"{}"),
        headers={},
        json=AsyncMock(return_value={"triggerEvent": "CANCELLED"}),
    )
    out = await CW.calcom_booking(req2)
    assert out.status_code == 200
