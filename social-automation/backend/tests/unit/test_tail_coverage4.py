"""Tail-coverage batch 4 — remaining provider/webhook/report branches.

Covers real branch behavior (not pragma'd):
- app.services.inference: DMR chat/embedding error + schema normalization,
  OpenAI-compat payload/response paths, JSON extraction/parsing fallbacks,
  NVIDIA FLUX / FLUX-dev / SD3.5 / Kontext-pipeline error + validation
  branches, get_team_id_for_user.
- app.api.telegram webhook: member welcome failure, chat_join_request
  approve/pending/error branches, lead-capture completed_lead_id.
- app.services.linkedin_ads_report: campaigns-list note, stats error,
  engagement-rate stat, lifetime-cap pace, uncapped campaigns, bad end
  date, notebook render path, organic-section + email failures.
- app.api.brand: non-404 re-raise, compliance failure, logo/favicon
  non-image-model fallback and generation errors.
- Misc stragglers: x_web, comfyui_image cancel, tiktok chunk guards,
  messenger_sidecar token retry, publishing/r2/telegram_group_watch/
  whatsapp/blog_articles/infographic/config one-liners.
"""

from __future__ import annotations

import asyncio
import base64
import io
import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException

import app.services.inference as INF

# ── inference: DMR chat/embedding ────────────────────────────────────


@pytest.mark.asyncio
async def test_dmr_chat_connection_error_maps_502(monkeypatch):
    import app.services.dmr as dmr

    async def _boom(*a, **kw):
        raise ConnectionError("dmr down")

    monkeypatch.setattr(dmr, "call_dmr_chat", _boom)
    with pytest.raises(HTTPException) as ei:
        await INF._call_dmr_chat("hi")
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_dmr_chat_schema_normalization(monkeypatch):
    import app.services.dmr as dmr

    # schema + parsed json → returns the json object
    monkeypatch.setattr(dmr, "call_dmr_chat", AsyncMock(return_value={"json": {"ok": 1}}))
    out = await INF._call_dmr_chat("hi", schema={"type": "object"})
    assert out == {"ok": 1}

    # schema + raw text + parse error → returned as-is for caller
    res = {"text": "not json", "_parse_error": "bad json"}
    monkeypatch.setattr(dmr, "call_dmr_chat", AsyncMock(return_value=res))
    out2 = await INF._call_dmr_chat("hi", schema={"type": "object"})
    assert out2 is res


@pytest.mark.asyncio
async def test_dmr_embedding_connection_error_maps_502(monkeypatch):
    import app.services.dmr as dmr

    async def _boom(*a, **kw):
        raise ConnectionError("dmr down")

    monkeypatch.setattr(dmr, "call_dmr_embedding", _boom)
    with pytest.raises(HTTPException) as ei:
        await INF._call_dmr_embedding("text")
    assert ei.value.status_code == 502


# ── inference: OpenAI-compatible provider ────────────────────────────


@pytest.mark.asyncio
async def test_openai_compat_schema_and_errors():
    import respx

    with respx.mock(assert_all_called=False) as router:
        router.post("https://llm.example/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"a": 1}', "role": "assistant"}}]},
            )
        )
        out = await INF._call_openai_compat(
            "p",
            "https://llm.example/v1",
            "gpt-oss-120b",
            "k",
            schema={"type": "object"},
        )
        assert out == {"a": 1}


@pytest.mark.asyncio
async def test_openai_compat_non_200_and_reasoning():
    import respx

    with respx.mock(assert_all_called=False) as router:
        router.post("https://llm.example/v1/chat/completions").mock(return_value=httpx.Response(429, text="rate limited"))
        with pytest.raises(HTTPException) as ei:
            await INF._call_openai_compat("p", "https://llm.example/v1", "m", "k", schema={"a": 1})
        assert ei.value.status_code == 502

    # content=null → reasoning_content fallback (plain text path)
    with respx.mock(assert_all_called=False) as router:
        router.post("https://llm.example/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": None, "reasoning_content": "deep"}}]},
            )
        )
        out = await INF._call_openai_compat("p", "https://llm.example/v1", "o1-mini", "k")
        assert out == {"text": "deep"}


# ── inference: JSON extraction/parsing helpers ────────────────────────


