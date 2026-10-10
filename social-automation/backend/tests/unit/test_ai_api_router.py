"""Endpoint-level tests for app/api/ai.py thin wrappers.

Covers transcribe/batch/prompt/auto-configure/analyze/seo/nlp/score,
drafts + post-draft, image-status poll, web-search, hashtags (both),
best-time, improve-content, workflow-config, spellcheck, seed-defaults,
the DMR management surface, and emoji helpers — previously ~40%.
"""

from __future__ import annotations

import base64
import importlib
import io
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

from app.api import ai
from app.api.ai import (
    AnalyzeContentRequest,
    AutoConfigureRequest,
    BatchInferenceItem,
    BatchInferenceRetrieveRequest,
    BatchInferenceSubmitRequest,
    ContentScoreRequest,
    DmrBenchmarkRequest,
    DmrChatRequest,
    DmrKeepAliveRequest,
    DmrSpeculativeDecodingRequest,
    DmrVisionRequest,
    GenerateImagePromptRequest,
    ImproveContentRequest,
    NlpCheckRequest,
    PostDraftRequest,
    SaveDraftRequest,
    SeoRequest,
    SpellcheckRequest,
    SuggestHashtagsRequest,
    _build_emoji_prompt,
    _remove_background_white,
    _validate_comfyui_job_id,
    ai_web_search,
    analyze_content,
    analyze_seo_endpoint,
    auto_configure,
    best_time_to_post,
    call_ollama,
    dmr_benchmark,
    dmr_chat,
    dmr_keep_alive,
    dmr_requests,
    dmr_speculative_decoding,
    dmr_status,
    dmr_vision,
    dmr_warmup,
    generate_image_prompt,
    get_image_status,
    get_workflow_config,
    improve_content,
    list_drafts,
    nlp_check_endpoint,
    post_draft,
    retrieve_batch_inference,
    save_draft,
    score_content_endpoint,
    seed_default_workflows,
    spellcheck,
    submit_batch_inference,
    suggest_hashtags,
    suggest_hashtags_tiered,
    transcribe_audio,
)

# ── fakes ─────────────────────────────────────────────────────────────


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
    def __init__(self, results=(), team=None):
        self._q = list(results)
        self._team = team
        self.added = []

    async def get(self, model, _id):
        return self._team

    async def execute(self, stmt):
        return _Res(self._q.pop(0) if self._q else None)

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = uuid.uuid4()

    async def commit(self):
        pass

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


def _team():
    return SimpleNamespace(id=uuid.uuid4())


def _user():
    return SimpleNamespace(id=uuid.uuid4(), email="admin@cloudless.gr")


def _team_id():
    return uuid.uuid4()


class _Upload:
    def __init__(self, content: bytes, content_type="audio/wav"):
        self._c = content
        self.content_type = content_type
        self.filename = "a.wav"

    async def read(self):
        return self._c


@pytest.fixture(autouse=True)
def _limiter_off(monkeypatch):
    monkeypatch.setattr(ai.limiter, "enabled", False)


_REQ = SimpleNamespace()  # placeholder Request for @limiter.limit endpoints


def _mod(name: str):
    return importlib.import_module(name)


# ── shim ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_ollama_delegates(monkeypatch):
    monkeypatch.setattr(ai, "call_inference", AsyncMock(return_value={"ok": 1}))
    out = await call_ollama("p", model="m", schema={"s": 1})
    assert out == {"ok": 1}
    assert ai.call_inference.await_args.kwargs["provider_name"] == "dmr"


# ── transcribe ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_empty_400():
    with pytest.raises(HTTPException) as e:
        await transcribe_audio(_REQ, _Upload(b""), None, _user(), _DB())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_transcribe_too_large_413():
    big = b"x" * (ai.MAX_AUDIO_SIZE_BYTES + 1)
    with pytest.raises(HTTPException) as e:
        await transcribe_audio(_REQ, _Upload(big), None, _user(), _DB())
    assert e.value.status_code == 413


@pytest.mark.asyncio
async def test_transcribe_success_maps_fields(monkeypatch):
    monkeypatch.setattr(
        ai, "transcribe_workers_ai", AsyncMock(return_value={"text": "hello", "detected_language": "en", "duration_seconds": 1.2, "confidence": 0.9})
    )
    out = await transcribe_audio(_REQ, _Upload(b"wav"), "whisper", _user(), _DB())
    assert out.text == "hello"
    assert out.language == "en"
    assert out.duration == 1.2
    assert out.detections == {"confidence": 0.9}
    # alias resolved to full model id
    assert ai.transcribe_workers_ai.await_args.kwargs["model"] == "@cf/openai/whisper"


