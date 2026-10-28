"""Coverage-gap tests for the monetization service trio —
``monetization.py`` live-metric gathering, ``monetization_executor.py``
generation/scheduling internals, and ``monetization_report.py``
orchestration. Stubs sit at module boundaries (inference, media, DB
result sets) — the code under test runs for real.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.monetization as M
import app.services.monetization_executor as X
import app.services.monetization_report as R
from app.core.security import encrypt_token
from app.models.content import Post, PostStatus
from app.services.monetization_report import MonetizationAction


def _account(platform="instagram", account_type="user", username="u", meta=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform=platform,
        account_type=account_type,
        username=username,
        account_id="acct-123",
        status="active",
        meta_data=meta or {},
        access_token_enc=encrypt_token("tok"),
    )


class _Rows:
    """db.execute() stub: returns canned rows for .all() / .first()."""

    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """Async-session stub: queue of _Rows for execute(), queue for scalar()."""

    def __init__(self, results=None, scalars=None):
        self._results = list(results or [])
        self._scalars = list(scalars or [])
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def execute(self, stmt):
        return self._results.pop(0) if self._results else _Rows([])

    async def scalar(self, stmt):
        return self._scalars.pop(0) if self._scalars else 0


# ── monetization.py ───────────────────────────────────────────────────────


def test_met_count_property():
    rep = M.ProgramReport(
        "x",
        "prog",
        [M.Criterion("a", 1, 1, True), M.Criterion("b", 0, 1, False), M.Criterion("c", None, 1, None)],
        False,
    )
    assert rep.met_count == 1


def test_evaluate_all_met_is_monetizable_true():
    # instagram gate: followers int + invite bool — supply both → fully met
    r = M.evaluate_program("instagram", {"followers": 20_000, "invite": True})
    assert r is not None and r.monetizable is True
    assert r.met_count == len(r.criteria)


def test_evaluate_bool_criterion_nonbool_current_is_manual():
    # invite supplied as a string — bool criteria stay undetermined
    r = M.evaluate_program("instagram", {"followers": 20_000, "invite": "yes"})
    assert r is not None and r.monetizable is None


def test_account_token_roundtrip():
    acc = _account()
    assert M._account_token(acc) == "tok"
    acc2 = _account()
    acc2.access_token_enc = acc2.access_token_enc.decode()  # str form
    assert M._account_token(acc2) == "tok"


@pytest.mark.asyncio
async def test_gather_metrics_snapshot_fallback_when_live_fails():
    # b"bad-token" won't decrypt → live call raises inside try → falls back
    acc = _account("instagram")
    acc.access_token_enc = b"not-fernet"
    now = datetime.now(UTC)
    snaps = [(now - timedelta(days=10), 100), (now - timedelta(days=2), 130)]
    out = await M.gather_account_metrics(_Db(results=[_Rows(snaps)]), acc)
    assert out["followers"] == 130
    assert out["followers_live"] is False
    assert out["gain_per_day"] > 0


@pytest.mark.asyncio
async def test_gather_metrics_instagram_live(monkeypatch):
    acc = _account("instagram")

    class _IG:
        def __init__(self, *a, **kw):
            pass

        async def get_profile(self):
            return {"followers_count": 777}

    monkeypatch.setattr("app.services.instagram_api.InstagramAPIClient", _IG)
    out = await M.gather_account_metrics(_Db(results=[_Rows([])]), acc)
    assert out["followers"] == 777
    assert out["followers_live"] is True
    assert out["gain_method"] == "none"


@pytest.mark.asyncio
async def test_gather_metrics_tiktok_live_and_views(monkeypatch):
    acc = _account("tiktok")
    recent_ts = datetime.now(UTC).timestamp() - 5 * 86400
    old_ts = datetime.now(UTC).timestamp() - 40 * 86400

    class _TT:
        def __init__(self, *a, **kw):
            pass

        async def get_stats(self):
            return {"data": {"follower_count": 12000}}

        async def list_videos(self, cursor=0, max_count=20):
            if cursor == 0:
                return {
                    "data": {
                        "videos": [
                            {"create_time": recent_ts, "view_count": 1000},
                            {"create_time": old_ts, "view_count": 999},
                        ],
                        "has_more": True,
                        "cursor": 1,
                    }
                }
            return {
                "data": {
                    "videos": [{"create_time": recent_ts, "view_count": 500}],
                    "has_more": False,
                }
            }

    monkeypatch.setattr("app.services.tiktok_api.TikTokAPIClient", _TT)
    out = await M.gather_account_metrics(_Db(results=[_Rows([])]), acc)
    assert out["followers"] == 12000
    assert out["views_30d"] == 1500  # old video excluded


@pytest.mark.asyncio
async def test_gather_metrics_twitter_live(monkeypatch):
    acc = _account("twitter")

    class _TW:
        def __init__(self, *a, **kw):
            pass

        async def get_me_metrics(self):
            return {"data": {"public_metrics": {"followers_count": 640}}}

    monkeypatch.setattr("app.services.twitter_api.TwitterAPIClient", _TW)
    out = await M.gather_account_metrics(_Db(results=[_Rows([])]), acc)
    assert out["followers"] == 640


@pytest.mark.asyncio
async def test_gather_metrics_facebook_days_held(monkeypatch):
    acc = _account("facebook")
    # Live call fails (no FB client branch) → snapshot path + days_held calc
    acc.access_token_enc = b"not-fernet"
    today = datetime.now(UTC).date()

    def day(d):
        return datetime(today.year, today.month, today.day, tzinfo=UTC) - timedelta(days=d)

    # oldest-first: day-4 below gate (breaks the streak), then 3 days ≥500
    snaps = [(day(4), 400), (day(2), 600), (day(1), 600), (day(0), 600)]
    out = await M.gather_account_metrics(_Db(results=[_Rows(snaps)]), acc)
    assert out["days_held"] == 3
    assert out["followers"] == 600


@pytest.mark.asyncio
async def test_gather_metrics_tiktok_views_empty_and_error(monkeypatch):
    acc = _account("tiktok")

    class _Empty:
        def __init__(self, *a, **kw):
            pass

        async def get_stats(self):
            return {"data": {"follower_count": 50}}

        async def list_videos(self, cursor=0, max_count=20):
            return {"data": {"videos": [], "has_more": False}}

    monkeypatch.setattr("app.services.tiktok_api.TikTokAPIClient", _Empty)
    out = await M.gather_account_metrics(_Db(results=[_Rows([])]), acc)
    assert out["views_30d"] == 0

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        async def get_stats(self):
            return {"data": {"follower_count": 50}}

        async def list_videos(self, cursor=0, max_count=20):
            raise RuntimeError("tiktok api down")

    monkeypatch.setattr("app.services.tiktok_api.TikTokAPIClient", _Boom)
    out2 = await M.gather_account_metrics(_Db(results=[_Rows([])]), acc)
    assert "views_30d" not in out2  # error swallowed, key absent


@pytest.mark.asyncio
async def test_gather_metrics_endpoint_gain_under_three_points():
    acc = _account("instagram")
    acc.access_token_enc = b"not-fernet"
    now = datetime.now(UTC)
    # two points → Theil-Sen needs ≥3 → endpoint-delta gain
    snaps = [(now - timedelta(days=7), 100), (now, 170)]
    out = await M.gather_account_metrics(_Db(results=[_Rows(snaps)]), acc)
    assert out["gain_method"] == "endpoints"
    assert out["gain_per_day"] == 10.0


# ── monetization_executor.py ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_account_multi_non_linkedin_returns_first():
    accs = [_account("instagram"), _account("instagram", username="u2")]
    out = await X._resolve_account(_Db(results=[_Rows(accs)]), uuid.uuid4(), "instagram", "post")
    assert out is accs[0]


def test_resolve_slot_malformed_time_falls_back():
    a = MonetizationAction("linkedin", "post", "not-a-time", "image")
    local = X._resolve_slot(a, datetime.now(UTC)).astimezone(X._ATHENS)
    assert (local.hour, local.minute) == (9, 30)


def test_resolve_slot_out_of_range_remaps():
    a = MonetizationAction("twitter", "post", "25:99", "text")
    local = X._resolve_slot(a, datetime.now(UTC)).astimezone(X._ATHENS)
    assert (local.hour, local.minute) == (17, 0)


@pytest.mark.asyncio
async def test_generate_copy_real_chain(monkeypatch):
    monkeypatch.setattr(
        "app.services.inference.call_inference",
        AsyncMock(return_value={"content": "raw copy", "hashtags": ["#x"]}),
    )
    monkeypatch.setattr(
        "app.services.plain_english.rewrite_plain_english",
        AsyncMock(return_value="clean copy"),
    )
    quality = SimpleNamespace(content="polished copy", hashtags=["#y"], seo_score=95)
    monkeypatch.setattr(
        "app.services.quality_pipeline.apply_quality_pipeline",
        AsyncMock(return_value=quality),
    )
    content, tags, seo = await X._generate_copy(_Db(), SimpleNamespace(id=uuid.uuid4()), MonetizationAction("linkedin", "post", "12:00", "image"), "brand")
    assert content == "polished copy" and tags == ["#y"] and seo == 95


class _ACtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_generate_media_comfyui_path(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.expand_visual_prompt", AsyncMock(return_value="p"))
    monkeypatch.setattr("app.services.gpu_arbiter.media_gpu_lock", lambda: _ACtx())
    monkeypatch.setattr(
        "app.services.comfyui_image.generate_image",
        AsyncMock(return_value=(b"png-bytes", {})),
    )
    asset = SimpleNamespace(id=uuid.uuid4())
    persist = AsyncMock(return_value=asset)
    monkeypatch.setattr("app.services.media_storage.persist_generated_image", persist)
    out = await X._generate_media(_Db(), SimpleNamespace(id=uuid.uuid4()), uuid.uuid4(), MonetizationAction("linkedin", "i", "12:00", "image"))
    assert out == asset.id


@pytest.mark.asyncio
async def test_generate_media_cf_fallback(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.expand_visual_prompt", AsyncMock(return_value="p"))
    monkeypatch.setattr("app.services.gpu_arbiter.media_gpu_lock", lambda: _ACtx())
    monkeypatch.setattr(
        "app.services.comfyui_image.generate_image",
        AsyncMock(side_effect=RuntimeError("gpu down")),
    )
    monkeypatch.setattr(
        "app.services.inference._call_workers_ai_image",
        AsyncMock(return_value={"image_base64": base64.b64encode(b"cf-png").decode()}),
    )
    asset = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr("app.services.media_storage.persist_generated_image", AsyncMock(return_value=asset))
    out = await X._generate_media(_Db(), SimpleNamespace(id=uuid.uuid4()), uuid.uuid4(), MonetizationAction("linkedin", "i", "12:00", "image"))
    assert out == asset.id


@pytest.mark.asyncio
async def test_generate_media_brand_banner_last_resort(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.expand_visual_prompt", AsyncMock(return_value="p"))
    monkeypatch.setattr("app.services.gpu_arbiter.media_gpu_lock", lambda: _ACtx())
    monkeypatch.setattr(
        "app.services.comfyui_image.generate_image",
        AsyncMock(side_effect=RuntimeError("gpu down")),
    )
    monkeypatch.setattr(
        "app.services.inference._call_workers_ai_image",
        AsyncMock(side_effect=RuntimeError("cf down")),
    )
    monkeypatch.setattr("app.services.brand_assets.generate_og_image", lambda *a, **kw: b"banner")
    asset = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr("app.services.media_storage.persist_generated_image", AsyncMock(return_value=asset))
    out = await X._generate_media(_Db(), SimpleNamespace(id=uuid.uuid4()), uuid.uuid4(), MonetizationAction("linkedin", "i", "12:00", "image"))
    assert out == asset.id


@pytest.mark.asyncio
async def test_generate_media_all_paths_fail_returns_none(monkeypatch):
    monkeypatch.setattr("app.services.media_ai.expand_visual_prompt", AsyncMock(return_value="p"))
    monkeypatch.setattr("app.services.gpu_arbiter.media_gpu_lock", lambda: _ACtx())
    monkeypatch.setattr(
        "app.services.comfyui_image.generate_image",
        AsyncMock(side_effect=RuntimeError("x")),
    )
    monkeypatch.setattr(
        "app.services.inference._call_workers_ai_image",
        AsyncMock(side_effect=RuntimeError("x")),
    )
    monkeypatch.setattr(
        "app.services.brand_assets.generate_og_image",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("x")),
    )
    out = await X._generate_media(_Db(), SimpleNamespace(id=uuid.uuid4()), uuid.uuid4(), MonetizationAction("linkedin", "i", "12:00", "image"))
    assert out is None


@pytest.mark.asyncio
async def test_schedule_slot_conflict_hops(monkeypatch):
    acc = _account("linkedin")
    db = _Db(results=[_Rows([SimpleNamespace(id=uuid.uuid4())]), _Rows([acc])])
    monkeypatch.setattr(X, "_generate_copy", AsyncMock(return_value=("c", [], 90)))
    monkeypatch.setattr(X, "_generate_media", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=(None, None, "")),
    )
    # First slot taken → _slot_taken True once then False → shifts +30m
    calls = iter([True, False])
    monkeypatch.setattr(X, "_slot_taken", AsyncMock(side_effect=lambda *a: next(calls)))
    out = await X.schedule_report_actions(
        db,
        SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="T"),
        [MonetizationAction("linkedin", "post", "23:00", "image")],
        "2026-10-14",
    )
    assert out[0]["status"] == "scheduled"


@pytest.mark.asyncio
async def test_schedule_copy_gen_failure_and_empty(monkeypatch):
    acc = _account("linkedin")
    db = _Db(
        results=[
            _Rows([SimpleNamespace(id=uuid.uuid4())]),  # owner
            _Rows([acc]),  # action1 account
            _Rows([acc]),  # action2 account
        ]
    )
    monkeypatch.setattr(X, "_slot_taken", AsyncMock(return_value=False))
    monkeypatch.setattr(
        X,
        "_generate_copy",
        AsyncMock(side_effect=[RuntimeError("infer down"), ("  ", [], 0)]),
    )
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=(None, None, "")),
    )
    out = await X.schedule_report_actions(
        db,
        SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="T"),
        [MonetizationAction("linkedin", "p1", "23:00", "image"), MonetizationAction("linkedin", "p2", "23:00", "image")],
        "2026-10-14",
    )
    assert out[0]["status"] == "failed" and "content generation" in out[0]["reason"]
    assert out[1]["status"] == "failed" and out[1]["reason"] == "empty generated copy"


@pytest.mark.asyncio
async def test_schedule_low_compliance_drafts(monkeypatch):
    acc = _account("linkedin")
    db = _Db(results=[_Rows([SimpleNamespace(id=uuid.uuid4())]), _Rows([acc])])
    monkeypatch.setattr(X, "_generate_copy", AsyncMock(return_value=("c", [], 90)))
    monkeypatch.setattr(X, "_generate_media", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=({"name": "b"}, {"v": 1}, "ctx")),
    )
    monkeypatch.setattr(
        "app.services.brand_compliance.score_brand_compliance",
        AsyncMock(return_value={"score": 1.0}),
    )
    out = await X.schedule_report_actions(
        db,
        SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="T"),
        [MonetizationAction("linkedin", "off-brand post", "23:00", "image")],
        "2026-10-14",
    )
    assert out[0]["status"] == "drafted" and "compliance" in out[0]["reason"]
    post = next(o for o in db.added if isinstance(o, Post))
    assert post.status == PostStatus.DRAFT and post.scheduled_at is None


@pytest.mark.asyncio
async def test_schedule_compliance_exception_still_schedules(monkeypatch):
    acc = _account("linkedin")
    db = _Db(results=[_Rows([SimpleNamespace(id=uuid.uuid4())]), _Rows([acc])])
    monkeypatch.setattr(X, "_generate_copy", AsyncMock(return_value=("c", [], 90)))
    monkeypatch.setattr(X, "_generate_media", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(
        "app.services.brand_compliance.load_brand_context",
        AsyncMock(return_value=({"name": "b"}, None, "ctx")),
    )
    monkeypatch.setattr(
        "app.services.brand_compliance.score_brand_compliance",
        AsyncMock(side_effect=RuntimeError("scorer down")),
    )
    out = await X.schedule_report_actions(
        db,
        SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="T"),
        [MonetizationAction("linkedin", "post", "23:00", "image")],
        "2026-10-14",
    )
    assert out[0]["status"] == "scheduled"


# ── monetization_report.py ────────────────────────────────────────────────


def _gate(platform="facebook", account_type="user", followers=66, eta=None):
    g = R.AccountGate(
        platform=platform,
        handle="@test",
        account_type=account_type,
        followers=followers,
        gain_per_day=0.5,
        followers_live=False,
        program=__import__("app.services.monetization", fromlist=["evaluate_program"]).evaluate_program(
            platform, {"followers": followers, "days_held": 0}, account_type=account_type
        ),
        funnel_note="funnel surface" if platform in ("linkedin",) else "",
        eta_days=eta,
    )
    return g


def _report(**kw):
    return R.MonetizationReport(
        generated_at=datetime.now(UTC),
        timezone="Europe/Athens",
        team_name="Cloudless",
        **kw,
    )


def test_subject_uses_timezone():
    rep = _report()
    assert rep.subject().startswith("SocialAuto monetization report — 20")


def test_text_render_eta_funnel_sections_scheduled():
    fb = _gate("facebook", "user", followers=66, eta=200)
    li = _gate("linkedin", "organization", followers=500)
    rep = _report(
        gates=[fb, li],
        telegram_members=None,
        sections=[{"platform": "facebook", "headline": "Grow", "strategy": "Post daily"}],
        scheduled=[{"platform": "facebook", "status": "scheduled", "scheduled_at": "2026-10-14 12:00 EEST", "instruction": "x"}],
    )
    text = rep.to_text()
    assert "~200d to next gate" in text
    assert "funnel surface" in text
    assert "STRATEGY" in text and "Post daily" in text
    assert "ACTIONS EXECUTED" in text and "scheduled" in text
    assert "channel members: —" in text


def test_html_render_eta_funnel_sections_scheduled():
    fb = _gate("facebook", "user", followers=66, eta=200)
    li = _gate("linkedin", "organization")
    rep = _report(
        gates=[fb, li],
        sections=[{"platform": "x", "headline": "h", "strategy": "s"}],
        scheduled=[{"platform": "x", "status": "drafted", "scheduled_at": None, "instruction": "i"}],
    )
    html = rep.to_html()
    assert "~200d to next gate" in html
    assert "funnel surface" in html
    assert "<h3>Strategy</h3>" in html
    assert "Actions executed" in html and "drafted" in html


@pytest.mark.asyncio
async def test_telegram_stats_with_account(monkeypatch):
    acc = _account("telegram", meta={"channel_id": "-100x"})
    acc.access_token_enc = encrypt_token("bot-tok")

    class _TG:
        def __init__(self, token):
            assert token == "bot-tok"

        async def get_chat_member_count(self, chat_id):
            assert chat_id == "-100x"
            return 1234

    monkeypatch.setattr("app.services.telegram_api.TelegramAPIClient", _TG)
    db = _Db(results=[_Rows([acc])], scalars=[3, 12])
    members, l7, l30 = await R._telegram_stats(db, uuid.uuid4())
    assert (members, l7, l30) == (1234, 3, 12)


@pytest.mark.asyncio
async def test_telegram_stats_no_account_and_error():
    # no telegram account → members None, lead counts still read
    db = _Db(results=[_Rows([])], scalars=[0, 0])
    members, l7, l30 = await R._telegram_stats(db, uuid.uuid4())
    assert members is None

    # telegram client raising → members stays None, leads still counted
    class _BadTG:
        def __init__(self, *a):
            pass

        async def get_chat_member_count(self, *a):
            raise RuntimeError("tg down")

    import app.services.telegram_api as tg_mod

    acc = _account("telegram", meta={"channel_id": "-100x"})
    acc.access_token_enc = encrypt_token("t")
    orig = tg_mod.TelegramAPIClient
    tg_mod.TelegramAPIClient = _BadTG
    try:
        db2 = _Db(results=[_Rows([acc])], scalars=[1, 5])
        members, l7, l30 = await R._telegram_stats(db2, uuid.uuid4())
        assert members is None and (l7, l30) == (1, 5)
    finally:
        tg_mod.TelegramAPIClient = orig


@pytest.mark.asyncio
async def test_gather_gates_builds_eta_and_funnel(monkeypatch):
    fb = _account("facebook", "user")
    li = _account("linkedin", "organization")
    db = _Db(results=[_Rows([fb, li])])
    metrics = {"followers": 66, "gain_per_day": 2.0, "followers_live": True}
    monkeypatch.setattr(R, "gather_account_metrics", AsyncMock(return_value=metrics))
    gates = await R._gather_gates(db, uuid.uuid4())
    fbg = next(g for g in gates if g.platform == "facebook")
    lig = next(g for g in gates if g.platform == "linkedin")
    assert fbg.eta_days is not None and fbg.followers_live is True
    assert lig.program is None and lig.funnel_note  # FUNNEL_ONLY lookup


@pytest.mark.asyncio
async def test_llm_strategy_nested_shape_fallback(monkeypatch):
    async def fake(prompt, **kw):
        return {
            "strategy": {
                "facebook": {
                    "headline": "h",
                    "strategy": "s",
                    "actions": [{"time": "10:00", "instruction": "do x"}],
                },
                "junk": "not-a-dict",
            }
        }

    monkeypatch.setattr("app.services.inference.call_inference", AsyncMock(side_effect=fake))
    sections, actions = await R._llm_strategy(None, None, [], {}, {})
    assert sections == [{"platform": "facebook", "headline": "h", "strategy": "s"}]
    assert len(actions) == 1 and actions[0].platform == "facebook"
    assert actions[0].time_athens == "10:00"


@pytest.mark.asyncio
async def test_llm_strategy_includes_platform_pulse(monkeypatch):
    captured = {}

    async def fake(prompt, **kw):
        captured["prompt"] = prompt
        return {"sections": [], "actions": []}

    monkeypatch.setattr("app.services.inference.call_inference", AsyncMock(side_effect=fake))
    insights = {
        "platforms": {
            "linkedin": {
                "posts": 5,
                "impressions": 1000,
                "engagement": 40,
                "avg_engagement_rate": 4.0,
                "avg_engagement_rate_wilson_low": 1.2,
                "momentum_7d_engagement_pct": 12.5,
                "momentum_7d_engagement_z": 2.0,
                "best_hour_athens": [9],
                "outliers": [{"id": 1}],
            }
        }
    }
    await R._llm_strategy(None, None, [_gate()], insights, {"channel_members": 10})
    assert '"platform": "linkedin"' in captured["prompt"]
    assert '"best_hour_athens": 9' in captured["prompt"]


@pytest.mark.asyncio
async def test_build_monetization_report_full(monkeypatch):
    monkeypatch.setattr(R, "_gather_gates", AsyncMock(return_value=[_gate()]))
    monkeypatch.setattr(R, "_telegram_stats", AsyncMock(return_value=(10, 2, 8)))
    monkeypatch.setattr(R, "build_team_insights", AsyncMock(return_value={"platforms": {}}))
    monkeypatch.setattr(
        "app.services.growth_initiatives.initiative_summary",
        AsyncMock(return_value=[{"name": "x"}]),
    )
    monkeypatch.setattr(
        R,
        "_llm_strategy",
        AsyncMock(
            return_value=(
                [{"platform": "fb", "headline": "h", "strategy": "s"}],
                [MonetizationAction("facebook", "i", "12:00", "image")],
            )
        ),
    )
    team = SimpleNamespace(id=uuid.uuid4(), name="Cloudless")
    rep = await R.build_monetization_report(_Db(), team=team)
    assert rep.llm_used is True
    assert rep.telegram_members == 10
    assert len(rep.actions) == 1


@pytest.mark.asyncio
async def test_build_monetization_report_rule_fallback(monkeypatch):
    monkeypatch.setattr(R, "_gather_gates", AsyncMock(return_value=[_gate()]))
    monkeypatch.setattr(R, "_telegram_stats", AsyncMock(return_value=(None, 0, 0)))
    monkeypatch.setattr(R, "build_team_insights", AsyncMock(return_value={}))
    monkeypatch.setattr("app.services.growth_initiatives.initiative_summary", AsyncMock(return_value=[]))
    monkeypatch.setattr(R, "_llm_strategy", AsyncMock(return_value=([], [])))
    rep = await R.build_monetization_report(_Db(), team=SimpleNamespace(id=uuid.uuid4(), name="T"))
    assert rep.llm_used is False
    assert rep.actions  # _rule_actions fired — facebook gate → fb action


@pytest.mark.asyncio
async def test_email_report_no_recipient_and_success(monkeypatch):
    rep = _report()
    import app.services.monetization_report as rmod

    class _Settings:
        DIGEST_EMAIL_TO = ""

    monkeypatch.setattr(rmod, "get_settings", lambda: _Settings())
    out = await R.email_monetization_report(rep)
    assert out.email_error == "DIGEST_EMAIL_TO not set"

    class _Settings2:
        DIGEST_EMAIL_TO = "ops@cloudless.gr"

    monkeypatch.setattr(rmod, "get_settings", lambda: _Settings2())
    monkeypatch.setattr(rmod, "send_email", AsyncMock())
    rep2 = _report()
    out2 = await R.email_monetization_report(rep2)
    assert out2.emailed is True

    monkeypatch.setattr(rmod, "send_email", AsyncMock(side_effect=RuntimeError("smtp down")))
    rep3 = _report()
    out3 = await R.email_monetization_report(rep3)
    assert out3.emailed is False and "smtp down" in out3.email_error


@pytest.mark.asyncio
async def test_execute_report_actions_empty_and_with_learned(monkeypatch):
    rep = _report(actions=[])
    assert await R.execute_report_actions(_Db(), SimpleNamespace(id=uuid.uuid4()), rep) == []

    rep2 = _report(
        actions=[MonetizationAction("tiktok", "post", "", "video")],
        insights={"platforms": {"tiktok": {"best_hour_athens": [20]}}},
    )
    sched = AsyncMock(return_value=[{"status": "scheduled"}])
    monkeypatch.setattr("app.services.monetization_executor.schedule_report_actions", sched)
    out = await R.execute_report_actions(_Db(), SimpleNamespace(id=uuid.uuid4()), rep2)
    assert out == [{"status": "scheduled"}]
    assert sched.call_args.kwargs["learned_hours"] == {"tiktok": 20}


@pytest.mark.asyncio
async def test_run_all_teams_empty_and_full(monkeypatch):
    team = SimpleNamespace(id=uuid.uuid4(), name="Cloudless")
    # team with no active accounts → skipped
    db = _Db(results=[_Rows([team])], scalars=[0])
    out = await R.run_monetization_report_for_all_teams(db)
    assert out[0]["email_error"] == "skipped empty team"

    # full path
    db2 = _Db(results=[_Rows([team])], scalars=[3])
    rep = _report(actions=[MonetizationAction("fb", "i", "12:00", "t")])

    async def fake_exec(db, team, report):
        report.scheduled = [{"status": "scheduled"}]

    monkeypatch.setattr(R, "build_monetization_report", AsyncMock(return_value=rep))
    monkeypatch.setattr(R, "execute_report_actions", AsyncMock(side_effect=fake_exec))
    monkeypatch.setattr(R, "email_monetization_report", AsyncMock(side_effect=lambda r: r))
    out2 = await R.run_monetization_report_for_all_teams(db2)
    assert out2[0]["team_name"] == "Cloudless"
    assert out2[0]["scheduled"] == [{"status": "scheduled"}]
    assert out2[0]["actions"] == ["i"]