def test_extract_json_object_balanced_and_escaped():
    assert INF._extract_json_object('pre {"a": "\\"x\\""} post') == '{"a": "\\"x\\""}'
    assert INF._extract_json_object("no braces") is None
    assert INF._extract_json_object("{unclosed") is None


def test_parse_json_response_fallbacks():
    # fenced json block wins
    assert INF._parse_json_response('```json\n{"b": 2}\n```') == {"b": 2}
    # candidate json fails → balanced-brace extraction
    assert INF._parse_json_response('words {"c": 3} words') == {"c": 3}
    # unparseable inner object → regex {...} fallback fails too → 500
    with pytest.raises(HTTPException) as ei:
        INF._parse_json_response("totally not json")
    assert ei.value.status_code == 500
    with pytest.raises(HTTPException):
        INF._parse_json_response("{broken")


# ── inference: NVIDIA FLUX providers ─────────────────────────────────


def _b64_png() -> str:
    return base64.b64encode(b"\x89PNG-fake").decode()


@pytest.mark.asyncio
async def test_nvidia_flux_kontext_branches():
    import respx

    url = "https://ai.api.nvidia.com/v1/genimg/kontext"
    # image payload + success decode
    with respx.mock(assert_all_called=False) as router:
        post = router.post(url).mock(return_value=httpx.Response(200, json={"image": _b64_png()}))
        out = await INF._call_nvidia_flux("p", url, "k", image="data:image/png;base64,xx", cfg_scale=4.0)
        assert out == b"\x89PNG-fake"
        assert post.calls[0].request.read()  # payload posted

    # provider error → 502
    with respx.mock(assert_all_called=False) as router:
        router.post(url).mock(return_value=httpx.Response(500, text="boom"))
        with pytest.raises(HTTPException) as ei:
            await INF._call_nvidia_flux("p", url, "k")
        assert ei.value.status_code == 502

    # empty image → 502
    with respx.mock(assert_all_called=False) as router:
        router.post(url).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(HTTPException) as ei:
            await INF._call_nvidia_flux("p", url, "k")
        assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_nvidia_flux_dev_branches():
    import respx

    url = "https://ai.api.nvidia.com/v1/genimg/dev"
    with respx.mock(assert_all_called=False) as router:
        post = router.post(url).mock(return_value=httpx.Response(200, json={"image": _b64_png()}))
        out = await INF._call_nvidia_flux_dev("p", url, "k", negative_prompt="no blur")
        assert out == b"\x89PNG-fake"
        assert "no blur" in post.calls[0].request.content.decode()

    with respx.mock(assert_all_called=False) as router:
        router.post(url).mock(return_value=httpx.Response(400, text="bad"))
        with pytest.raises(HTTPException) as ei:
            await INF._call_nvidia_flux_dev("p", url, "k")
        assert ei.value.status_code == 502

    with respx.mock(assert_all_called=False) as router:
        router.post(url).mock(return_value=httpx.Response(200, json={"image": ""}))
        with pytest.raises(HTTPException) as ei:
            await INF._call_nvidia_flux_dev("p", url, "k")
        assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_local_sd35_branches():
    import respx

    url = "http://sd35.local/v1/genimg"
    # cfg_scale + success path (artifacts list)
    with respx.mock(assert_all_called=False) as router:
        post = router.post(url).mock(
            return_value=httpx.Response(
                200,
                json={"artifacts": [{"base64": _b64_png()}]},
            )
        )
        out = await INF._call_local_sd35("p", url, cfg_scale=6.0)
        assert out == b"\x89PNG-fake"
        assert "cfg_scale" in post.calls[0].request.content.decode()

    # error → 502
    with respx.mock(assert_all_called=False) as router:
        router.post(url).mock(return_value=httpx.Response(503, text="nim down"))
        with pytest.raises(HTTPException) as ei:
            await INF._call_local_sd35("p", url)
        assert ei.value.status_code == 502

    # no artifacts → 502
    with respx.mock(assert_all_called=False) as router:
        router.post(url).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(HTTPException) as ei:
            await INF._call_local_sd35("p", url)
        assert ei.value.status_code == 502

    # artifacts present but empty image → 502
    with respx.mock(assert_all_called=False) as router:
        router.post(url).mock(return_value=httpx.Response(200, json={"artifacts": [{"base64": ""}]}))
        with pytest.raises(HTTPException) as ei:
            await INF._call_local_sd35("p", url)
        assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_nvidia_flux_pipeline_branches(monkeypatch):
    import respx

    monkeypatch.setattr(INF, "_call_nvidia_flux_dev", AsyncMock(return_value=b"\x89PNG-init"))
    kurl = "https://ai.api.nvidia.com/v1/genimg/kontext-pipe"

    # happy path through step 2
    with respx.mock(assert_all_called=False) as router:
        router.post(kurl).mock(return_value=httpx.Response(200, json={"image": _b64_png()}))
        out = await INF._call_nvidia_flux_pipeline("p", "u1", "k1", kurl, "k2")
        assert out == b"\x89PNG-fake"

    # kontext error → 502
    with respx.mock(assert_all_called=False) as router:
        router.post(kurl).mock(return_value=httpx.Response(502, text="x"))
        with pytest.raises(HTTPException) as ei:
            await INF._call_nvidia_flux_pipeline("p", "u1", "k1", kurl, "k2")
        assert ei.value.status_code == 502

    # kontext no image → 502
    with respx.mock(assert_all_called=False) as router:
        router.post(kurl).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(HTTPException) as ei:
            await INF._call_nvidia_flux_pipeline("p", "u1", "k1", kurl, "k2")
        assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_get_team_id_for_user(monkeypatch):
    import app.api.deps as deps

    team = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(deps, "get_user_team", AsyncMock(return_value=team))
    assert await INF.get_team_id_for_user(uuid.uuid4(), db=None) == team.id

    monkeypatch.setattr(deps, "get_user_team", AsyncMock(return_value=None))
    assert await INF.get_team_id_for_user(uuid.uuid4(), db=None) is None