@pytest.mark.asyncio
async def test_transcribe_service_error_500(monkeypatch):
    monkeypatch.setattr(ai, "transcribe_workers_ai", AsyncMock(side_effect=RuntimeError("down")))
    with pytest.raises(HTTPException) as e:
        await transcribe_audio(_REQ, _Upload(b"wav"), None, _user(), _DB())
    assert e.value.status_code == 500


@pytest.mark.asyncio
async def test_transcribe_empty_text_422(monkeypatch):
    monkeypatch.setattr(ai, "transcribe_workers_ai", AsyncMock(return_value={"text": ""}))
    with pytest.raises(HTTPException) as e:
        await transcribe_audio(_REQ, _Upload(b"wav"), None, _user(), _DB())
    assert e.value.status_code == 422


# ── batch inference ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_submit(monkeypatch):
    monkeypatch.setattr(ai, "submit_workers_ai_batch", AsyncMock(return_value={"request_id": "r1", "status": "queued", "model": "m"}))
    out = await submit_batch_inference(BatchInferenceSubmitRequest(model="@cf/x", requests=[BatchInferenceItem(text="hi")]), _user())
    assert out.request_id == "r1"


@pytest.mark.asyncio
async def test_batch_submit_error_500(monkeypatch):
    monkeypatch.setattr(ai, "submit_workers_ai_batch", AsyncMock(side_effect=ValueError("x")))
    with pytest.raises(HTTPException) as e:
        await submit_batch_inference(BatchInferenceSubmitRequest(model="@cf/x", requests=[]), _user())
    assert e.value.status_code == 500


@pytest.mark.asyncio
async def test_batch_retrieve(monkeypatch):
    monkeypatch.setattr(ai, "retrieve_workers_ai_batch", AsyncMock(return_value={"status": "done"}))
    out = await retrieve_batch_inference(BatchInferenceRetrieveRequest(model="m", request_id="r"), _user())
    assert out["status"] == "done"


# ── generate-image-prompt / auto-configure ────────────────────────────


def _quality_mock(monkeypatch, prompt="better prompt"):
    q = SimpleNamespace(prompt=prompt, negative_prompt="np", to_dict=lambda: {"score": 90})
    mod = _mod("app.services.media_quality")
    monkeypatch.setattr(mod, "apply_media_quality", AsyncMock(return_value=q))
    return q


@pytest.mark.asyncio
async def test_generate_image_prompt(monkeypatch):
    monkeypatch.setattr(ai, "call_inference", AsyncMock(return_value={"prompt": "raw", "negative_prompt": "rawneg"}))
    _quality_mock(monkeypatch)
    out = await generate_image_prompt(GenerateImagePromptRequest(description="a cat"), _user())
    assert out.prompt == "better prompt"
    assert out.negative_prompt == "np"
    assert out.quality == {"score": 90}


@pytest.mark.asyncio
async def test_auto_configure_image_runs_quality(monkeypatch):
    monkeypatch.setattr(
        ai,
        "call_inference",
        AsyncMock(
            return_value={
                "task_type": "image",
                "model": "m",
                "steps": 6,
                "style": "photorealistic",
                "platform": "instagram",
                "tone": "casual",
                "num_slides": 5,
                "enhanced_prompt": ["part1", "part2"],
                "negative_prompt": "bad",
            }
        ),
    )
    _quality_mock(monkeypatch)
    out = await auto_configure(AutoConfigureRequest(prompt="x", context="image"), _user())
    assert out.task_type == "image"
    assert out.enhanced_prompt == "better prompt"  # list joined → quality-fixed
    assert out.quality == {"score": 90}


@pytest.mark.asyncio
async def test_auto_configure_text_no_quality(monkeypatch):
    monkeypatch.setattr(
        ai,
        "call_inference",
        AsyncMock(
            return_value={
                "task_type": "text",
                "model": "m",
                "steps": 6,
                "style": "professional",
                "platform": "linkedin",
                "tone": "professional",
                "num_slides": 5,
            }
        ),
    )
    amq = AsyncMock()
    monkeypatch.setattr(_mod("app.services.media_quality"), "apply_media_quality", amq)
    out = await auto_configure(AutoConfigureRequest(prompt="x"), _user())
    assert out.enhanced_prompt is None
    amq.assert_not_awaited()


