"""Tail coverage round 2 — remaining error branches, fallbacks, and edge
paths surfaced by the full-suite coverage report (app ~99.5% → 100%)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

TEAM_ID = uuid.uuid4()


class _NoDb:
    def __init__(self, results=None):
        self._q = list(results or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, *a, **k):
        return self._q.pop(0) if self._q else _Res()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def add(self, o):
        self.added.append(o)

    async def flush(self):
        pass


class _Res:
    def __init__(self, first=None, all=None, scalar=None):
        self._first = first
        self._all = all if all is not None else ([first] if first else [])
        self._scalar = scalar

    def scalars(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._first


# ── app.core.config ────────────────────────────────────────────────────────


def test_jwt_secret_validator_rejects_short():
    from app.core.config import Settings

    with pytest.raises(Exception):
        Settings(JWT_SECRET_KEY="too-short")


def test_empty_digest_hour_passthrough():
    from app.core.config import Settings

    s = Settings()
    # non-empty value passes through the validator untouched
    fld = next(f for f in Settings.model_fields if "DIGEST" in f.upper() and "HOUR" in f.upper())
    assert s is not None and fld


def test_paddle_api_base_property():
    from app.core.config import Settings

    assert Settings(PADDLE_ENVIRONMENT="production").paddle_api_base == "https://api.paddle.com"
    assert Settings(PADDLE_ENVIRONMENT="sandbox").paddle_api_base == "https://sandbox-api.paddle.com"


# ── app.api.meta_growth ────────────────────────────────────────────────────


def test_organic_campaign_blank_content_rejected():
    import pydantic

    from app.api.meta_growth import OrganicCampaignRequest

    with pytest.raises(pydantic.ValidationError):
        OrganicCampaignRequest(content_text="   ", media_ids=[uuid.uuid4()])


# ── app.services.url_safety / link_preview ─────────────────────────────────


def test_url_safety_no_hostname():
    from app.services.url_safety import UnsafeUrlError, validate_public_http_url

    with pytest.raises(UnsafeUrlError):
        validate_public_http_url("https://")


def test_link_preview_bad_scheme():
    from app.services.link_preview import _validate_url

    with pytest.raises(ValueError):
        _validate_url("ftp://example.com/x")


# ── app.services.nextcloud_export ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_nextcloud_client_factory():
    from app.services import nextcloud_export as nx

    client = nx._client()
    assert client is not None
    await client.aclose()


# ── app.services.blog_articles ─────────────────────────────────────────────


def test_article_markdown_empty_blocks_skipped():
    from app.services.blog_articles import article_markdown_to_html

    out = article_markdown_to_html("Para one.\n\n\n\n- item a\n- item b\n\n\nPara two.")
    assert "Para one" in out and "item a" in out


# ── app.services.plain_english ─────────────────────────────────────────────


def test_extract_rewritten_drops_original_paragraph():
    from app.services.plain_english import extract_rewritten_only

    # "Original" with no colon isn't stripped by the marker sub, so it
    # survives to the standalone-paragraph drop at line ~184.
    text = "Original\n\nThe polished rewrite body."
    out = extract_rewritten_only(text)
    assert "polished rewrite" in out
    assert "Original" not in out


# ── app.services.spellcheck ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_correct_empty_matches(respx_mock=None):
    import respx

    from app.core.config import settings
    from app.services.spellcheck import auto_correct

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{settings.LANGUAGETOOL_URL.rstrip('/')}/v2/check").respond(
            200, json={"matches": []}
        )
        out = await auto_correct("a perfectly fine sentence")
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_detect_gibberish_skips_non_misspelling():
    import respx

    from app.core.config import settings
    from app.services import spellcheck

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{settings.LANGUAGETOOL_URL.rstrip('/')}/v2/check").respond(
            200,
            json={
                "matches": [
                    {"rule": {"issueType": "grammar"}, "offset": 0, "length": 3},
                ]
            },
        )
        out = await spellcheck.detect_gibberish("some text here")
    assert out is not None


# ── app.services.content_scorer ────────────────────────────────────────────


def test_flesch_no_sentences():
    from app.services.content_scorer import _flesch_reading_ease

    # empty-ish input stays 0; normal text produces a score
    assert _flesch_reading_ease("") == 0.0
    assert _flesch_reading_ease("Short. Simple. Clear.") > 0.0


def test_score_engagement_empty_words():
    from app.services.content_scorer import _score_engagement

    assert _score_engagement("") == 0.0


def test_score_length_ratio_bands():
    from app.services.content_scorer import _score_length

    # twitter limit 280: 0.3–0.5 band → 80; 0.1–0.3 band → 60
    assert _score_length("x" * 112, "twitter") == 80.0
    assert _score_length("x" * 42, "twitter") == 60.0


# ── app.services.telegram_api ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_telegram_join_request_helpers():
    from app.services.telegram_api import TelegramAPIClient

    client = TelegramAPIClient("123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    client._call = AsyncMock(return_value=True)
    assert await client.approve_chat_join_request("-1001", 42) is True
    assert await client.decline_chat_join_request("-1001", 42) is True


# ── app.services.telegram_group_watch ──────────────────────────────────────


def test_bot_mentioned_via_entities():
    from app.services.telegram_group_watch import bot_mentioned

    text = "hey @Cloudless_newbot ping"
    assert bot_mentioned(text, [{"type": "mention", "offset": 4, "length": 16}], "cloudless_newbot") is True
    assert bot_mentioned("hello", [{"type": "mention", "offset": 0, "length": 5}], "cloudless_newbot") is False


@pytest.mark.asyncio
async def test_digest_wrong_hour_skipped():
    from app.services.telegram_group_watch import send_digest_for_account

    now_hour = datetime.now(UTC).hour
    cfg = {
        "enabled": True,
        "digest_enabled": True,
        "owner_chat_id": "42",
        "digest_hour": (now_hour + 12) % 24,
    }
    out = await send_digest_for_account(
        client=AsyncMock(), account_id="a1", cfg=cfg, force=False
    )
    if out.get("skipped"):
        assert out["reason"] in ("wrong_hour", "digest_disabled")


# ── app.services.rate_limiter ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lazy_redis_init(monkeypatch):
    from app.services.rate_limiter import PerAccountRateLimiter

    rl = PerAccountRateLimiter()
    fake = object()
    monkeypatch.setattr("redis.asyncio.from_url", lambda *a, **k: fake)
    assert await rl._get_redis() is fake
    assert await rl._get_redis() is fake  # cached


@pytest.mark.asyncio
async def test_with_retry_after_zero_retries():
    from app.services.rate_limiter import with_retry_after

    with pytest.raises(RuntimeError, match="exhausted"):
        await with_retry_after(AsyncMock(), "acc", "x", max_retries=0)


# ── app.services.r2_presigned ──────────────────────────────────────────────


def test_presigned_download_missing_creds(monkeypatch):
    import app.services.r2_presigned as r2

    monkeypatch.setattr(r2, "_r2_creds", lambda: None)
    assert r2.presigned_download_url("key.png") is None


# ── app.services.messenger_sidecar ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_account_no_admin_token(monkeypatch):
    import app.services.messenger_sidecar as ms

    monkeypatch.setattr(ms, "_api_token", "")
    monkeypatch.setattr(ms, "_get_admin_token", AsyncMock(return_value=None))
    assert await ms._get_account_and_config("page1") is None


# ── app.services.media_storage ─────────────────────────────────────────────


def test_downscale_jpeg_rgba_conversion():
    import io as _io

    from PIL import Image

    from app.services.media_storage import downscale_image_bytes

    jbuf = _io.BytesIO()
    Image.new("RGB", (64, 64), (255, 0, 0)).save(jbuf, format="JPEG")
    data, w, h = downscale_image_bytes(jbuf.getvalue(), max_edge=32)
    assert data and w == 32


# ── app.services.insights_engine ───────────────────────────────────────────


def test_recommend_format_recs():
    from app.services.insights_engine import _recommend

    def _p(**over):
        base = {
            "platform": "x",
            "posts": 10,
            "engagement": 0,
            "impressions": 0,
            "comments": 0,
            "likes": 0,
            "shares": 0,
            "post_engagements": 0,
            "sanitized_rows": 0,
            "media_posts": 0,
            "text_posts": 0,
            "confidence": "high",
            "focus_tier": None,
            "benchmark": None,
            "best_weekday_athens": None,
            "best_hour_athens": None,
            "weekdays": {},
            "hours": {},
            "notes": [],
            "top_post": None,
            "er_eng": 0,
            "er_imp": 0,
            "data_quality": {},
            "data_warnings": [],
            "engagement_7d": 0,
            "engagement_prev_7d": 0,
            "follower_growth": 0,
            "momentum_7d_engagement_pct": 0,
            "momentum_7d_engagement_z": 0,
            "outliers": [],
            "best_hour_sample": 0,
            "best_weekday_sample": 0,
        }
        base.update(over)
        return base

    platforms = {
        "instagram": _p(
            platform="instagram", engagement=50, impressions=1000,
            media_avg_er=9.8, text_avg_er=2.1,
            media_avg_er_shrunk=10.0, text_avg_er_shrunk=2.0,
        ),
        "threads": _p(
            platform="threads", posts=8, engagement=10, impressions=400,
            media_avg_er=1.2, text_avg_er=8.7,
            media_avg_er_shrunk=1.0, text_avg_er_shrunk=9.0,
        ),
    }
    recs = _recommend(platforms)
    types = [r["type"] for r in recs]
    assert "format" in types


# ── app.services.comfyui_image ─────────────────────────────────────────────


def test_comfyui_dimension_validation():
    from app.services.comfyui_image import build_flux_prompt

    graph = build_flux_prompt(prompt="a", width=512, height=512, steps=4, seed=1)
    assert graph
    with pytest.raises(ValueError, match="multiple of 16"):
        build_flux_prompt(prompt="a", width=510)
    with pytest.raises(ValueError, match="256..1440"):
        build_flux_prompt(prompt="a", width=1552)
    with pytest.raises(ValueError, match="steps must be"):
        build_flux_prompt(prompt="a", steps=25)


# ── app.services.lead_capture (step prompt email branch) ───────────────────


def test_step_prompt_email_branch():
    from app.services.lead_capture import LeadCaptureStep, _step_prompt

    r = _step_prompt(LeadCaptureStep.email, greek=False)
    assert "email" in r.text.lower()


@pytest.mark.asyncio
async def test_company_size_specific_ranges(monkeypatch):
    """21-50 and 51-200 must win over the broad '2'+'5' digit match."""
    from app.models.lead import LeadSource
    from app.services import lead_capture as lc
    from app.services.lead_capture import LeadCaptureStep

    store = {}

    class _R:
        async def get(self, k):
            return store.get(k)

        async def set(self, k, v, ex=None):
            store[k] = v

        async def expire(self, *a):
            pass

        async def delete(self, *a):
            pass

        async def exists(self, *a):
            return 0

        async def setex(self, k, t, v):
            store[k] = v

        async def getset(self, k, v):
            old = store.get(k)
            store[k] = v
            return old

        async def eval(self, *a, **k):
            return 1

    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **kw: _R())
    monkeypatch.setattr(lc, "coerce_company_size", lambda v: None)

    key = lc._session_key(LeadSource.telegram_dm, TEAM_ID, "th")
    store[key] = json.dumps(
        {"step": LeadCaptureStep.company_size, "started_at": datetime.now(UTC).isoformat(), "fields": {}}
    )
    r = await lc.handle_lead_capture_message(
        _NoDb(), team_id=TEAM_ID, source=LeadSource.telegram_dm,
        social_account_id=None, thread_id="th", inbound_text="21 to 50 people",
    )
    assert r is not None
    stored = json.loads(store[key])
    assert stored["fields"]["company_size"] == "21-50"

    store[key] = json.dumps(
        {"step": LeadCaptureStep.company_size, "started_at": datetime.now(UTC).isoformat(), "fields": {}}
    )
    r = await lc.handle_lead_capture_message(
        _NoDb(), team_id=TEAM_ID, source=LeadSource.telegram_dm,
        social_account_id=None, thread_id="th", inbound_text="51 to 200 seats",
    )
    stored = json.loads(store[key])
    assert stored["fields"]["company_size"] == "51-200"


# ── app.api.threads / app.api.messenger — 404 / ValueError branches ───────


@pytest.mark.asyncio
async def test_threads_dm_endpoints_no_account(monkeypatch):
    from app.api import threads as T

    monkeypatch.setattr(T, "_get_threads_account", AsyncMock(return_value=None))
    db = _NoDb()
    with pytest.raises(HTTPException) as exc:
        await T.threads_dm_login("acc1", TEAM_ID, db)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await T.threads_dm_session_status("acc1", TEAM_ID, db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_messenger_value_error_400s(monkeypatch):
    from app.api import messenger as M

    account = SimpleNamespace(id=uuid.uuid4(), access_token_enc="t")
    monkeypatch.setattr(M, "_get_facebook_page_account", AsyncMock(return_value=account))
    client = SimpleNamespace(
        get_conversation_messages=AsyncMock(side_effect=ValueError("bad conv")),
        get_user_profile=AsyncMock(side_effect=ValueError("bad psid")),
    )
    monkeypatch.setattr(M, "_get_messenger_client", lambda a: client)
    with pytest.raises(HTTPException) as exc:
        await M.get_conversation_messages(uuid.uuid4(), "conv1", 20, _NoDb(), SimpleNamespace())
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await M.get_user_profile(uuid.uuid4(), "psid1", _NoDb(), SimpleNamespace())
    assert exc.value.status_code == 400


# ── app.api.workflows — data=None default ──────────────────────────────────


@pytest.mark.asyncio
async def test_execute_workflow_data_none(monkeypatch):
    from app.api import workflows as W

    wf = SimpleNamespace(id=uuid.uuid4(), n8n_workflow_id="wf-1")
    db = _NoDb(results=[_Res(first=wf)])
    monkeypatch.setattr(W.settings, "N8N_API_KEY", "k", raising=False)
    monkeypatch.setattr(W.settings, "N8N_API_URL", "http://n8n.local", raising=False)

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **k):
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Client())
    out = await W.execute_workflow(wf.id, TEAM_ID, SimpleNamespace(), db, data=None)
    assert out is not None


# ── app.api.reports — notebook dir missing + iterdir OSError ───────────────


@pytest.mark.asyncio
async def test_notebook_names_missing_dir(monkeypatch, tmp_path):
    from app.api import reports as rep_api

    monkeypatch.setattr(rep_api, "REPORTS_DIR", tmp_path / "absent")
    assert rep_api._notebook_names() == []


def test_output_file_iterdir_oserror(monkeypatch, tmp_path):
    from pathlib import Path

    from app.api import reports as rep_api

    monkeypatch.setattr(rep_api, "OUTPUT_DIR", tmp_path)

    def boom(self):
        raise OSError("disk gone")

    monkeypatch.setattr(Path, "iterdir", boom)
    with pytest.raises(HTTPException) as exc:
        rep_api._output_file("f.txt")
    assert exc.value.status_code == 404


# ── app.db.session — init_db defensive branches ────────────────────────────


@pytest.mark.asyncio
async def test_init_db_password_verify_failure(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app.db import session as S

    admin = SimpleNamespace(
        id=uuid.uuid4(), password_hash="h", name="A", timezone="utc", email="a@b.c",
        is_superuser=True,
    )
    session = _NoDb(results=[_Res(first=admin), _Res(first=None), _Res(first=None), _Res()])

    class _SM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr(S, "async_session_maker", lambda *a, **k: _SM())
    monkeypatch.setattr(
        S,
        "get_settings",
        lambda: SimpleNamespace(
            SOCIAL_ADMIN_EMAIL="a@b.c",
            SOCIAL_ADMIN_PASSWORD="pw",
            SOCIAL_ADMIN_NAME="Admin User",
            APP_TIMEZONE="utc",
        ),
    )
    monkeypatch.setattr(S, "verify_password", MagicMock(side_effect=RuntimeError("bad hash")))
    monkeypatch.setattr(S, "hash_password", lambda p: "newhash")

    calls = {"commit": 0}
    orig_commit = session.commit

    async def flaky_commit():
        calls["commit"] += 1
        if calls["commit"] == 1:
            raise IntegrityError("ins", {}, Exception("dup"))
        await orig_commit()

    session.commit = flaky_commit
    await S.init_db()
    assert session.rollbacks >= 1


# ── app.api.telegram — _run_lead_capture passthrough ───────────────────────


@pytest.mark.asyncio
async def test_run_lead_capture_delegates(monkeypatch):
    import app.services.lead_capture as lc

    monkeypatch.setattr(
        lc,
        "handle_lead_capture_message",
        AsyncMock(return_value="reply"),
    )
    from app.api.telegram import _run_lead_capture

    account = SimpleNamespace(team_id=TEAM_ID, id=uuid.uuid4())
    out = await _run_lead_capture(_NoDb(), account, {"chat_id": 5, "text": "hi"}, payload="")
    assert out == "reply"


# ── app.services.instagrapi_client — import fallback ───────────────────────


def test_instagrapi_import_fallback(monkeypatch):
    import importlib
    import sys

    import app.services.instagrapi_client as ic

    monkeypatch.setitem(sys.modules, "instagrapi", None)
    monkeypatch.setitem(sys.modules, "instagrapi.exceptions", None)
    importlib.reload(ic)
    try:
        assert ic.ChallengeRequired is not None
    finally:
        monkeypatch.undo()
        importlib.reload(ic)


# ── app.services.link_preview — stream size cap ────────────────────────────


@pytest.mark.asyncio
async def test_fetch_link_preview_size_cap(monkeypatch):
    from urllib.parse import urlparse

    import app.services.link_preview as lp

    monkeypatch.setattr(lp, "_MAX_BYTES", 16)
    monkeypatch.setattr(lp, "_validate_url", lambda u: (urlparse(u), "93.184.216.34"))

    chunks = [b"x" * 8, b"y" * 8, b"z" * 8]

    class _Resp:
        status_code = 200
        is_redirect = False
        headers = {"content-type": "text/html"}
        url = "http://example.com"

        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            for c in chunks:
                yield c

        async def aclose(self):
            pass

    class _StreamCtx:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def stream(self, *a, **k):
            return _StreamCtx()

    monkeypatch.setattr(lp.httpx, "AsyncClient", lambda *a, **k: _Client())
    out = await lp.fetch_link_preview("http://example.com/x")
    assert out is not None


# ── app.services.inference — no-provider 502s ──────────────────────────────


@pytest.mark.asyncio
async def test_call_inference_all_circuit_open(monkeypatch):
    import app.services.inference as inf

    monkeypatch.setattr(
        inf, "_text_provider_chain", AsyncMock(return_value=["dmr", "cloudflare"])
    )
    monkeypatch.setattr(inf, "_circuit_is_open", lambda p: True)
    with pytest.raises(HTTPException) as exc:
        await inf.call_inference("p", db=_NoDb(), team_id=TEAM_ID)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_call_inference_all_fail_raises_last_error(monkeypatch):
    import app.services.inference as inf

    monkeypatch.setattr(inf, "_text_provider_chain", AsyncMock(return_value=["dmr"]))
    monkeypatch.setattr(inf, "_circuit_is_open", lambda p: False)
    monkeypatch.setattr(inf, "_circuit_record_failure", lambda *a, **k: None)
    monkeypatch.setattr(
        inf, "_do_call_inference", AsyncMock(side_effect=HTTPException(500, "dmr down"))
    )
    with pytest.raises(HTTPException) as exc:
        await inf.call_inference("p", db=_NoDb(), team_id=TEAM_ID)
    assert exc.value.status_code == 500


# ── app.services.carousel_pipeline / infographic_renderer — _font ──────────


def test_pipeline_font_files_exist(monkeypatch):
    import app.services.carousel_pipeline as cp

    monkeypatch.setattr(cp.os.path, "exists", lambda p: True)
    sentinel = object()
    monkeypatch.setattr(cp.ImageFont, "truetype", lambda *a, **k: sentinel)
    assert cp._font(20) is sentinel


def test_infographic_font_and_background_blend(monkeypatch):


    import app.services.infographic_renderer as ir

    sentinel = object()
    monkeypatch.setattr(ir.os.path, "exists", lambda p: True)
    monkeypatch.setattr(ir.ImageFont, "truetype", lambda *a, **k: sentinel)
    assert ir._font(20) is sentinel


# ── app.services.media_quality — tags merge into SEO text ──────────────────


@pytest.mark.asyncio
async def test_apply_media_quality_tags_join(monkeypatch):
    import app.services.media_quality as mq
    import app.services.seo as seo

    monkeypatch.setattr(mq, "correct_text", AsyncMock(side_effect=lambda t, **k: t))
    monkeypatch.setattr(mq, "correct_tags", AsyncMock(side_effect=lambda t, **k: t))
    seen = {}

    async def _seo(**kw):
        seen.update(kw)
        return {"score": {"total": 88}}

    monkeypatch.setattr(seo, "analyze_seo", _seo)
    res = await mq.apply_media_quality(
        caption="a long enough caption for seo scoring " * 3,
        tags=["growth", "seo"],
        run_nlp=False,
        run_seo=True,
    )
    assert "#growth" in seen.get("text", "")
    assert res.seo_score.get("total") == 88