# ── telegram webhook: member welcome + join request + lead ───────────


import app.api.telegram as T  # noqa: E402


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        return self._v

    def all(self):
        return self._v if isinstance(self._v, list) else []


class _DB:
    def __init__(self, results=()):
        self._q = list(results)
        self.added = []
        self.committed = 0

    async def execute(self, stmt):
        return _Res(self._q.pop(0) if self._q else None)

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        pass

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        pass


class _WReq:
    def __init__(self, payload):
        self._p = payload

    async def json(self):
        return self._p


def _tg_account(**kw):
    meta = kw.pop(
        "meta_data",
        {"bot_token_enc": "enc", "bot_id": "1", "bot_username": "b", "webhook_secret": ""},
    )
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        team_id=kw.pop("team_id", uuid.uuid4()),
        platform="telegram",
        account_type="bot",
        account_id="12345",
        username="bot",
        display_name="Bot",
        status="active",
        meta_data=meta,
        access_token_enc=b"enc",
        **kw,
    )


_JOIN_UPDATE = {
    "update_id": 5,
    "chat_join_request": {
        "chat": {"id": -100, "type": "channel", "title": "HQ"},
        "from": {"id": 7, "username": "newbie", "first_name": "New"},
        "invite_link": {"name": "launch", "invite_link": "https://t.me/+x"},
    },
}

_MEMBER_UPDATE = {
    "update_id": 6,
    "chat_member": {
        "chat": {"id": -100, "type": "channel", "title": "HQ"},
        "old_chat_member": {"status": "left", "user": {"id": 7}},
        "new_chat_member": {
            "status": "member",
            "user": {"id": 7, "username": "newbie", "first_name": "N"},
        },
    },
}


def _approve_client(ok=True):
    c = Mock()
    c.approve_chat_join_request = AsyncMock(return_value=ok)
    return c


@pytest.mark.asyncio
async def test_webhook_join_request_approved_and_failed(monkeypatch):
    acc = _tg_account()
    monkeypatch.setattr(T, "agent_enabled", lambda a: True)
    monkeypatch.setattr(T, "_agent_cfg", lambda a: {"auto_approve": True})

    # approve ok → "approved" + event recorded + commit
    monkeypatch.setattr(T, "_client_for", lambda a: _approve_client(True))
    db = _DB([acc])
    out = await T.receive_webhook(acc.id, _WReq(dict(_JOIN_UPDATE)), db, None)
    assert out["join_request"] == "approved"
    assert db.committed >= 1

    # approve returns False → "approve_failed"
    acc2 = _tg_account()
    monkeypatch.setattr(T, "_client_for", lambda a: _approve_client(False))
    out2 = await T.receive_webhook(acc2.id, _WReq(dict(_JOIN_UPDATE)), _DB([acc2]), None)
    assert out2["join_request"] == "approve_failed"