# ── analyze / seo / nlp / score ───────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_content_success(monkeypatch):
    monkeypatch.setattr(
        ai,
        "call_inference",
        AsyncMock(
            return_value={
                "sentiment": "positive",
                "readability_score": 8.0,
                "estimated_reach": "high",
                "suggestions": ["shorter"],
                "hashtag_score": 7,
                "engagement_prediction": "high",
            }
        ),
    )
    issue = SimpleNamespace(field="content", reason="jargon_or_buzzwords", matches=["synergy"])
    monkeypatch.setattr(ai, "check_plain_english", lambda t: [issue])
    seo_mod = _mod("app.services.seo")
    monkeypatch.setattr(seo_mod, "analyze_seo", AsyncMock(return_value={"score": {"total": 88}}))
    out = await analyze_content(AnalyzeContentRequest(content="We leverage synergy. It is great!", platform="twitter"), _team_id(), _user(), _DB(team=_team()))
    assert out.sentiment == "positive"
    assert "jargon_or_buzzwords" in out.suggestions  # plain-english reasons appended
    assert out.plain_english_issues[0]["reason"] == "jargon_or_buzzwords"
    assert out.seo_score == {"total": 88}


@pytest.mark.asyncio
async def test_analyze_content_inference_fallback(monkeypatch):
    monkeypatch.setattr(ai, "call_inference", AsyncMock(side_effect=RuntimeError("dmr down")))
    monkeypatch.setattr(ai, "check_plain_english", lambda t: [])
    monkeypatch.setattr(_mod("app.services.seo"), "analyze_seo", AsyncMock(side_effect=RuntimeError("seo down")))
    out = await analyze_content(AnalyzeContentRequest(content="x", platform="tiktok"), _team_id(), _user(), _DB(team=None))
    assert out.sentiment == "neutral"  # fallback defaults
    assert out.seo_score is None


@pytest.mark.asyncio
async def test_analyze_seo_endpoint(monkeypatch):
    monkeypatch.setattr(
        ai.seo,
        "analyze_seo",
        AsyncMock(
            return_value={
                "platform": "linkedin",
                "score": {"total": 90},
                "keywords": [],
                "meta": {},
                "open_graph": {},
                "character_count": 10,
                "hashtag_count": 0,
                "link_count": 0,
            }
        ),
    )
    out = await analyze_seo_endpoint(SeoRequest(content="c"), _team_id(), _user(), _DB(team=_team()))
    assert out.score["total"] == 90


@pytest.mark.asyncio
async def test_nlp_check(monkeypatch):
    from app.services.plain_english import NlpIssue

    monkeypatch.setattr(
        _mod("app.services.plain_english"),
        "sofia_nlp_score",
        lambda t: (62, [NlpIssue(field="content", reason="em_dash", snippet="a — b", matches=["—"])]),
    )
    out = await nlp_check_endpoint(NlpCheckRequest(content="Hello. World ok."), _user())
    assert out.score == 62
    assert out.issue_count == 1
    assert out.recommendations[0].startswith("Em dash")


@pytest.mark.asyncio
async def test_score_content(monkeypatch):
    mod = _mod("app.services.content_scorer")
    monkeypatch.setattr(
        mod,
        "score_content",
        lambda *a, **k: SimpleNamespace(to_dict=lambda: {"readability": 8, "engagement": 7, "hashtag_quality": 6, "length_fit": 9, "overall": 7.5}),
    )
    monkeypatch.setattr(mod, "build_hashtag_strategy", lambda **k: SimpleNamespace(to_dict=lambda: {"safe": ["x"]}))
    out = await score_content_endpoint(ContentScoreRequest(content="post", platform="linkedin", hashtags=["x"]), _user())
    assert out.overall == 7.5
    assert out.hashtag_strategy == {"safe": ["x"]}


# ── drafts ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_draft_no_team_404():
    with pytest.raises(HTTPException) as e:
        await save_draft(SaveDraftRequest(prompt="p", image_base64="x"), uuid.uuid4(), _user(), _DB())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_save_draft_writes_chroma(monkeypatch):
    added = []
    monkeypatch.setattr(ai.chroma_client, "add_content", AsyncMock(side_effect=lambda *a: added.append(a)))
    out = await save_draft(SaveDraftRequest(prompt="p", image_base64="x"), uuid.uuid4(), _user(), _DB(team=_team()))
    assert out.draft_id
    assert added[0][2].startswith("DRAFT:")


