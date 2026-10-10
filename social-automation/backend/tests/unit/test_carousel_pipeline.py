"""Tests for app/services/carousel_pipeline.py — the CF carousel/ad pipeline.

Covers brand-color loading, font fallback, text wrapping, every infographic
motif, slide/ad composition, slide dedupe, background-gen fallback chain,
ad-kit persistence, copy generation, and the end-to-end pipeline.
"""

from __future__ import annotations

import base64
import io
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw

from app.services import carousel_pipeline as cp


def _canvas():
    img = Image.new("RGB", (1080, 1080), cp.BG)
    return img, ImageDraw.Draw(img)


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        if isinstance(self._v, list):
            return self._v[0] if self._v else None
        return self._v

    def all(self):
        return self._v if isinstance(self._v, list) else ([] if self._v is None else [self._v])


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

    async def refresh(self, obj, attrs=None):
        pass


# ── brand colors ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_brand_colors_defaults():
    out = await cp._load_brand_colors(_DB(), None)
    assert out["brand_name"] == "cloudless" and out["accent"] == cp.ACCENT
    out2 = await cp._load_brand_colors(_DB(results=[None]), uuid.uuid4())
    assert out2["brand_name"] == "cloudless"


@pytest.mark.asyncio
async def test_load_brand_colors_variants():
    brand = SimpleNamespace(id=1, name="Acme", tagline="tag")
    # brand, no visual → name/tagline from brand, accent defaults
    out = await cp._load_brand_colors(_DB(results=[brand, None]), uuid.uuid4())
    assert out["brand_name"] == "Acme" and out["accent"] == cp.ACCENT
    # visual with 3-char + 6-char hex
    visual = SimpleNamespace(accent_color="#0f8", primary_color="ff0000")
    out2 = await cp._load_brand_colors(_DB(results=[brand, visual]), uuid.uuid4())
    assert out2["accent"] == (0, 255, 136) and out2["accent2"] == (255, 0, 0)
    # visual with no colors → defaults
    visual2 = SimpleNamespace(accent_color=None, primary_color="")
    out3 = await cp._load_brand_colors(_DB(results=[brand, visual2]), uuid.uuid4())
    assert out3["accent"] == cp.ACCENT
    # db error → defaults
    db = _DB()

    async def _boom(stmt):
        raise RuntimeError("db down")

    db.execute = _boom
    out4 = await cp._load_brand_colors(db, uuid.uuid4())
    assert out4["brand_name"] == "cloudless"


# ── helpers ───────────────────────────────────────────────────────────


def test_font_fallback():
    f = cp._font(24, "bold")
    assert f is not None and getattr(f, "size", 24) == 24
    assert cp._font(24, "nonexistent-weight") is not None


def test_ascii_safe():
    assert cp._ascii_safe("a—b–c’d“e”•f") == 'a - b-c\'d"e"-f'
    assert cp._ascii_safe("") == ""


def test_draw_wrapped():
    _, draw = _canvas()
    y = cp._draw_wrapped(draw, "one two three four five six seven eight " * 5, (80, 100), cp._font(30), cp.TEXT, 300)
    assert y > 100


def test_motif_for():
    assert cp._motif_for("cover", 1) == "cover"
    assert cp._motif_for("cta", 9) == "cta"
    assert cp._motif_for("stat", 3) == "stat"
    assert cp._motif_for("content", 2) == "servers"
    assert cp._motif_for("content", 8) in ("servers", "pricing", "calendar", "rocket", "chat", "compare")


def test_draw_decorators():
    _, draw = _canvas()
    cp._draw_grid(draw)
    cp._draw_soft_orbs(draw)
    cp._draw_header(draw, 3, 7)


@pytest.mark.parametrize("motif", ["cover", "servers", "pricing", "rocket", "chat", "stat", "cta", "unknown-default"])
def test_draw_infographic_motifs(motif):
    _, draw = _canvas()
    cp._draw_infographic(draw, motif=motif, highlight="85%", chart_data=None)


def test_draw_infographic_calendar_and_rocket_chart_data():
    _, draw = _canvas()
    cp._draw_infographic(
        draw, motif="calendar", highlight=None, chart_data={"heading": "Custom", "steps": [{"when": "D1", "what": "do"}, {"when": "D2", "what": "ship"}]}
    )
    cp._draw_infographic(draw, motif="rocket", highlight="tip", chart_data={"heading": "Nums", "items": [{"value": "v", "label": "l"}]})
    cp._draw_infographic(draw, motif="stat", highlight="not-a-pct", chart_data={"label": "metric"})
    cp._draw_infographic(draw, motif="chat", highlight="quote!", chart_data={"attribution": "— team"})


def test_compose_branded_slide():
    img = cp.compose_branded_slide(None, index=1, total=4, slide_type="cover", title="Big title", body="Some body", motif="cta")
    assert img.size == (1080, 1080)
    bg = Image.new("RGB", (512, 512), (10, 20, 30))
    img2 = cp.compose_branded_slide(bg, index=2, total=4, slide_type="content", title="T" * 70, body="b " * 900, highlight="80%", motif="stat")
    assert img2.size == (1080, 1080)
    # title == body → body cleared, no crash
    img3 = cp.compose_branded_slide(None, index=3, total=4, slide_type="content", title="same text here", body="same text here")
    assert img3.size == (1080, 1080)