@pytest.mark.asyncio
async def test_webhook_join_request_pending_and_error(monkeypatch):
    acc = _tg_account()
    # agent disabled → pending_manual
    monkeypatch.setattr(T, "agent_enabled", lambda a: False)
    out = await T.receive_webhook(acc.id, _WReq(dict(_JOIN_UPDATE)), _DB([acc]), None)
    assert out["join_request"] == "pending_manual"

    # agent enabled but processing raises → "error"
    acc2 = _tg_account()
    monkeypatch.setattr(T, "agent_enabled", lambda a: True)
    monkeypatch.setattr(T, "_agent_cfg", lambda a: {"auto_approve": True})

    def _boom(a):
        raise RuntimeError("client build failed")

    monkeypatch.setattr(T, "_client_for", _boom)
    out2 = await T.receive_webhook(acc2.id, _WReq(dict(_JOIN_UPDATE)), _DB([acc2]), None)
    assert out2["join_request"] == "error"


@pytest.mark.asyncio
async def test_webhook_member_welcome_failure(monkeypatch):
    acc = _tg_account()
    monkeypatch.setattr(T, "notify_channel_event", AsyncMock(return_value=True))
    monkeypatch.setattr(T, "_client_for", lambda a: Mock())
    monkeypatch.setattr(T, "maybe_welcome_member", AsyncMock(side_effect=RuntimeError("boom")))
    out = await T.receive_webhook(acc.id, _WReq(dict(_MEMBER_UPDATE)), _DB([acc]), None)
    assert out["member_event"] == "member_joined"
    assert out["welcomed"] is False


@pytest.mark.asyncio
async def test_webhook_lead_capture_completed(monkeypatch):
    acc = _tg_account()
    lead_id = uuid.uuid4()
    reply = SimpleNamespace(text="thanks!", completed_lead_id=lead_id)

    async def _snapshot(*a, **kw):
        return SimpleNamespace()

    import app.services.lead_capture as lc_mod

    monkeypatch.setattr(T, "_lead_enabled_and_active", AsyncMock(return_value=True))
    monkeypatch.setattr(lc_mod, "snapshot_lead_session", _snapshot)
    monkeypatch.setattr(T, "_run_lead_capture", AsyncMock(return_value=reply))
    monkeypatch.setattr(T, "_deliver_lead_reply", AsyncMock())
    monkeypatch.setattr(T, "_client_for", lambda a: Mock())

    update = {
        "update_id": 9,
        "message": {
            "message_id": 1,
            "chat": {"id": 55, "type": "private"},
            "from": {"id": 55, "username": "u"},
            "text": "audit",
            "date": 1,
        },
    }
    out = await T.receive_webhook(acc.id, _WReq(update), _DB([acc]), None)
    assert out["lead_capture"] is True
    assert out["lead_id"] == str(lead_id)


# ── linkedin_ads_report remaining branches ───────────────────────────


import app.services.analytics_sync as A  # noqa: E402
import app.services.linkedin_ads_report as R  # noqa: E402


def _metrics(**kw):
    m = R.CampaignMetrics(account_id="a", campaign_id="c")
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def _http_resp(status=200, body=None):
    return httpx.Response(status, json=body or {})


class _Http:
    def __init__(self, responses):
        self._q = list(responses)
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.urls.append(url)
        return self._q.pop(0) if self._q else _http_resp(200, {})

    async def post(self, url, **kw):
        self.urls.append(url)
        return self._q.pop(0) if self._q else _http_resp(200, {})


@pytest.mark.asyncio
async def test_collect_api_metrics_campaigns_list_error(monkeypatch):
    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {})
    # group_id "x" is non-digit → group fetch skipped; 2nd GET = campaigns list
    campaign = {
        "name": "B",
        "status": "ACTIVE",
        "runSchedule": {},
        "campaignGroup": "urn:li:adCampaignGroup:x",
    }
    http = _Http([_http_resp(200, campaign), _http_resp(503, {})])
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: http)
    monkeypatch.setattr(A, "_fetch_linkedin_ad_stats", AsyncMock(return_value={}))
    m = R.CampaignMetrics(account_id="a", campaign_id="c1")
    await R._collect_api_metrics(_DB([]), m)
    assert m.raw["campaigns_note"] == "adCampaigns list HTTP 503"