@pytest.mark.asyncio
async def test_list_drafts_parses_json(monkeypatch):
    good = json.dumps({"prompt": "p", "image_base64": "b64", "created_at": "t"})
    monkeypatch.setattr(ai.chroma_client, "query_similar", AsyncMock(return_value=[f"DRAFT:{good}", "junk", "DRAFT:{bad"]))
    out = await list_drafts(uuid.uuid4(), _user(), _DB(team=_team()))
    assert len(out.drafts) == 1
    assert out.drafts[0].prompt == "p"


@pytest.mark.asyncio
async def test_post_draft_no_account_404(monkeypatch):
    with pytest.raises(HTTPException) as e:
        await post_draft(PostDraftRequest(draft_id="d", platform="linkedin"), uuid.uuid4(), _user(), _DB(results=[None], team=_team()))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_post_draft_missing_draft_404(monkeypatch):
    monkeypatch.setattr(ai.chroma_client, "get_content", AsyncMock(return_value=None))
    acc = SimpleNamespace(id=uuid.uuid4())
    with pytest.raises(HTTPException) as e:
        await post_draft(PostDraftRequest(draft_id="d", platform="linkedin"), uuid.uuid4(), _user(), _DB(results=[acc], team=_team()))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_post_draft_happy_path(monkeypatch):
    draft = json.dumps({"prompt": "p", "image_base64": base64.b64encode(b"png").decode(), "caption": "cap", "hashtags": ["h"]})
    monkeypatch.setattr(ai.chroma_client, "get_content", AsyncMock(return_value=f"DRAFT:{draft}"))
    monkeypatch.setattr(ai, "persist_generated_image", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())))

    class _Conn:
        def __enter__(self):
            return "conn"

        def __exit__(self, *a):
            return None

    monkeypatch.setattr(ai.celery_app, "connection_or_acquire", lambda: _Conn())
    sent = []
    monkeypatch.setattr(ai.publish_post_now, "apply_async", lambda **k: sent.append(k))
    monkeypatch.setattr(ai.process_publish_queue, "apply_async", lambda **k: sent.append(k))
    acc = SimpleNamespace(id=uuid.uuid4())
    db = _DB(results=[acc], team=_team())
    out = await post_draft(PostDraftRequest(draft_id="d", platform="linkedin"), uuid.uuid4(), _user(), db)
    assert out.success is True
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_post_draft_queue_failure_reports(monkeypatch):
    draft = json.dumps({"prompt": "p", "caption": "cap"})
    monkeypatch.setattr(ai.chroma_client, "get_content", AsyncMock(return_value=f"DRAFT:{draft}"))
    monkeypatch.setattr(ai.celery_app, "connection_or_acquire", lambda: (_ for _ in ()).throw(RuntimeError("redis down")))
    acc = SimpleNamespace(id=uuid.uuid4())
    db = _DB(results=[acc], team=_team())
    out = await post_draft(PostDraftRequest(draft_id="d", platform="linkedin"), uuid.uuid4(), _user(), db)
    assert out.success is False
    assert "could not be queued" in out.message


# ── image status / job id ─────────────────────────────────────────────


def test_validate_comfyui_job_id():
    assert _validate_comfyui_job_id("abc-123") == "abc-123"
    with pytest.raises(HTTPException) as e:
        _validate_comfyui_job_id("../escape")
    assert e.value.status_code == 400


class _Client:
    get = AsyncMock()
    post = AsyncMock()

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None


@pytest.mark.asyncio
async def test_get_image_status_processing(monkeypatch):
    _Client.get = AsyncMock(return_value=SimpleNamespace(status_code=200, json=lambda: {}))
    monkeypatch.setattr(ai, "httpx", SimpleNamespace(AsyncClient=_Client))
    out = await get_image_status("job1", uuid.uuid4(), _user(), _DB())
    assert out["status"] == "processing"