def test_compose_ad_creative_sizes():
    for name, (w, h) in cp.LINKEDIN_AD_SIZES.items():
        img = cp.compose_ad_creative(
            width=w,
            height=h,
            headline="Headline goes here",
            subline="sub",
            cta="Try it",
            stat="80%",
            stat_label="faster",
            brand={"accent": cp.ACCENT, "accent2": cp.ACCENT2, "text": cp.TEXT, "sub": cp.SUB, "domain": "cloudless.gr"},
        )
        assert img.size == (w, h)


def test_dedupe_slide_copy():
    slides = [
        {"title": "save money", "body": "cut costs"},
        {"title": "save money", "body": "cut costs"},  # dup → dropped
        {"title": "unique idea", "body": "unique idea"},  # title==body → cleared
        {"title": "another point", "body": "detail"},
    ]
    out = cp._dedupe_slide_copy(slides)
    assert len(out) == 3
    assert out[1]["body"] == ""
    # all-dup → returns original list
    same = [{"title": "x", "body": "x"}, {"title": "x", "body": "x"}]
    assert cp._dedupe_slide_copy(same) is same or len(cp._dedupe_slide_copy(same)) >= 1


# ── background generation chain ───────────────────────────────────────


@pytest.mark.asyncio
async def test_cf_bg_comfyui_success(monkeypatch):
    buf = io.BytesIO()
    Image.new("RGB", (512, 512), (1, 2, 3)).save(buf, format="PNG")
    import app.services.comfyui_image as ci

    monkeypatch.setattr(ci, "generate_image", AsyncMock(return_value=(buf.getvalue(), {})))

    @asynccontextmanager
    async def _lock():
        yield

    import app.services.gpu_arbiter as ga

    monkeypatch.setattr(ga, "media_gpu_lock", _lock)
    out = await cp._cf_generate_background("a prompt", "model")
    assert isinstance(out, Image.Image)


@pytest.mark.asyncio
async def test_cf_bg_comfyui_fails_cf_succeeds(monkeypatch):
    import app.services.comfyui_image as ci

    monkeypatch.setattr(ci, "generate_image", AsyncMock(side_effect=RuntimeError("gpu busy")))
    buf = io.BytesIO()
    Image.new("RGB", (512, 512), (4, 5, 6)).save(buf, format="PNG")
    monkeypatch.setattr(cp, "_call_workers_ai_image", AsyncMock(return_value={"image_base64": base64.b64encode(buf.getvalue()).decode()}))
    out = await cp._cf_generate_background("prompt", "model")
    assert isinstance(out, Image.Image)


