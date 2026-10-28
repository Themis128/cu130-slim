"""Tail-coverage tests — reachable branches across small modules that the
feature suites don't exercise: helpers, fallbacks, error paths, and worker
export glue. Stubs sit at module boundaries; the code runs for real.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

# ── app.core.security ─────────────────────────────────────────────────────


def test_fernet_rejects_wrong_key_length(monkeypatch):
    import app.core.security as sec

    monkeypatch.setattr(sec, "_fernet", None)
    monkeypatch.setattr(sec.settings, "ENCRYPTION_KEY", "too-short", raising=False)
    with pytest.raises(ValueError, match="32 bytes"):
        sec.get_fernet()


def test_oauth_state_tampered_signature_returns_none():
    import base64 as b64
    import json as _json

    from app.core.security import sign_oauth_state, verify_oauth_state

    state = sign_oauth_state({"team_id": "t1"})
    raw = b64.urlsafe_b64decode(state + "=" * (-len(state) % 4))
    env = _json.loads(raw)
    env["p"]["team_id"] = "tampered"
    forged = b64.urlsafe_b64encode(_json.dumps(env).encode()).rstrip(b"=").decode()
    assert verify_oauth_state(forged) is None
    assert verify_oauth_state(state)["team_id"] == "t1"


def test_decrypt_field_enc_prefix_failure_and_plain():
    from app.core.security import decrypt_field

    assert decrypt_field("enc:not-a-real-fernet-token") is None
    assert decrypt_field("plaintext") == "plaintext"
    assert decrypt_field(None) is None


# ── app.services.platforms.base ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_base_platform_methods_are_noops():
    from app.services.platforms.base import PlatformDriver

    class _D(PlatformDriver):
        pass

    p = _D()
    assert p.platform is None
    assert await p.publish(SimpleNamespace(), SimpleNamespace(), None) is None
    assert await p.delete(SimpleNamespace(), "x") is None
    assert await p.get_follower_count(SimpleNamespace()) is None


# ── app.services.r2_presigned ─────────────────────────────────────────────


def test_r2_host_default_and_endpoint(monkeypatch):
    import app.services.r2_presigned as r2

    monkeypatch.setattr(r2.settings, "R2_S3_ENDPOINT", "", raising=False)
    monkeypatch.setattr(r2.settings, "CLOUDFLARE_ACCOUNT_ID", "acct123", raising=False)
    assert r2._r2_host() == "acct123.r2.cloudflarestorage.com"
    monkeypatch.setattr(r2.settings, "R2_S3_ENDPOINT", "https://custom.example.com/", raising=False)
    assert r2._r2_host() == "custom.example.com"


def test_public_url_variants(monkeypatch):
    import app.services.r2_presigned as r2

    monkeypatch.setattr(r2.settings, "R2_PUBLIC_URL", "", raising=False)
    assert r2._public_url("k") is None
    monkeypatch.setattr(r2.settings, "R2_PUBLIC_URL", "https://pub.example.com", raising=False)
    assert r2._public_url("k") == "https://pub.example.com/k"


def test_presigned_upload_missing_creds_returns_none(monkeypatch):
    import app.services.r2_presigned as r2

    monkeypatch.setattr(r2, "_r2_creds", lambda: None)
    monkeypatch.setattr(r2.settings, "R2_BUCKET_NAME", "bkt", raising=False)
    assert r2.presigned_upload_url(uuid.uuid4(), "f.png", "image/png", 1024) is None
    monkeypatch.setattr(r2, "_r2_creds", lambda: ("ak", "sk"))
    monkeypatch.setattr(r2.settings, "R2_BUCKET_NAME", "", raising=False)
    assert r2.presigned_upload_url(uuid.uuid4(), "f.png", "image/png", 1024) is None


# ── app.services.spellcheck ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_correct_empty_text():
    from app.services.spellcheck import auto_correct

    assert await auto_correct("") == ""
    assert await auto_correct("   ") == "   "


# ── app.services.rate_limiter ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_cooled_down(monkeypatch):
    from app.services.rate_limiter import PerAccountRateLimiter

    rl = PerAccountRateLimiter()

    class _R:
        async def exists(self, k):
            return 1 if "tiktok" in k else 0

    rl._get_redis = AsyncMock(return_value=_R())  # type: ignore[attr-defined]
    assert await rl.is_cooled_down("acc1", "tiktok") is True
    assert await rl.is_cooled_down("acc1", "twitter") is False


# ── app.services.content_scorer ───────────────────────────────────────────


def test_flesch_degenerate_inputs():
    from app.services.content_scorer import _flesch_reading_ease

    assert _flesch_reading_ease("") == 0.0


def test_has_stats_signal_year():
    from app.services.content_scorer import _has_stats_signal

    assert _has_stats_signal("In 2026 we shipped") is True
    assert _has_stats_signal("plain words only") is False


def test_score_length_boundaries():
    from app.services.content_scorer import _score_length

    # empty and very long text hit the boundary branches
    assert _score_length("", "twitter") >= 0.0
    assert _score_length("x " * 2000, "twitter") >= 0.0
    assert isinstance(_score_length("medium length post body", "linkedin"), float)


# ── app.services.image_transform ──────────────────────────────────────────


def test_save_image_avif_fallback(monkeypatch):
    from PIL import Image

    import app.services.image_transform as it

    img = Image.new("RGB", (8, 8))

    real_save = Image.Image.save

    def fake_save(self, fp, format=None, **kw):
        if format == "AVIF":
            raise RuntimeError("no avif codec")
        return real_save(self, fp, format=format, **kw)

    monkeypatch.setattr(Image.Image, "save", fake_save)
    data, mime = it._save_image(img, "AVIF", 80)
    assert mime == "image/webp" and data


def test_save_image_unsupported_raises():
    from PIL import Image

    from app.services.image_transform import _save_image

    with pytest.raises(ValueError, match="Unsupported format"):
        _save_image(Image.new("RGB", (8, 8)), "TIFF", 80)


def test_watermark_font_fallback(monkeypatch):
    from PIL import Image, ImageFont

    import app.services.image_transform as it

    real_default = ImageFont.load_default()
    monkeypatch.setattr(
        it,
        "ImageFont",
        SimpleNamespace(
            truetype=lambda *a, **k: (_ for _ in ()).throw(OSError("no font")),
            load_default=lambda *a, **k: real_default,
        ),
    )
    img = Image.new("RGBA", (64, 64))
    out = it.add_watermark(_img_bytes(img), "wm")
    assert out.image_bytes and out.mime_type.startswith("image/")


def _img_bytes(img, fmt="PNG"):
    import io

    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ── app.services.free_instagram_client ────────────────────────────────────


def test_parse_embedded_bad_unicode_falls_back(monkeypatch):
    import app.services.free_instagram_client as fic

    monkeypatch.setattr(fic, "_decode_unicode_escapes", lambda s: (_ for _ in ()).throw(ValueError("bad")))
    html = '"follower_count": 42, "biography": "hello world", "profile_pic_url": "http://x/y.jpg"'
    out = fic._parse_embedded(html)
    assert out["follower_count"] == 42
    assert out["biography"] == "hello world"
    assert out["profile_pic_url"] == "http://x/y.jpg"


# ── app.services.instagrapi_client ────────────────────────────────────────


def test_sync_call_exhaustion_zero_retries():
    from app.services.instagrapi_client import InstagrapiClient, InstagrapiError

    cl = InstagrapiClient.__new__(InstagrapiClient)
    cl._max_retries = 0
    cl._username = "u"
    with pytest.raises(InstagrapiError, match="after 0 retries"):
        cl._sync_call(lambda c: 1)


# ── app.services.lead_capture ─────────────────────────────────────────────


class _FakeRedis:
    def __init__(self, store=None, close_fails=False):
        self.store = store if store is not None else {}
        self.close_fails = close_fails

    async def get(self, k):
        return self.store.get(k)

    async def setex(self, k, ttl, v):
        self.store[k] = v

    async def delete(self, k):
        self.store.pop(k, None)

    async def aclose(self):
        if self.close_fails:
            raise RuntimeError("close boom")


def _patch_redis(monkeypatch, fake):
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **kw: fake)


@pytest.mark.asyncio
async def test_snapshot_and_restore_close_exception_tolerated(monkeypatch):
    from app.models.lead import LeadSource
    from app.services import lead_capture as lc

    fake = _FakeRedis(close_fails=True)
    _patch_redis(monkeypatch, fake)
    # aclose raises → swallowed by the finally
    assert await lc.snapshot_lead_session(LeadSource.telegram_dm, uuid.uuid4(), "t1") is None
    await lc.restore_lead_session(LeadSource.telegram_dm, uuid.uuid4(), "t1", '{"step": "name"}')
    await lc.restore_lead_session(LeadSource.telegram_dm, uuid.uuid4(), "t1", None)


def test_step_prompt_company_size():
    from app.services.lead_capture import LeadCaptureStep, _step_prompt

    r = _step_prompt(LeadCaptureStep.company_size, greek=False)
    assert r.text  # re-ask company-size question


@pytest.mark.asyncio
async def test_company_size_freeform_parsing(monkeypatch):
    from app.models.lead import LeadSource
    from app.services import lead_capture as lc
    from app.services.lead_capture import LeadCaptureStep

    TEAM_ID = uuid.uuid4()

    async def _run(text):
        state = {
            "step": LeadCaptureStep.company_size,
            "started_at": datetime.now(UTC).isoformat(),
            "fields": {"name": "N", "email": "n@x.gr"},
        }
        fake = _FakeRedis()
        key = lc._session_key(LeadSource.telegram_dm, TEAM_ID, "th1")
        fake.store[key] = json.dumps(state)
        _patch_redis(monkeypatch, fake)
        return await lc.handle_lead_capture_message(
            _NoDb(),
            team_id=TEAM_ID,
            source=LeadSource.telegram_dm,
            social_account_id=None,
            thread_id="th1",
            inbound_text=text,
        )

    # "6,20" hits the 6-20 branch; "200 people" hits the bare-200 branch
    r620 = await _run("team of 6,20")
    r200 = await _run("we have 200 people")
    assert r620 is not None and r200 is not None


class _NoDb:
    async def execute(self, *a, **kw):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    def add(self, o):
        pass

    async def commit(self):
        pass

    async def flush(self):
        pass


# ── app.services.messenger_sidecar ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_account_and_config_401_retry(monkeypatch):
    import httpx
    import respx

    import app.services.messenger_sidecar as ms

    ms._api_token = "stale-token"
    monkeypatch.setattr(ms, "ADMIN_EMAIL", "a@x.gr", raising=False)
    monkeypatch.setattr(ms, "ADMIN_PASSWORD", "pw", raising=False)

    calls = {"accounts": 0}

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{ms.SOCIAL_API_URL}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "fresh"}))

        def accounts_resp(request):
            calls["accounts"] += 1
            if calls["accounts"] == 1:
                return httpx.Response(401)
            return httpx.Response(
                200,
                json=[
                    {
                        "platform": "facebook",
                        "account_id": "pg1",
                        "id": "acct-1",
                        "display_name": "cloudless.gr",
                        "meta_data": {"page_token": "pt", "messenger_auto_reply": {"enabled": True}},
                    }
                ],
            )

        router.get(f"{ms.SOCIAL_API_URL}/api/v1/accounts").mock(side_effect=accounts_resp)
        out = await ms._get_account_and_config("pg1")

    assert out is not None and out[0] == "acct-1" and out[1] == "pt"
    assert calls["accounts"] == 2  # retried after re-auth
    ms._api_token = ""


@pytest.mark.asyncio
async def test_generate_ai_response_cf_fallback(monkeypatch):
    import httpx
    import respx

    import app.services.messenger_sidecar as ms

    monkeypatch.setattr(ms, "DMR_BASE_URL", "http://dmr.test", raising=False)
    monkeypatch.setattr(ms, "CLOUDFLARE_API_TOKEN", "cf-tok", raising=False)
    monkeypatch.setattr(ms, "CLOUDFLARE_ACCOUNT_ID", "acct", raising=False)

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{ms.DMR_BASE_URL}/engines/v1/chat/completions").mock(side_effect=httpx.ConnectError("dmr down"))
        router.post(url__regex=r".*cloudflare.*").mock(return_value=httpx.Response(200, json={"result": {"response": " cf-answer "}}))
        out = await ms._generate_ai_response({"system_prompt": "s"}, "hello", "page")

    assert out == "cf-answer"


@pytest.mark.asyncio
async def test_generate_ai_response_both_fail_returns_fallback(monkeypatch):
    import httpx
    import respx

    import app.services.messenger_sidecar as ms

    monkeypatch.setattr(ms, "DMR_BASE_URL", "http://dmr.test", raising=False)
    monkeypatch.setattr(ms, "CLOUDFLARE_API_TOKEN", "cf", raising=False)
    monkeypatch.setattr(ms, "CLOUDFLARE_ACCOUNT_ID", "acct", raising=False)

    with respx.mock(assert_all_called=False) as router:
        router.post(url__regex=r".*").mock(side_effect=httpx.ConnectError("down"))
        out = await ms._generate_ai_response({"system_prompt": "s", "fallback_text": "the-fallback"}, "hi", "pg")
    assert out == "the-fallback"


# ── app.worker.tasks.datalake_export._export_monetization ─────────────────


@pytest.mark.asyncio
async def test_export_monetization_rows(monkeypatch):
    import app.services.monetization as mon
    import app.worker.tasks.datalake_export as de

    acc_ok = SimpleNamespace(
        id=uuid.uuid4(),
        platform="facebook",
        account_type="user",
        username="themis",
    )
    acc_err = SimpleNamespace(
        id=uuid.uuid4(),
        platform="tiktok",
        account_type="user",
        username="tk",
    )
    acc_fun = SimpleNamespace(
        id=uuid.uuid4(),
        platform="linkedin",
        account_type="organization",
        username="cloudless-gr",
    )
    rows_holder = {acc_ok.id: {"followers": 66, "gain_per_day": 1.5}}

    async def fake_gather(db, acc):
        if acc is acc_err:
            raise RuntimeError("metrics down")
        if acc is acc_fun:
            return {"followers": 500, "gain_per_day": 3.0}
        return rows_holder[acc.id]

    monkeypatch.setattr(mon, "gather_account_metrics", fake_gather)

    class _Res:
        def scalars(self):
            return SimpleNamespace(all=lambda: [acc_ok, acc_err, acc_fun])

    class _Db:
        async def execute(self, *a):
            return _Res()

    out = await de._export_monetization(_Db())
    assert len(out) == 3
    by_plat = {r["platform"]: r for r in out}
    assert by_plat["tiktok"]["error"] == "metrics down"
    assert by_plat["linkedin"]["role"]  # funnel surface
    assert by_plat["facebook"]["program"].startswith("Facebook Stars")
    assert by_plat["facebook"]["criteria"]


# ── app.api.ops monetization-report endpoint ──────────────────────────────


@pytest.mark.asyncio
async def test_ops_monetization_report_inline_and_queued(monkeypatch):
    from app.api import ops

    runner = AsyncMock(return_value=[{"team_name": "T"}])
    monkeypatch.setattr(
        "app.services.monetization_report.run_monetization_report_for_all_teams",
        runner,
    )
    out = await ops.trigger_monetization_report(
        send=False,
        execute=True,
        insight_days=30,
        async_queue=False,
        current_user=None,
        db=SimpleNamespace(),
    )
    assert out["reports"] == [{"team_name": "T"}]

    sent = {}

    class _Task:
        @staticmethod
        def delay(**kw):
            sent.update(kw)

    monkeypatch.setattr("app.worker.tasks.monetization.run_daily_monetization_report", _Task)
    out2 = await ops.trigger_monetization_report(
        send=True,
        execute=False,
        insight_days=30,
        async_queue=True,
        current_user=None,
        db=SimpleNamespace(),
    )
    assert out2 == {"queued": True}
    assert sent == {"send": True, "execute": False}


# ── app.api.reports residuals ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reports_output_dir_missing(monkeypatch, tmp_path):
    from app.api import reports as rep_api

    monkeypatch.setattr(rep_api, "OUTPUT_DIR", tmp_path / "nope")
    monkeypatch.setattr(rep_api, "REPORTS_DIR", tmp_path / "noreps")
    res = await rep_api.list_reports(current_user=None, limit=10, notebook=None)
    assert res == {"reports": []}


@pytest.mark.asyncio
async def test_reports_bad_manifest_skipped(monkeypatch, tmp_path):
    from app.api import reports as rep_api

    out = tmp_path / "output"
    reps = tmp_path / "reports"
    out.mkdir()
    reps.mkdir()
    (reps / "nb1.ipynb").write_text("{}")
    (out / "nb1-run.manifest.json").write_text('{"subject": "s1"}')
    (out / "bad-run.manifest.json").write_text("{not json")
    monkeypatch.setattr(rep_api, "OUTPUT_DIR", out)
    monkeypatch.setattr(rep_api, "REPORTS_DIR", reps)
    res = await rep_api.list_reports(current_user=None, limit=10, notebook=None)
    # bad manifest contributes nothing; the good one resolves to nb1
    assert len(res["reports"]) == 1
    assert res["reports"][0]["notebook"] == "nb1"


@pytest.mark.asyncio
async def test_reports_invalid_notebook_filter(monkeypatch, tmp_path):
    from app.api import reports as rep_api

    out = tmp_path / "o"
    reps = tmp_path / "r"
    out.mkdir()
    reps.mkdir()
    (reps / "nb1.ipynb").write_text("{}")
    (out / "nb1-1.manifest.json").write_text('{"subject": "a"}')
    (out / "other-1.manifest.json").write_text('{"subject": "b"}')
    monkeypatch.setattr(rep_api, "OUTPUT_DIR", out)
    monkeypatch.setattr(rep_api, "REPORTS_DIR", reps)
    res = await rep_api.list_reports(current_user=None, limit=10, notebook="bad name!")
    # invalid filter name → empty prefix → all manifests listed
    assert len(res["reports"]) == 2


@pytest.mark.asyncio
async def test_get_report_file_symlink_escape(monkeypatch, tmp_path):
    from app.api import reports as rep_api

    out = tmp_path / "o"
    reps = tmp_path / "r"
    out.mkdir()
    reps.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("sensitive")
    (out / "link.txt").symlink_to(outside)
    monkeypatch.setattr(rep_api, "OUTPUT_DIR", out)
    monkeypatch.setattr(rep_api, "REPORTS_DIR", reps)
    with pytest.raises(HTTPException) as exc:
        await rep_api.get_report_file("link.txt", current_user=None)
    assert exc.value.status_code == 400


# ── app.services.x_web ────────────────────────────────────────────────────


def test_from_tweety_tweet_shapes():
    from app.services.x_web import _from_tweety_tweet

    assert _from_tweety_tweet(SimpleNamespace()) is None  # no id
    t = SimpleNamespace(
        id="123",
        created_on=None,
        date=datetime.now(UTC),
        author=SimpleNamespace(username="other_user"),
        retweeted_tweet=None,
        is_retweet=False,
        quoted_tweet=None,
        is_quote=False,
        views=100,
        likes=5,
        retweet_counts=None,
        retweets=2,
        replies=1,
        text="hi",
    )
    m = _from_tweety_tweet(t, username="me")
    assert m is not None and m.id == "123"
    assert _from_tweety_tweet(t, username="other_user") is not None


def test_prepare_media_video_symlink(tmp_path):
    from app.services.x_web import MediaPlan, prepare_media

    src = tmp_path / "clip.MP4"
    src.write_bytes(b"\x00" * 100)
    dest = tmp_path / "out"
    dest.mkdir()
    out = prepare_media(MediaPlan(kind="video", paths=[str(src)]), str(dest))
    assert len(out) == 1 and out[0].endswith(".mp4")


def test_prepare_media_image_failure_raises(tmp_path):
    from app.services.x_web import MediaPlan, XWebMediaError, prepare_media

    src = tmp_path / "bad.png"
    src.write_bytes(b"not-a-png")
    dest = tmp_path / "o"
    dest.mkdir()
    with pytest.raises(XWebMediaError):
        prepare_media(MediaPlan(kind="images", paths=[str(src)]), str(dest))


@pytest.mark.asyncio
async def test_read_with_twscrape_metrics():
    from app.services.x_web import _read_with_twscrape

    api = SimpleNamespace(
        user_by_login=AsyncMock(
            return_value=SimpleNamespace(
                followersCount="1,200",
                friendsCount=300,
                statusesCount=50,
                listedCount=7,
            )
        ),
    )
    out = await _read_with_twscrape(api, "handle", None, [], 0)
    assert out.followers == 1200 and out.following == 300
    assert out.source == "x_web_twscrape"


@pytest.mark.asyncio
async def test_read_with_twscrape_user_not_found():
    from app.services.x_web import _read_with_twscrape

    api = SimpleNamespace(user_by_login=AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="not found"):
        await _read_with_twscrape(api, "ghost", None, [], 0)


@pytest.mark.asyncio
async def test_fetch_x_web_analytics_disabled(monkeypatch):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "is_configured", lambda: False)
    out, reason = await xw.fetch_x_web_analytics(username="x", user_id=None)
    assert out is None and "disabled" in reason


@pytest.mark.asyncio
async def test_fetch_x_web_analytics_breaker_and_cleanup(monkeypatch, tmp_path):
    import app.services.x_web as xw

    monkeypatch.setattr(xw, "is_configured", lambda: True)
    monkeypatch.setattr(xw.get_settings(), "X_WEB_ANALYTICS_MAX_TWEET_LOOKUPS", "5", raising=False) if hasattr(
        xw.get_settings(), "X_WEB_ANALYTICS_MAX_TWEET_LOOKUPS"
    ) else None

    guard = SimpleNamespace(
        reserve_analytics_slot=AsyncMock(return_value=SimpleNamespace(ok=True, reason="")),
        trip=AsyncMock(),
    )

    class _Boom(Exception):
        pass

    async def tweety_factory():
        raise _Boom("tweety down")

    wd = tmp_path / "tws_workdir"
    wd.mkdir()

    async def twscrape_factory(handle):
        api = SimpleNamespace(_x_web_workdir=str(wd))
        api.user_by_login = AsyncMock(side_effect=_Boom("tws down"))
        return api

    out, reason = await xw.fetch_x_web_analytics(
        username="x",
        user_id=None,
        guard=guard,
        tweety_factory=tweety_factory,
        twscrape_factory=twscrape_factory,
    )
    assert out is None and "twscrape" in reason
    assert not wd.exists()  # workdir cleaned in finally