@pytest.mark.asyncio
async def test_get_image_status_completed_persists(monkeypatch):
    job = "job9"
    history = {job: {"outputs": {"n": {"images": [{"filename": "f.png"}]}}}}
    img_resp = SimpleNamespace(status_code=200, content=b"pngbytes", json=lambda: {})
    calls = [SimpleNamespace(status_code=200, json=lambda: history), img_resp]
    _Client.get = AsyncMock(side_effect=calls)
    monkeypatch.setattr(ai, "httpx", SimpleNamespace(AsyncClient=_Client))
    asset = SimpleNamespace(id=uuid.uuid4(), filename=None, generation_prompt=None, alt_text=None, storage_path="p/f.png")
    monkeypatch.setattr(ai, "persist_generated_image", AsyncMock(return_value=asset))
    db = _DB(results=[None], team=_team())  # existing check → none
    out = await get_image_status(job, uuid.uuid4(), _user(), db)
    assert out["status"] == "completed"
    assert out["asset_id"] == str(asset.id)
    assert asset.generation_prompt == f"comfyui:{job}"


@pytest.mark.asyncio
async def test_get_image_status_unknown_on_error(monkeypatch):
    _Client.get = AsyncMock(side_effect=RuntimeError("conn"))
    monkeypatch.setattr(ai, "httpx", SimpleNamespace(AsyncClient=_Client))
    out = await get_image_status("job1", uuid.uuid4(), _user(), _DB())
    assert out["status"] == "unknown"


# ── web-search / hashtags / best-time / improve ───────────────────────


@pytest.mark.asyncio
async def test_web_search(monkeypatch):
    monkeypatch.setattr(_mod("app.services.web_search"), "web_search", AsyncMock(return_value=[{"title": "t"}]))
    out = await ai_web_search("query", 3, _user())
    assert out["results"] == [{"title": "t"}]


@pytest.mark.asyncio
async def test_suggest_hashtags_platform_cap(monkeypatch):
    monkeypatch.setattr(ai, "call_inference", AsyncMock(return_value={"hashtags": ["#one", "two", "three", "four", "  "]}))
    out = await suggest_hashtags(SuggestHashtagsRequest(content="x", platform="twitter"), _user(), _DB())
    assert out.hashtags == ["one", "two"]  # twitter cap = 2


@pytest.mark.asyncio
async def test_suggest_hashtags_tiered(monkeypatch):
    monkeypatch.setattr(
        ai,
        "call_inference",
        AsyncMock(
            return_value={
                "safe": [{"tag": "#broad", "estimated_reach": "broad"}],
                "rising": [{"tag": "trend", "estimated_reach": "mid"}],
                "niche": [{"tag": "cloudlessgr", "estimated_reach": "low"}],
            }
        ),
    )
    out = await suggest_hashtags_tiered(SuggestHashtagsRequest(content="x", platform="linkedin"), _user(), _DB())
    assert out.hashtags == ["broad", "trend", "cloudlessgr"]
    assert out.tiers["niche"][0].tier == "niche"


@pytest.mark.asyncio
async def test_best_time_404_no_account():
    with pytest.raises(HTTPException) as e:
        await best_time_to_post(SimpleNamespace(account_id=uuid.uuid4()), uuid.uuid4(), _user(), _DB(results=[None]))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_best_time_defaults_when_sparse(monkeypatch):
    acc = SimpleNamespace(id=uuid.uuid4(), platform="instagram")
    db = _DB(results=[acc, []])  # account, no samples
    out = await best_time_to_post(SimpleNamespace(account_id=acc.id), uuid.uuid4(), _user(), db)
    assert out.posts_analyzed == 0
    assert out.best_times[0]["timezone"] == "Europe/Athens"


@pytest.mark.asyncio
async def test_best_time_uses_analytics(monkeypatch):
    acc = SimpleNamespace(id=uuid.uuid4(), platform="linkedin")
    samples = [(datetime_now(), 0.5)] * 12
    db = _DB(results=[acc, samples])
    ranked = [{"day": "Monday", "time": "08:00", "timezone": "Europe/Athens", "score": 1}]
    monkeypatch.setattr(_mod("app.services.linkedin_ai"), "rank_best_time_windows", lambda s, tz: ranked)
    out = await best_time_to_post(SimpleNamespace(account_id=acc.id), uuid.uuid4(), _user(), db)
    assert out.source == "analytics"
    assert out.posts_analyzed == 12


def datetime_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