@pytest.mark.asyncio
async def test_collect_api_metrics_stats_error(monkeypatch):
    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {})
    campaign = {
        "name": "B",
        "status": "ACTIVE",
        "runSchedule": {},
        "campaignGroup": "x",
    }
    http = _Http([_http_resp(200, campaign), _http_resp(200, {"elements": []})])
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: http)
    monkeypatch.setattr(
        A,
        "_fetch_linkedin_ad_stats",
        AsyncMock(return_value={"_error": SimpleNamespace(notes="denied")}),
    )
    m = R.CampaignMetrics(account_id="a", campaign_id="c1")
    with pytest.raises(RuntimeError, match="adAnalytics: denied"):
        await R._collect_api_metrics(_DB([]), m)


def test_report_engagement_rate_and_budget_reached():
    end = date.today() + timedelta(days=30)
    # engagement_rate stat line
    m = _metrics(status="active", engagement_rate=2.5)
    assert "ER 2.50%" in R.build_report_text(m, None, None)

    # spend >= budget → "lifetime budget reached"
    m2 = _metrics(
        status="active",
        spend_eur=120.0,
        budget_eur=100.0,
        schedule_start=(date.today() - timedelta(days=5)).isoformat(),
        schedule_end=end.isoformat(),
    )
    assert "lifetime budget reached" in R.build_report_text(m2, None, end)


def test_report_uncapped_and_other_remaining(monkeypatch):
    settings = R.get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_ADS_CREDIT_EUR", 100.0, raising=False)
    end = date.today() + timedelta(days=30)
    m = _metrics(
        status="active",
        spend_eur=10.0,
        budget_eur=50.0,
        schedule_start=(date.today() - timedelta(days=1)).isoformat(),
        schedule_end=end.isoformat(),
    )
    m.raw["campaigns"] = [
        {"id": "c", "name": "Mine", "status": "active", "budget": 50.0},
        {"id": "c2", "name": "UncappedCo", "status": "active", "budget": None},
        {"id": "c3", "name": "Other", "status": "paused", "budget": 40.0},
        {"id": "c4", "name": "Done", "status": "completed", "budget": 10.0},
    ]
    m.raw["spend_by_campaign"] = {"c": 10.0, "c3": 5.0}
    out = R.build_report_text(m, None, end)
    assert "unverified" in out and "UncappedCo" in out


@pytest.mark.asyncio
async def test_run_daily_report_bad_end_date(monkeypatch):
    settings = R.get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_ADS_END_DATE", "not-a-date", raising=False)

    db = _DB([])

    class _SM:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(R, "async_session_maker", lambda: _SM())
    monkeypatch.setattr(R, "_get_team_id", AsyncMock(return_value=None))
    out = await R.run_daily_report()
    assert out["error"] == "no team"


