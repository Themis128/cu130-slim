"""Branch arms of app/api/ai.py that the endpoint suite left open.

Each test pins the *alternate* arm of a decision: no team, no brand visual,
already-persisted ComfyUI output, an unknown template variable, an email
draft with AI-invented numbers, a twice-duplicated carousel caption, and so on.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import ai
from app.api.ai import EmojiBatchRequest, EmojiGenerateRequest, GenerateContentRequest
from tests.unit.test_ai_api_router import (  # noqa: F401 — _limiter_off is autouse
    _DB,
    _REQ,
    _carousel_patches,
    _carousel_req,
    _img_patches,
    _img_req,
    _limiter_off,
    _team,
    _team_id,
    _user,
)

# ── generate-image: brand enhancement arms ──────────────────────────────


@pytest.mark.asyncio
async def test_generate_image_brand_without_visual_keeps_raw_prompt(monkeypatch):
    _img_patches(monkeypatch, diffusers={"image_base64": "aW1n"})
    import app.services.brand_assets as ba
    import app.services.inference as inf

    build = AsyncMock()
    monkeypatch.setattr(ba, "build_brand_image_prompt", build)
    db = _DB(results=[SimpleNamespace(id=uuid.uuid4()), None, SimpleNamespace(tone_dimensions={})], team=_team())
    await ai.generate_image(_img_req(), _team_id(), current_user=_user(), db=db)
    build.assert_not_called()
    assert inf._call_local_diffusers_txt2img.await_args.kwargs["prompt"] == "a cloud"


@pytest.mark.asyncio
async def test_generate_image_brand_visual_without_voice(monkeypatch):
    _img_patches(monkeypatch, diffusers={"image_base64": "aW1n"})
    import app.services.brand_assets as ba
    import app.services.inference as inf

    seen = {}

    def build(visual, voice, prompt):
        seen.update(visual=visual, voice=voice)
        return f"B::{prompt}"

    monkeypatch.setattr(ba, "build_brand_image_prompt", build)
    visual = SimpleNamespace(primary_color="#000", accent_color="#fff", neutral_colors=[], image_style="flat", photography_direction=None)
    db = _DB(results=[SimpleNamespace(id=uuid.uuid4()), visual, None], team=_team())
    await ai.generate_image(_img_req(), _team_id(), current_user=_user(), db=db)
    assert seen["voice"] is None and seen["visual"]["primary_color"] == "#000"
    assert inf._call_local_diffusers_txt2img.await_args.kwargs["prompt"] == "B::a cloud"


# ── generate-image-pipeline / flux without a team ───────────────────────


def _no_team_image_patches(monkeypatch):
    import app.services.inference as inf

    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    monkeypatch.setattr(ai.chroma_client, "query_similar", AsyncMock(return_value=[]))
    monkeypatch.setattr(ai.chroma_client, "add_content", AsyncMock())
    cfg = AsyncMock(return_value=("http://nv", "m", "key"))
    monkeypatch.setattr(inf, "_get_provider_config", cfg)
    monkeypatch.setattr(ai, "persist_generated_image", AsyncMock())
    return cfg


@pytest.mark.asyncio
async def test_generate_image_pipeline_without_team_skips_quota_persist_and_quality(monkeypatch):
    cfg = _no_team_image_patches(monkeypatch)
    monkeypatch.setattr(ai, "_call_nvidia_flux_pipeline", AsyncMock(return_value=b"px"))
    req = SimpleNamespace(
        prompt="p",
        negative_prompt="",
        enhance_prompt=True,
        cfg_scale=3.5,
        seed=0,
        steps=4,
        width=1024,
        height=1024,
        enhance_cfg_scale=3.5,
        enhance_steps=4,
    )
    out = await ai.generate_image_pipeline(req, _team_id(), current_user=_user(), db=_DB(team=None))
    assert out.image_base64 == "cHg="
    assert out.asset_id is None and out.quality is None
    ai.check_quota.assert_not_awaited()
    ai.chroma_client.query_similar.assert_not_awaited()
    ai.chroma_client.add_content.assert_not_awaited()
    ai.persist_generated_image.assert_not_awaited()
    assert all(c.args[1] is None for c in cfg.await_args_list)  # team-less provider lookup


@pytest.mark.asyncio
async def test_generate_image_flux_without_team_skips_quota_persist_and_quality(monkeypatch):
    _no_team_image_patches(monkeypatch)
    monkeypatch.setattr(ai, "_call_nvidia_flux", AsyncMock(return_value=b"fx"))
    out = await ai.generate_image_flux(ai.GenerateImageFluxRequest(prompt="p"), _team_id(), current_user=_user(), db=_DB(team=None))
    assert out.image_base64 == "Zng=" and out.asset_id is None and out.quality is None
    ai.check_quota.assert_not_awaited()
    ai.chroma_client.add_content.assert_not_awaited()
    ai.persist_generated_image.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_image_flux_empty_prompt_not_indexed(monkeypatch):
    _no_team_image_patches(monkeypatch)
    monkeypatch.setattr(ai, "_call_nvidia_flux", AsyncMock(return_value=b"fx"))
    monkeypatch.setattr(ai, "persist_generated_image", AsyncMock(return_value=None))
    out = await ai.generate_image_flux(ai.GenerateImageFluxRequest(prompt=""), _team_id(), current_user=_user(), db=_DB(team=_team()))
    ai.chroma_client.add_content.assert_not_awaited()  # nothing to dedupe on
    ai.persist_generated_image.assert_awaited_once()
    assert out.asset_id is None and out.quality is None  # persist returned nothing


# ── ComfyUI job status poll ─────────────────────────────────────────────


class _Comfy:
    responses: list = []
    gets: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def get(self, url, params=None):
        _Comfy.gets.append((url, params))
        return _Comfy.responses.pop(0)


def _comfy(monkeypatch, *responses):
    monkeypatch.setattr(_Comfy, "responses", list(responses))
    monkeypatch.setattr(_Comfy, "gets", [])
    monkeypatch.setattr(ai, "httpx", SimpleNamespace(AsyncClient=_Comfy))
    persist = AsyncMock()
    monkeypatch.setattr(ai, "persist_generated_image", persist)
    return persist


def _hist(job, outputs):
    return SimpleNamespace(status_code=200, json=lambda: {job: {"outputs": outputs}})


@pytest.mark.asyncio
async def test_image_status_non_200_history_is_unknown(monkeypatch):
    _comfy(monkeypatch, SimpleNamespace(status_code=502, json=dict))
    out = await ai.get_image_status("job1", uuid.uuid4(), _user(), _DB())
    assert out == {"job_id": "job1", "status": "unknown"}


@pytest.mark.asyncio
async def test_image_status_outputs_without_images_still_processing(monkeypatch):
    persist = _comfy(monkeypatch, _hist("job1", {"n1": {"images": []}, "n2": {}}))
    out = await ai.get_image_status("job1", uuid.uuid4(), _user(), _DB(team=_team()))
    assert out == {"job_id": "job1", "status": "processing"}
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_image_status_completed_without_team_does_not_persist(monkeypatch):
    persist = _comfy(monkeypatch, _hist("job1", {"n": {"images": [{"filename": "f.png"}]}}))
    out = await ai.get_image_status("job1", uuid.uuid4(), _user(), _DB(team=None))
    assert out["status"] == "completed" and out["asset_id"] is None and out["storage_path"] is None
    assert out["image_url"].endswith("/view?filename=f.png")
    persist.assert_not_awaited()
    assert len(_Comfy.gets) == 1  # never fetched /view


@pytest.mark.asyncio
async def test_image_status_already_persisted_job_is_not_duplicated(monkeypatch):
    persist = _comfy(monkeypatch, _hist("job1", {"n": {"images": [{"filename": "f.png"}]}}))
    db = _DB(results=[SimpleNamespace(id=uuid.uuid4())], team=_team())  # existing asset row
    out = await ai.get_image_status("job1", uuid.uuid4(), _user(), db)
    assert out["status"] == "completed" and out["asset_id"] is None
    persist.assert_not_awaited()
    assert len(_Comfy.gets) == 1


@pytest.mark.asyncio
async def test_image_status_view_fetch_failure_skips_persist(monkeypatch):
    persist = _comfy(
        monkeypatch,
        _hist("job1", {"n": {"images": [{"filename": "f.png", "subfolder": "s", "type": "temp"}]}}),
        SimpleNamespace(status_code=404, content=b""),
    )
    out = await ai.get_image_status("job1", uuid.uuid4(), _user(), _DB(results=[None], team=_team()))
    assert out["status"] == "completed" and out["asset_id"] is None
    persist.assert_not_awaited()
    assert _Comfy.gets[1][1] == {"filename": "f.png", "subfolder": "s", "type": "temp"}


# ── generate-content ────────────────────────────────────────────────────


def _content_patches(monkeypatch, *, inference, final_content, db_results):
    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    monkeypatch.setattr("app.services.chroma_client.query_similar", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.chroma_client.add_content", AsyncMock())
    import app.services.brand_compliance as bc
    import app.services.plain_english as pe
    import app.services.quality_pipeline as qp

    monkeypatch.setattr(bc, "load_brand_context", AsyncMock(return_value=(None, None, "")))
    monkeypatch.setattr(pe, "rewrite_plain_english", AsyncMock(side_effect=lambda c, **kw: c))
    infer = AsyncMock(return_value=inference)
    monkeypatch.setattr(ai, "call_inference", infer)
    monkeypatch.setattr(
        qp,
        "apply_quality_pipeline",
        AsyncMock(return_value=SimpleNamespace(content=final_content, hashtags=[], seo_score=None, nlp_report=None, to_dict=dict)),
    )
    return infer, _DB(results=db_results)


@pytest.mark.asyncio
async def test_generate_content_unresolved_pillar_adds_no_section(monkeypatch):
    infer, db = _content_patches(monkeypatch, inference={"content": "c"}, final_content="c", db_results=[_team()])
    import app.services.pillars as pl

    monkeypatch.setattr(pl, "resolve_pillar", AsyncMock(return_value=None))
    out = await ai.generate_content(GenerateContentRequest(prompt="x", platform="linkedin", pillar="Nope"), uuid.uuid4(), db=db, current_user=_user())
    assert "CONTENT PILLAR" not in infer.await_args.args[0]
    assert out.pillar_id is None and out.pillar_name is None


@pytest.mark.asyncio
async def test_generate_content_template_unknown_variable_left_verbatim(monkeypatch):
    tpl = SimpleNamespace(user_prompt_template="On {{topic}} for {{audience}}", variables=["audience", "topic"], system_prompt="SYS")
    infer, db = _content_patches(monkeypatch, inference={"content": "c"}, final_content="c", db_results=[_team(), tpl])
    await ai.generate_content(GenerateContentRequest(prompt="k8s", platform="linkedin", template_id=uuid.uuid4()), uuid.uuid4(), db=db, current_user=_user())
    sent = infer.await_args.args[0]
    assert "On k8s for {{audience}}" in sent  # unknown var untouched, known var filled


@pytest.mark.asyncio
async def test_generate_email_flags_ai_invented_numbers(monkeypatch):
    infer, db = _content_patches(
        monkeypatch,
        inference={"subject": "Save 40% this week", "content": "body"},
        final_content="Join 3 peers — 72% saw gains, {{first_name}}. https://x.io/p/7",
        db_results=[_team()],
    )
    out = await ai.generate_content(GenerateContentRequest(prompt="Invite 3 clients to the demo", platform="email"), uuid.uuid4(), db=db, current_user=_user())
    assert out.subject == "Save 40% this week"
    # 3 is in the brief; 7 sits inside a URL — only 40 and 72 are invented.
    assert out.warnings == ["AI-invented numbers not in your brief: 40, 72 — review or remove before sending"]


@pytest.mark.asyncio
async def test_generate_email_without_invented_numbers_has_no_warnings(monkeypatch):
    _, db = _content_patches(monkeypatch, inference={"subject": "Demo day", "content": "b"}, final_content="See you at 3", db_results=[_team()])
    out = await ai.generate_content(GenerateContentRequest(prompt="demo at 3", platform="email"), uuid.uuid4(), db=db, current_user=_user())
    assert out.warnings is None and out.subject == "Demo day"


# ── generate-carousel ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_carousel_without_team_skips_quota_dedup_and_index(monkeypatch):
    _carousel_patches(monkeypatch, similar=["x"])
    recent = AsyncMock(return_value=["old"])
    monkeypatch.setattr(ai, "_recent_published_captions", recent)
    await ai.generate_carousel(_carousel_req(), _team_id(), current_user=_user(), db=_DB(team=None))
    ai.check_quota.assert_not_awaited()
    ai.chroma_client.query_similar.assert_not_awaited()
    recent.assert_not_awaited()
    ai.chroma_client.add_content.assert_not_awaited()
    assert "avoid repeating" not in ai.call_inference.call_args.args[0]


@pytest.mark.asyncio
async def test_generate_carousel_steers_away_from_recent_published_stories(monkeypatch):
    _carousel_patches(monkeypatch)
    monkeypatch.setattr(ai, "_recent_published_captions", AsyncMock(return_value=["Pi cluster story\nlong tail", None, "  GPU tale  "]))
    await ai.generate_carousel(_carousel_req(), _team_id(), current_user=_user(), db=_DB(team=_team()))
    sent = ai.call_inference.call_args.args[0]
    assert "Do NOT retell or repackage" in sent
    assert "Pi cluster story; GPU tale" in sent and "long tail" not in sent


@pytest.mark.asyncio
async def test_generate_carousel_no_hashtags_and_no_slides(monkeypatch):
    _carousel_patches(monkeypatch, inference={"slides": [], "suggested_caption": "cap", "hashtags": []})
    import app.services.plain_english as pe
    import app.services.seo as seomod

    monkeypatch.setattr(pe, "run_nlp_check_and_fix", AsyncMock(return_value=([], "clean cap", SimpleNamespace(to_dict=dict))))
    out = await ai.generate_carousel(_carousel_req(), _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert seomod.analyze_seo.await_args.kwargs["text"] == "clean cap"  # no hashtag block appended
    ai.chroma_client.add_content.assert_not_awaited()  # nothing to index
    assert out.hashtags == []


# ── generate-carousel-pipeline ──────────────────────────────────────────


def _pipeline_patches(monkeypatch, *, slides, hashtags=("a",), dup=False):
    import app.services.carousel_pipeline as cp
    import app.services.duplicate_detector as dd
    import app.services.plain_english as pe
    import app.services.seo as seomod
    import app.services.spellcheck as sp

    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    copy = SimpleNamespace(slides=[], suggested_caption="cap", hashtags=list(hashtags))
    monkeypatch.setattr(ai, "generate_carousel", AsyncMock(return_value=copy))
    monkeypatch.setattr(pe, "run_nlp_check_and_fix", AsyncMock(return_value=(slides, "cap", SimpleNamespace(to_dict=dict))))
    monkeypatch.setattr(ai, "_recent_published_captions", AsyncMock(return_value=["cap"] if dup else []))
    monkeypatch.setattr(dd, "is_duplicate", lambda a, b, threshold: dup)
    monkeypatch.setattr(ai, "_call_cf_image_pipeline", AsyncMock(side_effect=RuntimeError("no bg")))
    monkeypatch.setattr(cp, "compose_branded_slide", AsyncMock())
    monkeypatch.setattr(sp, "auto_correct", AsyncMock(side_effect=lambda s: s))
    monkeypatch.setattr(seomod, "analyze_seo", AsyncMock(return_value={"score": {"t": 1}}))
    monkeypatch.setattr(ai, "_save_generation_template", AsyncMock())


@pytest.mark.asyncio
async def test_carousel_pipeline_refuses_caption_duplicated_twice(monkeypatch):
    _pipeline_patches(monkeypatch, slides=[{"title": "T", "body": "b"}], dup=True)
    with pytest.raises(HTTPException) as e:
        await ai.generate_carousel_pipeline(ai.GenerateCarouselPipelineRequest(topic="t"), _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert e.value.status_code == 409 and "duplicates a recently published post" in e.value.detail
    assert ai.generate_carousel.await_count == 2  # one strict retry, then refuse
    ai._call_cf_image_pipeline.assert_not_awaited()  # refused before any media render


@pytest.mark.asyncio
async def test_carousel_pipeline_without_slides_or_hashtags_saves_no_template(monkeypatch):
    _pipeline_patches(monkeypatch, slides=[], hashtags=())
    import app.services.seo as seomod

    out = await ai.generate_carousel_pipeline(ai.GenerateCarouselPipelineRequest(topic="t"), _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert out.media_ids == [] and out.slides == []
    assert seomod.analyze_seo.await_args.kwargs["text"] == "cap"
    ai._save_generation_template.assert_not_awaited()


# ── DMR vision / emoji ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dmr_vision_keeps_existing_data_uri(monkeypatch):
    import app.services.dmr as dmr

    vision = AsyncMock(return_value="a cat")
    monkeypatch.setattr(dmr, "call_dmr_vision", vision)
    uri = "data:image/png;base64,QUJD"
    await ai.dmr_vision(_REQ, ai.DmrVisionRequest(image_base64=uri, prompt="what?"), _user())
    assert vision.await_args.args[0] == uri  # not re-wrapped as image/jpeg


def test_emoji_prompt_colored_without_color_has_no_background_clause():
    pos, _ = ai._build_emoji_prompt("rocket", "flat", "colored", None)
    assert "background" not in pos.split("rocket", 1)[1].split("high quality")[0]
    pos2, _ = ai._build_emoji_prompt("rocket", "flat", "colored", "#ff0000")
    assert "on solid #ff0000 background" in pos2


@pytest.mark.asyncio
async def test_emoji_cloudflare_provider_and_short_enhancement_ignored(monkeypatch):
    import app.services.dmr as dmr
    import app.services.inference as inf

    local = AsyncMock()
    monkeypatch.setattr(inf, "_call_local_diffusers_txt2img", local)
    monkeypatch.setattr(inf, "_get_provider_config", AsyncMock(return_value=(None, "m", "key")))
    monkeypatch.setattr(inf, "_call_workers_ai_image", AsyncMock(return_value={"image_base64": "Q0Y="}))
    monkeypatch.setattr(dmr, "call_dmr_chat", AsyncMock(return_value={"text": " ok "}))  # ≤10 chars → ignored
    monkeypatch.setattr(dmr, "call_dmr_vision", AsyncMock(return_value=None))
    req = EmojiGenerateRequest(concept="party popper", provider="cloudflare", background="white", remove_bg=False)
    out = await ai.generate_emoji(_REQ, req, uuid.uuid4(), db=_DB(team=None), current_user=_user())
    assert out.provider == "cloudflare" and out.image_base64 == "Q0Y="
    assert out.enhanced_prompt.startswith("party popper,")
    local.assert_not_awaited()


@pytest.mark.asyncio
async def test_emoji_batch_without_team_skips_quota(monkeypatch):
    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    ok = SimpleNamespace(image_base64="A", provider="cloudflare", quality_check=None)
    monkeypatch.setattr(ai, "generate_emoji", AsyncMock(return_value=ok))
    out = await ai.generate_emoji_batch(_REQ, EmojiBatchRequest(concepts=["one"]), uuid.uuid4(), current_user=_user(), db=_DB(team=None))
    ai.check_quota.assert_not_awaited()
    assert out.emojis == [{"concept": "one", "image_base64": "A", "success": True, "provider": "cloudflare", "quality_check": None}]


@pytest.mark.asyncio
async def test_schedule_chroma_index_drops_write_when_capped():
    """A full pending set → the new coroutine is closed, never scheduled."""
    import asyncio as _aio

    async def _park():
        await _aio.sleep(60)

    parked = [_aio.ensure_future(_park()) for _ in range(2)]
    try:
        ai._PENDING_INDEX_TASKS.clear()
        ai._PENDING_INDEX_TASKS.update(parked)
        doomed = _park()
        ai._schedule_chroma_index(doomed)
        assert doomed.cr_frame is None  # closed, never started
        assert len(ai._PENDING_INDEX_TASKS) == 2
    finally:
        for t in parked:
            t.cancel()
        ai._PENDING_INDEX_TASKS.clear()