@pytest.mark.asyncio
async def test_improve_content(monkeypatch):
    monkeypatch.setattr(ai, "call_inference", AsyncMock(return_value={"improved_content": "better", "changes": ["shortened"]}))
    monkeypatch.setattr(_mod("app.services.brand_compliance"), "load_brand_context", AsyncMock(return_value=(None, None, "brandctx")))
    q = SimpleNamespace(content="better", seo_score={"total": 91}, nlp_report={"score": 95}, improved=True, to_dict=lambda: {"improved": True})
    monkeypatch.setattr(_mod("app.services.quality_pipeline"), "apply_quality_pipeline", AsyncMock(return_value=q))
    out = await improve_content(ImproveContentRequest(content="orig", platform="linkedin"), uuid.uuid4(), _user(), _DB(team=_team()))
    assert out.improved_content == "better"
    assert out.seo_score == {"total": 91}


# ── misc thin endpoints ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_config():
    key = next(iter(ai.CONTENT_WORKFLOW_CONFIGS))
    out = await get_workflow_config(key, _user())
    assert out["content_type"] == key


@pytest.mark.asyncio
async def test_workflow_config_404():
    with pytest.raises(HTTPException) as e:
        await get_workflow_config("nonexistent", _user())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_spellcheck_empty_shortcircuit():
    out = await spellcheck(SpellcheckRequest(text="  "), _user())
    assert out.matches == []


@pytest.mark.asyncio
async def test_spellcheck_success(monkeypatch):
    ok = SimpleNamespace(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {
            "matches": [{"message": "typo", "offset": 0, "length": 3, "replacements": [{"value": "fix"}], "rule": {"id": "R1"}, "context": {"text": "abc"}}]
        },
    )
    _Client.post = AsyncMock(return_value=ok)
    monkeypatch.setattr(ai, "httpx", SimpleNamespace(AsyncClient=_Client, HTTPStatusError=httpx.HTTPStatusError, RequestError=httpx.RequestError))
    out = await spellcheck(SpellcheckRequest(text="abc"), _user())
    assert out.matches[0].rule_id == "R1"


@pytest.mark.asyncio
async def test_spellcheck_503_unreachable(monkeypatch):
    _Client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
    monkeypatch.setattr(ai, "httpx", SimpleNamespace(AsyncClient=_Client, HTTPStatusError=httpx.HTTPStatusError, RequestError=httpx.RequestError))
    with pytest.raises(HTTPException) as e:
        await spellcheck(SpellcheckRequest(text="abc"), _user())
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_seed_default_workflows():
    db = _DB(team=_team())
    out = await seed_default_workflows(uuid.uuid4(), db, _user())
    assert len(out["seeded"]) == len(ai.CONTENT_WORKFLOW_CONFIGS)
    assert len(db.added) == len(ai.CONTENT_WORKFLOW_CONFIGS)


@pytest.mark.asyncio
async def test_seed_default_workflows_400():
    with pytest.raises(HTTPException) as e:
        await seed_default_workflows(uuid.uuid4(), _DB(), _user())
    assert e.value.status_code == 400


# ── DMR management ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dmr_status_offline(monkeypatch):
    mod = _mod("app.services.dmr")
    monkeypatch.setattr(mod, "_check_dmr_health", AsyncMock(return_value=False))
    monkeypatch.setattr(mod, "_get_vram_info", AsyncMock(return_value=None))
    monkeypatch.setattr(mod, "is_warmup_done", lambda: False)
    out = await dmr_status(_REQ, _user())
    assert out.online is False
    assert out.missing_models == []