def _daily_report_harness(monkeypatch, *, rendered, org_section_error=None, email_error=None):
    settings = R.get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_ADS_CAMPAIGN_ID", "c1", raising=False)
    monkeypatch.setattr(settings, "LINKEDIN_ADS_END_DATE", "", raising=False)
    monkeypatch.setattr(settings, "LINKEDIN_ADS_EMAIL_TO", "", raising=False)
    monkeypatch.setattr(settings, "DIGEST_EMAIL_TO", "ops@x.io", raising=False)
    monkeypatch.setattr(settings, "SLACK_ADS_WEBHOOK_URL", "", raising=False)
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", "", raising=False)
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(settings, "SLACK_ACCESS_TOKEN", "", raising=False)
    monkeypatch.setattr(settings, "SLACK_ADS_CHANNEL_ID", "", raising=False)
    monkeypatch.setattr(settings, "SLACK_CHANNEL_ID", "", raising=False)

    db = _DB([])

    class _SM:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(R, "async_session_maker", lambda: _SM())
    monkeypatch.setattr(R, "_get_team_id", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(R, "_final_sent", AsyncMock(return_value=False))
    monkeypatch.setattr(R, "_latest_snapshot", AsyncMock(return_value=None))
    metrics = _metrics(status="active", spend_eur=15.0, clicks=5, budget_eur=100.0)
    monkeypatch.setattr(R, "collect_metrics", AsyncMock(return_value=metrics))
    monkeypatch.setattr(R, "_render_report_notebook", AsyncMock(return_value=rendered))
    if org_section_error:
        monkeypatch.setattr(R, "build_org_section", AsyncMock(side_effect=RuntimeError("org fail")))
    else:
        monkeypatch.setattr(R, "build_org_section", AsyncMock(return_value=""))
    monkeypatch.setattr(R, "_post_slack_text", AsyncMock(return_value=(True, None, None)))
    if email_error:
        monkeypatch.setattr(R, "send_email_smtp", Mock(side_effect=RuntimeError("smtp down")))
    else:
        monkeypatch.setattr(R, "send_email_smtp", Mock())
    return db


@pytest.mark.asyncio
async def test_run_daily_report_rendered_tuple_path(monkeypatch):
    _daily_report_harness(monkeypatch, rendered=("report text", "<html>r</html>", [{"f": 1}]))
    out = await R.run_daily_report()
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_run_daily_report_org_section_failure(monkeypatch):
    _daily_report_harness(monkeypatch, rendered=None, org_section_error=True)
    out = await R.run_daily_report()
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_run_daily_report_email_failure(monkeypatch):
    _daily_report_harness(monkeypatch, rendered=None, email_error=True)
    out = await R.run_daily_report()
    assert out["ok"] is True
    assert out.get("email_error") or out.get("email_err")


# ── brand api remaining branches ─────────────────────────────────────


import app.api.brand as B  # noqa: E402


def _brand(**kw):
    b = SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        name="Cloudless",
        industry="tech",
        positioning_statement="pos",
        mission="m",
        values=["v1"],
        tagline="tag",
        target_audience={},
        website_url="https://cloudless.gr",
        voice=None,
        visual=None,
        guidelines=None,
        assets=[],
    )
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def _visual():
    return SimpleNamespace(
        primary_color="#00fff5",
        accent_color="#ff00ff",
        logo_url="/logo.png",
        logo_variants={},
    )


def _team():
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_get_brand_reraises_non_404(monkeypatch):
    async def _boom(team_id, db):
        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(B, "_get_brand", _boom)
    with pytest.raises(HTTPException) as ei:
        await B.get_brand(uuid.uuid4(), None, _DB([]))
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_score_compliance_failure_maps_500(monkeypatch):
    brand = _brand(
        voice=SimpleNamespace(
            tone_dimensions={},
            messaging_pillars=[],
            banned_phrases=[],
            preferred_phrases=[],
            example_content=None,
            voice_signature={},
        )
    )
    monkeypatch.setattr(B, "_get_brand", AsyncMock(return_value=brand))

    async def _boom(**kw):
        raise RuntimeError("scorer dead")

    monkeypatch.setattr("app.services.brand_compliance.score_brand_compliance", _boom)
    with pytest.raises(HTTPException) as ei:
        await B.score_compliance(
            B.ComplianceRequest(content="c", platform="linkedin"),
            uuid.uuid4(),
            None,
            _DB([]),
        )
    assert ei.value.status_code == 500


@pytest.mark.asyncio
async def test_generate_logo_color_scheme_and_non_image_model(monkeypatch):
    import app.services.inference as inf_mod
    import app.services.media_storage as ms_mod

    brand = _brand(visual=_visual())
    monkeypatch.setattr(B, "_get_brand", AsyncMock(return_value=brand))
    monkeypatch.setattr(B, "_get_team", AsyncMock(return_value=_team()))
    # provider returns a text model → falls back to CF_TXT2IMG_FREE
    monkeypatch.setattr(
        inf_mod,
        "_get_provider_config",
        AsyncMock(return_value=("p", "@cf/meta/llama-3.1-8b-instruct", "key")),
    )
    img = AsyncMock(return_value={"image_base64": base64.b64encode(b"png").decode()})
    monkeypatch.setattr(inf_mod, "_call_workers_ai_image", img)
    monkeypatch.setattr(
        ms_mod,
        "persist_generated_image",
        AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
    )

    req = B.GenerateLogoRequest(color_scheme="dark navy and teal")
    out = await B.generate_brand_logo(req, uuid.uuid4(), SimpleNamespace(id=uuid.uuid4()), _DB([]))
    assert out.logo_url.startswith("/api/v1/media/view/")
    assert "dark navy and teal" in img.await_args.kwargs["prompt"]
    assert img.await_args.kwargs["model"].startswith("@cf/")