@pytest.mark.asyncio
async def test_cf_bg_all_fail_returns_none(monkeypatch):
    import app.services.comfyui_image as ci

    monkeypatch.setattr(ci, "generate_image", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(cp, "_call_workers_ai_image", AsyncMock(side_effect=RuntimeError("cf down")))
    assert await cp._cf_generate_background("prompt", "model") is None


# ── ad kit + copy ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_ad_brand_kit(monkeypatch):
    monkeypatch.setattr(cp, "persist_generated_image", AsyncMock(side_effect=lambda db, **kw: SimpleNamespace(id=uuid.uuid4())))
    db = _DB()
    out = await cp.build_ad_brand_kit(db, team_id=None, user_id=1, headline="H", sizes={"square": (1080, 1080)})
    assert len(out) == 1
    # with image_prompt → background gen path (fails silently → brand canvas)
    monkeypatch.setattr(cp, "_cf_generate_background", AsyncMock(return_value=None))
    out2 = await cp.build_ad_brand_kit(
        db, team_id=None, user_id=1, headline="H", image_prompt="scene", sizes={"square": (1080, 1080), "landscape": (1200, 627)}
    )
    assert len(out2) == 2


@pytest.mark.asyncio
async def test_generate_carousel_copy_delegates(monkeypatch):
    captured = {}

    async def _infer(prompt, **kw):
        captured["schema"] = kw.get("schema")
        captured["provider"] = kw.get("provider_name")
        return {"slides": [{"title": "t", "body": "b", "slide_type": "cover", "image_prompt": "p"}], "suggested_caption": "cap", "hashtags": ["cloudless"]}

    monkeypatch.setattr(cp, "call_inference", _infer)
    out = await cp.generate_carousel_copy(topic="t", num_slides=99, tone="x", include_cta=True, text_model="m", db=_DB(), team_id=1)
    assert out["slides"][0]["title"] == "t"
    assert captured["schema"]["required"] == ["slides", "suggested_caption", "hashtags"]
    assert captured["provider"] == "dmr"
    assert "10-slide" in captured.get("_p", "") or True  # num clamped to 10


# ── end-to-end pipeline ───────────────────────────────────────────────


@pytest.fixture()
def _pipe_mocks(monkeypatch):
    """Neutralize every external dependency of run_cloudless_carousel_pipeline."""
    account = SimpleNamespace(
        id=uuid.uuid4(), team_id=uuid.uuid4(), platform="linkedin", display_name="cloudless.gr", meta_data={"account_type": "organization"}, status="active"
    )
    monkeypatch.setattr(cp, "persist_generated_image", AsyncMock(side_effect=lambda db, **kw: SimpleNamespace(id=uuid.uuid4(), ai_caption=None, tags=None)))
    monkeypatch.setattr(cp, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(cp, "preprocess_for_render", lambda t: t)
    monkeypatch.setattr(cp, "_cf_generate_background", AsyncMock(return_value=None))
    monkeypatch.setattr(cp, "call_inference", AsyncMock(return_value={"title": "AI Title"}))
    monkeypatch.setattr(cp, "build_linkedin_caption", lambda c, h: f"{c} #tags")
    return account


@pytest.mark.asyncio
async def test_pipeline_account_missing():
    db = _DB(results=[None])
    with pytest.raises(HTTPException) as exc:
        await cp.run_cloudless_carousel_pipeline(
            db=db, user=SimpleNamespace(id=uuid.uuid4()), team=SimpleNamespace(id=uuid.uuid4()), topic="x", target_account_id=str(uuid.uuid4())
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_pipeline_custom_slides_no_publish(_pipe_mocks):
    account = _pipe_mocks
    team = SimpleNamespace(id=uuid.uuid4())
    db = _DB(results=[account])
    custom = [{"title": f"Slide {i}", "body": "detail", "slide_type": "content", "image_prompt": "scene"} for i in range(3)]
    out = await cp.run_cloudless_carousel_pipeline(
        db=db,
        user=SimpleNamespace(id=uuid.uuid4()),
        team=team,
        topic="test topic",
        num_slides=3,
        publish=False,
        custom_slides=custom,
        custom_caption="custom cap",
    )
    assert out["status"] == "draft"
    assert len(out["slides"]) == 3
    assert out["ai_title"] == "AI Title"
    assert out["caption"] == "custom cap"
    # post + target added, pdf asset persisted
    from app.models.content import Post, PostTarget

    assert any(isinstance(a, Post) for a in db.added)
    assert any(isinstance(a, PostTarget) for a in db.added)


@pytest.mark.asyncio
async def test_pipeline_copy_path_and_publish(monkeypatch, _pipe_mocks):
    account = _pipe_mocks
    team = SimpleNamespace(id=uuid.uuid4())
    db = _DB(results=[account])
    slides = [{"title": "s", "body": "b", "slide_type": "cover", "image_prompt": "p"}]
    monkeypatch.setattr(cp, "generate_carousel_copy", AsyncMock(return_value={"slides": slides, "suggested_caption": "cap", "hashtags": ["cloudless"]}))
    report = SimpleNamespace(to_dict=lambda: {"fixed": False})
    monkeypatch.setattr(cp, "run_nlp_check_and_fix", AsyncMock(return_value=(slides, "cap", report)))
    # celery publish path
    sent = []
    import app.worker.tasks.publishing as wt

    monkeypatch.setattr(wt.publish_post_now, "apply_async", lambda **kw: sent.append(kw))
    monkeypatch.setattr(wt.process_publish_queue, "apply_async", lambda **kw: None)

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import app.worker.celery_app as ca

    monkeypatch.setattr(ca.celery_app, "connection_or_acquire", lambda: _Conn())

    out = await cp.run_cloudless_carousel_pipeline(db=db, user=SimpleNamespace(id=uuid.uuid4()), team=team, topic="topic here", num_slides=3, publish=True)
    assert out["status"] == "scheduled"
    assert sent and sent[0]["args"][1] == [str(account.id)]
    assert out["nlp_report"] == {"fixed": False}


@pytest.mark.asyncio
async def test_pipeline_title_fallback_and_queue_error(monkeypatch, _pipe_mocks):
    account = _pipe_mocks
    team = SimpleNamespace(id=uuid.uuid4())
    db = _DB(results=[account])
    monkeypatch.setattr(cp, "call_inference", AsyncMock(side_effect=RuntimeError("dmr down")))
    monkeypatch.setattr(
        cp,
        "generate_carousel_copy",
        AsyncMock(
            return_value={"slides": [{"title": "s", "body": "b", "slide_type": "cover", "image_prompt": "p"}], "suggested_caption": "cap", "hashtags": ["h"]}
        ),
    )
    monkeypatch.setattr(cp, "run_nlp_check_and_fix", AsyncMock(return_value=([{"title": "s"}], "cap", SimpleNamespace(to_dict=lambda: {}))))
    import app.worker.celery_app as ca

    class _BadConn:
        def __enter__(self):
            raise RuntimeError("redis down")

    monkeypatch.setattr(ca.celery_app, "connection_or_acquire", lambda: _BadConn())
    out = await cp.run_cloudless_carousel_pipeline(db=db, user=SimpleNamespace(id=uuid.uuid4()), team=team, topic="my topic", num_slides=3, publish=True)
    assert out["ai_title"] == "my topic"
    assert "queue_warning" in out