@pytest.mark.asyncio
async def test_dmr_status_online_missing_models(monkeypatch):
    mod = _mod("app.services.dmr")
    monkeypatch.setattr(mod, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(mod, "_get_vram_info", AsyncMock(return_value={"used": 1}))
    monkeypatch.setattr(mod, "validate_dmr_models", AsyncMock(return_value={"missing": ["m2"]}))
    monkeypatch.setattr(mod, "is_warmup_done", lambda: True)
    out = await dmr_status(_REQ, _user())
    assert out.missing_models == ["m2"]
    assert out.warmup_done is True


@pytest.mark.asyncio
async def test_dmr_chat_nonstream(monkeypatch):
    monkeypatch.setattr(_mod("app.services.dmr"), "call_dmr_chat", AsyncMock(return_value={"response": "hi"}))
    out = await dmr_chat(_REQ, DmrChatRequest(prompt="p"), _user())
    assert out == {"response": "hi"}


@pytest.mark.asyncio
async def test_dmr_chat_stream(monkeypatch):
    async def _gen():
        yield "a"
        yield "b"

    monkeypatch.setattr(_mod("app.services.dmr"), "call_dmr_chat", AsyncMock(return_value={"stream": _gen()}))
    out = await dmr_chat(_REQ, DmrChatRequest(prompt="p", stream=True), _user())
    assert out.media_type == "text/event-stream"
    chunks = [c async for c in out.body_iterator]
    assert '"content": "a"' in chunks[0]
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_dmr_vision(monkeypatch):
    seen = {}

    async def _vision(uri, prompt, max_tokens, model_override):
        seen["uri"] = uri
        return "a cat"

    monkeypatch.setattr(_mod("app.services.dmr"), "call_dmr_vision", _vision)
    out = await dmr_vision(_REQ, DmrVisionRequest(image_base64="aGk=", prompt="what"), _user())
    assert out.text == "a cat"
    assert seen["uri"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_dmr_vision_failure(monkeypatch):
    monkeypatch.setattr(_mod("app.services.dmr"), "call_dmr_vision", AsyncMock(return_value=None))
    out = await dmr_vision(_REQ, DmrVisionRequest(image_base64="x", prompt="p"), _user())
    assert out.error


@pytest.mark.asyncio
async def test_dmr_benchmark(monkeypatch):
    monkeypatch.setattr(_mod("app.services.dmr"), "get_model_benchmark", AsyncMock(return_value={"tps": 50}))
    out = await dmr_benchmark(_REQ, DmrBenchmarkRequest(model="m"), _user())
    assert out == {"tps": 50}
    monkeypatch.setattr(_mod("app.services.dmr"), "get_model_benchmark", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as e:
        await dmr_benchmark(_REQ, DmrBenchmarkRequest(model="m"), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_dmr_requests(monkeypatch):
    monkeypatch.setattr(_mod("app.services.dmr"), "get_dmr_requests", AsyncMock(return_value=[{"r": 1}]))
    out = await dmr_requests(_REQ, model="m", limit=5, current_user=_user())
    assert out == [{"r": 1}]


@pytest.mark.asyncio
async def test_dmr_warmup(monkeypatch):
    mod = _mod("app.services.dmr")
    calls = []
    monkeypatch.setattr(mod, "reset_warmup", lambda: calls.append("reset"))
    monkeypatch.setattr(mod, "warmup_models", AsyncMock())
    monkeypatch.setattr(mod, "is_warmup_done", lambda: True)
    out = await dmr_warmup(_REQ, _user())
    assert out["warmup_done"] is True
    assert calls == ["reset"]


@pytest.mark.asyncio
async def test_dmr_keep_alive(monkeypatch):
    ka = AsyncMock()
    monkeypatch.setattr(_mod("app.services.dmr"), "configure_keep_alive", ka)
    out = await dmr_keep_alive(_REQ, DmrKeepAliveRequest(model="m", keep_alive="10m"), _user())
    assert out["keep_alive"] == "10m"
    ka.assert_awaited_once()


@pytest.mark.asyncio
async def test_dmr_speculative_decoding(monkeypatch):
    sd = AsyncMock()
    monkeypatch.setattr(_mod("app.services.dmr"), "configure_speculative_decoding", sd)
    await dmr_speculative_decoding(_REQ, DmrSpeculativeDecodingRequest(model="m"), _user())
    sd.assert_awaited_once()


# ── emoji helpers ─────────────────────────────────────────────────────


def test_build_emoji_prompt():
    pos, neg = _build_emoji_prompt("rocket", "flat", "transparent", None)
    assert "rocket" in pos
    assert "white background" in pos
    assert "complex background" in neg
    # colored bg uses the supplied color
    pos2, _ = _build_emoji_prompt("icon", "flat", "colored", "#ff00ff")
    assert "#ff00ff" in pos2
    # unknown style falls back to flat preset without error
    _build_emoji_prompt("x", "no-such-style", "white", None)


@pytest.mark.asyncio
async def test_remove_background_white():
    img = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
    img.putpixel((0, 0), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = await _remove_background_white(base64.b64encode(buf.getvalue()).decode())
    result = Image.open(io.BytesIO(base64.b64decode(out)))
    assert result.getpixel((1, 1))[3] == 0  # white → transparent
    assert result.getpixel((0, 0)) == (255, 0, 0, 255)  # red kept