@pytest.mark.asyncio
async def test_generate_favicon_non_image_model_and_error(monkeypatch):
    import app.services.inference as inf_mod
    import app.services.media_storage as ms_mod

    brand = _brand(visual=_visual())
    monkeypatch.setattr(B, "_get_brand", AsyncMock(return_value=brand))
    monkeypatch.setattr(B, "_get_team", AsyncMock(return_value=_team()))
    monkeypatch.setattr(
        inf_mod,
        "_get_provider_config",
        AsyncMock(return_value=("p", "@cf/meta/llama-3.1-8b-instruct", "key")),
    )
    img = AsyncMock(return_value={"image_base64": base64.b64encode(b"png").decode()})
    monkeypatch.setattr(inf_mod, "_call_workers_ai_image", img)
    monkeypatch.setattr(
        ms_mod,
        "persist_generated_image",
        AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
    )
    out = await B.generate_brand_favicon(uuid.uuid4(), SimpleNamespace(id=uuid.uuid4()), _DB([]))
    assert img.await_args.kwargs["model"].startswith("@cf/")
    assert out.favicon_url

    # generation error → 502
    img.side_effect = RuntimeError("cf dead")
    with pytest.raises(HTTPException) as ei:
        await B.generate_brand_favicon(uuid.uuid4(), SimpleNamespace(id=uuid.uuid4()), _DB([]))
    assert ei.value.status_code == 502


# ── misc one/two-line stragglers ─────────────────────────────────────


def test_sidecar_file_path_uploads_absolute():
    from app.services.publishing import _sidecar_file_path

    assert _sidecar_file_path("/uploads/2024/01/i.jpg") == "/uploads/2024/01/i.jpg"
    assert _sidecar_file_path("/app/uploads/x.jpg") == "/uploads/x.jpg"


def test_telegram_group_watch_mention_entity():
    from app.services.telegram_group_watch import bot_mentioned

    assert bot_mentioned(
        "hi cloudlessbot",
        [{"type": "mention", "offset": 3, "length": 12}],
        "cloudlessbot",
    )


def test_whatsapp_human_error_fallback():
    from app.services.whatsapp_cloud_client import _human_error_message

    assert _human_error_message({"code": 1}, "fallback-msg") == "fallback-msg"


def test_blog_articles_empty_block_skipped():
    from app.services.blog_articles import article_markdown_to_html

    out = article_markdown_to_html("para one\n\n   \n\npara two")
    assert "para one" in out and "para two" in out


def test_x_web_to_jpeg_grayscale(tmp_path):
    from PIL import Image

    from app.services import x_web

    src = tmp_path / "gray.png"
    Image.new("L", (64, 64), 128).save(src)
    out = x_web._to_jpeg(str(src), str(tmp_path), 0)
    assert out.endswith(".jpg")
    assert Image.open(out).mode == "RGB"


def test_x_web_prepare_media_passthrough(tmp_path, monkeypatch):
    from app.services import x_web

    def _boom(src, dest, i):
        raise x_web.XWebMediaError("unfixable")

    monkeypatch.setattr(x_web, "_to_jpeg", _boom)
    plan = SimpleNamespace(paths=["/x/y.png"], kind="images")
    with pytest.raises(x_web.XWebMediaError, match="unfixable"):
        x_web.prepare_media(plan, str(tmp_path))


@pytest.mark.asyncio
async def test_x_web_publish_generic_error(monkeypatch):
    from app.services import x_web

    monkeypatch.setattr(x_web, "is_configured", lambda: True)
    monkeypatch.setattr(x_web, "plan_media", lambda p: SimpleNamespace(has_media=False))
    guard = SimpleNamespace(
        reserve_post_slot=AsyncMock(return_value=SimpleNamespace(ok=True, permanent=False)),
        trip=AsyncMock(),
    )

    async def _factory():
        raise RuntimeError("login exploded")

    monkeypatch.setattr(x_web, "classify_x_web_error", lambda e: None)
    out = await x_web.publish_via_x_web(
        expected_username="me",
        chunks=["hello"],
        media_paths=None,
        guard=guard,
        client_factory=_factory,
    )
    assert out.status == "error"
    assert "login exploded" in out.error


@pytest.mark.asyncio
async def test_comfyui_generate_image_cancelled(monkeypatch):
    import app.services.comfyui_image as CI

    class _Resp:
        status_code = 200
        content = b"x" * 2048

        def __init__(self, body=None):
            self._json = body or {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._json

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            return _Resp({"prompt_id": "pid"})

        async def get(self, url, **kw):
            raise asyncio.CancelledError()

    cancelled = []

    async def _cancel(client, prompt_id):
        cancelled.append(prompt_id)

    monkeypatch.setattr(CI.httpx, "AsyncClient", lambda **kw: _Client())
    monkeypatch.setattr(CI, "_cancel_prompt", _cancel)
    monkeypatch.setattr(CI.stack_ops, "keepawake", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await CI.generate_image(prompt="x", timeout_s=5, poll_s=0)
    assert cancelled == ["pid"]


def test_tiktok_upload_chunk_guards(monkeypatch):
    from app.services import tiktok_api

    # explicit chunk_size → >MAX_CHUNK_COUNT raises before any HTTP
    monkeypatch.setattr(tiktok_api, "MAX_CHUNK_COUNT", 1)
    client = tiktok_api.TikTokAPIClient(access_token="t", open_id="o")
    video = b"x" * (11 * 1024 * 1024)
    with pytest.raises(ValueError, match="1000 chunks"):
        asyncio.run(
            client.upload_video_file(
                "https://upload.us.tiktokapis.com/x",
                video,
                chunk_size=5 * 1024 * 1024,
            )
        )


@pytest.mark.asyncio
async def test_tiktok_upload_final_chunk_too_big(monkeypatch):
    from app.services import tiktok_api

    # whole-file upload → single chunk; shrink the cap so a small file trips it
    monkeypatch.setattr(tiktok_api, "MAX_FINAL_CHUNK_SIZE", 10)
    client = tiktok_api.TikTokAPIClient(access_token="t", open_id="o")
    video = b"x" * 64
    with pytest.raises(ValueError, match="final TikTok upload chunk"):
        await client.upload_video_file(
            "https://upload.us.tiktokapis.com/x",
            video,
            chunk_size=len(video),
        )


@pytest.mark.asyncio
async def test_messenger_sidecar_401_retry_fails(monkeypatch):
    import respx

    from app.services import messenger_sidecar as MS

    calls = {"n": 0}

    async def _token():
        calls["n"] += 1
        return "tok" if calls["n"] == 1 else ""

    monkeypatch.setattr(MS, "_get_admin_token", _token)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MS.SOCIAL_API_URL}/api/v1/accounts").mock(return_value=httpx.Response(401))
        assert await MS._get_account_and_config("page1") is None


def test_config_digest_hour_passthrough(monkeypatch):
    from app.core.config import Settings

    s = Settings(SLACK_BILLING_DIGEST_HOUR=9)
    assert s.SLACK_BILLING_DIGEST_HOUR == 9


def test_main_upload_dir_makedirs_oserror(monkeypatch):
    import importlib
    import os as _os

    import app.main as main_mod

    real_makedirs = _os.makedirs
    monkeypatch.setattr(_os, "makedirs", Mock(side_effect=OSError("ro fs")))
    monkeypatch.setenv("UPLOAD_DIR", "/nonexistent-dir-xyz")
    importlib.reload(main_mod)
    monkeypatch.setattr(_os, "makedirs", real_makedirs)
    monkeypatch.delenv("UPLOAD_DIR", raising=False)
    importlib.reload(main_mod)


def test_infographic_ai_background_blend():
    from PIL import Image

    from app.services import infographic_renderer as IR

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, format="PNG")
    out = IR.render_infographic(
        {"title": "T", "subtitle": "", "sections": [], "footer": ""},
        buf.getvalue(),
        width=400,
        height=400,
    )
    assert out[:4] == b"\x89PNG"
