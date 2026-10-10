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


# ── generate-content / generate-workflow / templates / emoji ─────────


from app.api.ai import (  # noqa: E402
    EmojiGenerateRequest,
    GenerateContentRequest,
    GenerateWorkflowRequest,
    SaveGenerationTemplateRequest,
    generate_content,
    generate_emoji,
    generate_workflow,
    list_emoji_styles,
    save_generation_template,
)


@pytest.mark.asyncio
async def test_generate_content_happy(monkeypatch):
    team = _team()
    db = _DB(results=[team])
    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    q = AsyncMock(return_value=["old similar post"])
    add = AsyncMock()
    monkeypatch.setattr("app.services.chroma_client.query_similar", q)
    monkeypatch.setattr("app.services.chroma_client.add_content", add)
    import app.services.brand_compliance as bc

    monkeypatch.setattr(bc, "load_brand_context", AsyncMock(return_value=({"b": 1}, {"voice_signature": {"post_blueprint": 1}}, "brandctx")))
    monkeypatch.setattr(bc, "score_brand_compliance", AsyncMock(return_value={"score": 88}))
    import app.services.plain_english as pe

    monkeypatch.setattr(pe, "rewrite_plain_english", AsyncMock(side_effect=lambda c, **kw: c))
    infer = AsyncMock(return_value={"content": "the post", "hashtags": ["#a"], "suggested_media": "img"})
    monkeypatch.setattr(ai, "call_inference", infer)
    import app.services.quality_pipeline as qp

    quality = SimpleNamespace(content="final", hashtags=["#a"], seo_score={"total": 92}, nlp_report={"ok": 1}, to_dict=lambda: {"score": 92})
    monkeypatch.setattr(qp, "apply_quality_pipeline", AsyncMock(return_value=quality))

    req = GenerateContentRequest(prompt="write about the pi cluster", platform="linkedin")
    out = await generate_content(req, uuid.uuid4(), db=db, current_user=_user())
    assert out.content == "final" and out.hashtags == ["#a"]
    assert out.seo_score == {"total": 92} and out.brand_compliance == {"score": 88}
    sent = infer.await_args.args[0]
    assert "avoid repeating" in sent  # chroma context injected
    assert "Structure (mandatory" in sent  # post_blueprint hint
    assert "brandctx" in sent  # brand context
    add.assert_awaited_once()  # indexed for dedup


@pytest.mark.asyncio
async def test_generate_content_template_pillar_web(monkeypatch):
    team = _team()
    tpl = SimpleNamespace(
        user_prompt_template="About {{topic}} for {{platform}} ({{tone}}) {{brand}} {{length}}",
        variables=["topic", "platform", "tone", "brand", "length"],
        system_prompt="SYS-PROMPT",
    )
    db = _DB(results=[team, tpl])  # team lookup → template lookup
    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    monkeypatch.setattr("app.services.chroma_client.query_similar", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.chroma_client.add_content", AsyncMock())
    import app.services.brand_compliance as bc

    monkeypatch.setattr(bc, "load_brand_context", AsyncMock(return_value=(None, None, "")))
    import app.services.pillars as pl

    pillar = SimpleNamespace(id=uuid.uuid4(), name="Prove", description="show results")
    monkeypatch.setattr(pl, "resolve_pillar", AsyncMock(return_value=pillar))
    import app.services.web_search as ws

    monkeypatch.setattr(ws, "web_search", AsyncMock(return_value=[{"title": "t", "url": "u"}]))
    monkeypatch.setattr(ws, "format_search_context", lambda s, q: "SEARCHCTX")
    import app.services.plain_english as pe

    monkeypatch.setattr(pe, "rewrite_plain_english", AsyncMock(side_effect=lambda c, **kw: c))
    infer = AsyncMock(return_value={"content": "c", "hashtags": [], "suggested_media": None})
    monkeypatch.setattr(ai, "call_inference", infer)
    import app.services.quality_pipeline as qp

    monkeypatch.setattr(
        qp, "apply_quality_pipeline", AsyncMock(return_value=SimpleNamespace(content="c", hashtags=[], seo_score=None, nlp_report=None, to_dict=lambda: {}))
    )

    req = GenerateContentRequest(prompt="monitoring", platform="threads", template_id=uuid.uuid4(), pillar="Prove", web_search=True)
    out = await generate_content(req, uuid.uuid4(), db=db, current_user=_user())
    sent = infer.await_args.args[0]
    assert sent.startswith("SYS-PROMPT")
    assert "About monitoring for threads" in sent
    assert "CONTENT PILLAR (3P system): Prove" in sent
    assert "SEARCHCTX" in sent
    assert out.pillar_name == "Prove" and out.pillar_id == str(pillar.id)
    assert out.web_sources == [{"title": "t", "url": "u"}]


@pytest.mark.asyncio
async def test_generate_content_no_team(monkeypatch):
    db = _DB(results=[None])  # no team → quota/chroma/brand skipped
    import app.services.plain_english as pe

    monkeypatch.setattr(pe, "rewrite_plain_english", AsyncMock(side_effect=lambda c, **kw: c))
    monkeypatch.setattr(ai, "call_inference", AsyncMock(return_value={"content": "c", "hashtags": ["#x"], "suggested_media": None}))
    import app.services.quality_pipeline as qp

    monkeypatch.setattr(
        qp, "apply_quality_pipeline", AsyncMock(return_value=SimpleNamespace(content="c", hashtags=["#x"], seo_score=None, nlp_report=None, to_dict=lambda: {}))
    )
    req = GenerateContentRequest(prompt="hi", platform="twitter")
    out = await generate_content(req, uuid.uuid4(), db=db, current_user=_user())
    assert out.content == "c" and out.pillar_id is None


@pytest.mark.asyncio
async def test_generate_workflow_category_lookup(monkeypatch):
    intent = {
        "intent": "carousel",
        "platforms": ["linkedin"],
        "needs_image": True,
        "needs_scheduling": True,
        "schedule_hint": "tue 9am",
        "data_sources": ["github"],
        "complexity": "medium",
    }
    monkeypatch.setattr(ai, "call_inference", AsyncMock(return_value=intent))
    tpl = SimpleNamespace(id=uuid.uuid4(), prompt_template="do {{thing}} now", n8n_workflow_json={"nodes": [], "name": "tpl"})
    db = _DB(results=[tpl], team=_team())  # db.get(team) via ctor; template via execute
    req = GenerateWorkflowRequest(prompt="make a carousel bot")
    out = await generate_workflow(req, uuid.uuid4(), db=db, current_user=_user())
    assert out.template_id == tpl.id
    assert out.n8n_workflow_json["name"] == "AI Generated: carousel"
    assert out.variables_used == {"thing": "<thing>"}
    assert any(getattr(o, "prompt_text", None) == "make a carousel bot" for o in db.added)


@pytest.mark.asyncio
async def test_generate_workflow_builtin_nodes(monkeypatch):
    intent = {
        "intent": "announcement",
        "platforms": ["twitter"],
        "needs_image": False,
        "needs_scheduling": False,
        "schedule_hint": None,
        "data_sources": ["rss"],
        "complexity": "simple",
    }
    monkeypatch.setattr(ai, "call_inference", AsyncMock(return_value=intent))
    db = _DB(results=[None], team=_team())  # no template → built from scratch
    req = GenerateWorkflowRequest(prompt="announce on X")
    out = await generate_workflow(req, uuid.uuid4(), db=db, current_user=_user())
    assert out.template_id is None
    names = [n["name"] for n in out.n8n_workflow_json["nodes"]]
    assert "Start" in names


@pytest.mark.asyncio
async def test_save_generation_template(monkeypatch):
    team = _team()
    db = _DB(team=team)
    tmpl = SimpleNamespace(id=uuid.uuid4(), name="tpl", category="gen", tags=["t"], created_at=datetime_now())
    monkeypatch.setattr(ai, "_save_generation_template", AsyncMock(return_value=tmpl))
    req = SaveGenerationTemplateRequest(name="tpl", prompt_template="p")
    out = await save_generation_template(req, uuid.uuid4(), db=db, current_user=_user())
    assert out.name == "tpl" and out.id == tmpl.id


@pytest.mark.asyncio
async def test_save_generation_template_no_team():
    with pytest.raises(HTTPException) as e:
        await save_generation_template(SaveGenerationTemplateRequest(name="x", prompt_template="p"), uuid.uuid4(), db=_DB(team=None), current_user=_user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_emoji_local_provider(monkeypatch):
    team = _team()
    db = _DB(team=team)
    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    import app.services.inference as inf

    monkeypatch.setattr(inf, "_call_local_diffusers_txt2img", AsyncMock(return_value={"image_base64": "AAA="}))
    monkeypatch.setattr(ai, "_remove_background_white", AsyncMock(return_value="BBB="))
    import app.services.dmr as dmr

    monkeypatch.setattr(dmr, "call_dmr_chat", AsyncMock(return_value={"text": "a happy cloud mascot, flat vector"}))
    monkeypatch.setattr(dmr, "call_dmr_vision", AsyncMock(return_value=None))
    req = EmojiGenerateRequest(concept="happy cloud", enhance_prompt=True)
    out = await generate_emoji(_REQ, req, uuid.uuid4(), db=db, current_user=_user())
    assert out.image_base64 == "BBB="
    assert "a happy cloud mascot" in out.enhanced_prompt
    assert out.provider == "local-diffusers"


@pytest.mark.asyncio
async def test_generate_emoji_all_providers_fail(monkeypatch):
    db = _DB(team=None)
    import app.services.inference as inf

    monkeypatch.setattr(inf, "_call_local_diffusers_txt2img", AsyncMock(side_effect=HTTPException(status_code=500, detail="down")))
    monkeypatch.setattr(inf, "_get_provider_config", AsyncMock(return_value=(None, "m", "key")))
    monkeypatch.setattr(inf, "_call_workers_ai_image", AsyncMock(side_effect=HTTPException(status_code=500, detail="down too")))
    req = EmojiGenerateRequest(concept="x", enhance_prompt=False, background="white", remove_bg=False)
    with pytest.raises(HTTPException) as e:
        await generate_emoji(_REQ, req, uuid.uuid4(), db=db, current_user=_user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_generate_emoji_concept_required():
    req = EmojiGenerateRequest(concept="   ")
    with pytest.raises(HTTPException) as e:
        await generate_emoji(_REQ, req, uuid.uuid4(), db=_DB(team=None), current_user=_user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_list_emoji_styles_endpoint():
    out = await list_emoji_styles(_REQ, current_user=_user())
    assert "flat" in out["styles"]
    assert 512 in out["sizes"] and "transparent" in out["backgrounds"]


# ── generate_image orchestration ────────────────────────────────────


def _img_patches(monkeypatch, *, diffusers=None, cf=None, provider_cfg=None, is_image_model=True, infographic=False):
    """Patch every seam generate_image touches."""
    import app.services.inference as inf
    import app.services.infographic_renderer as igr

    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    monkeypatch.setattr(ai.chroma_client, "query_similar", AsyncMock(return_value=[]))
    monkeypatch.setattr(ai.chroma_client, "add_content", AsyncMock())
    monkeypatch.setattr(
        ai,
        "persist_generated_image",
        AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), storage_path="gen/x.png", generation_prompt="p", ai_caption="", alt_text="", tags=[])),
    )
    monkeypatch.setattr(ai, "_save_generation_template", AsyncMock())

    monkeypatch.setattr(inf, "_call_local_diffusers_txt2img", AsyncMock(return_value=diffusers))
    monkeypatch.setattr(inf, "_call_workers_ai_image", AsyncMock(return_value=cf))
    monkeypatch.setattr(inf, "_get_provider_config", AsyncMock(return_value=provider_cfg or ("http://cf", "m", "key")))
    monkeypatch.setattr(inf, "_is_workers_ai_image_model", lambda m: is_image_model)

    monkeypatch.setattr(igr, "is_infographic_request", lambda p: infographic)
    monkeypatch.setattr(igr, "generate_infographic_content", AsyncMock(return_value={"title": "t", "bullets": ["b"]}))
    monkeypatch.setattr(igr, "sanitize_prompt_for_background", lambda p: f"bg:{p}")
    monkeypatch.setattr(igr, "render_infographic", lambda *a, **kw: b"overlaid")

    import app.services.media_quality as mq

    monkeypatch.setattr(mq, "apply_media_quality", AsyncMock(return_value=SimpleNamespace(to_dict=lambda: {"ok": 1})))
    monkeypatch.setattr(mq, "persist_media_quality_metadata", AsyncMock())


def _img_req(**kw):
    return ai.GenerateImageRequest(prompt="a cloud", **kw)


@pytest.mark.asyncio
async def test_generate_image_diffusers_happy(monkeypatch):
    _img_patches(monkeypatch, diffusers={"image_base64": "aW1n"})
    team = _team()
    out = await ai.generate_image(_img_req(), _team_id(), current_user=_user(), db=_DB(team=team))
    assert out.image_base64 == "aW1n"
    assert out.asset_id is not None
    assert out.quality == {"ok": 1}
    ai.check_quota.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_image_cf_fallback(monkeypatch):
    _img_patches(monkeypatch, diffusers=None, cf={"image_base64": "Y2Y="})
    out = await ai.generate_image(_img_req(), _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert out.image_base64 == "Y2Y="


@pytest.mark.asyncio
async def test_generate_image_both_fail_502(monkeypatch):
    _img_patches(monkeypatch, diffusers=None, cf=None)
    with pytest.raises(HTTPException) as e:
        await ai.generate_image(_img_req(), _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_generate_image_cf_non_image_model_400(monkeypatch):
    _img_patches(monkeypatch, diffusers=None, is_image_model=False)
    with pytest.raises(HTTPException) as e:
        await ai.generate_image(_img_req(), _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert e.value.status_code == 400
    assert "text-to-image model" in e.value.detail


@pytest.mark.asyncio
async def test_generate_image_nvidia_provider(monkeypatch):
    _img_patches(monkeypatch, diffusers=None)
    monkeypatch.setattr(ai, "_call_nvidia_flux_dev", AsyncMock(return_value=b"nvbytes"))
    out = await ai.generate_image(_img_req(provider="nvidia-flux-dev"), _team_id(), current_user=_user(), db=_DB(team=_team()))
    import base64 as b64

    assert out.image_base64 == b64.b64encode(b"nvbytes").decode()


@pytest.mark.asyncio
async def test_generate_image_nvidia_no_key_400(monkeypatch):
    _img_patches(monkeypatch, diffusers=None, provider_cfg=("", "", None))
    with pytest.raises(HTTPException) as e:
        await ai.generate_image(_img_req(provider="nvidia-flux-dev"), _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_image_infographic_overlay(monkeypatch):
    _img_patches(monkeypatch, diffusers={"image_base64": "YmFja2dyb3VuZA=="}, infographic=True)
    req = _img_req()
    out = await ai.generate_image(req, _team_id(), current_user=_user(), db=_DB(team=_team()))
    # overlay replaced the raw background
    assert out.image_base64 != "YmFja2dyb3VuZA=="


@pytest.mark.asyncio
async def test_generate_image_no_team_skips_quota(monkeypatch):
    _img_patches(monkeypatch, diffusers={"image_base64": "eA=="})
    monkeypatch.setattr(ai, "persist_generated_image", AsyncMock(return_value=None))
    out = await ai.generate_image(_img_req(), _team_id(), current_user=_user(), db=_DB(team=None))
    ai.check_quota.assert_not_awaited()
    assert out.asset_id is None


# ── generate_image_pipeline + blog article ──────────────────────────


@pytest.mark.asyncio
async def test_generate_image_pipeline_missing_keys(monkeypatch):
    import app.services.inference as inf

    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    monkeypatch.setattr(ai.chroma_client, "query_similar", AsyncMock(return_value=[]))
    monkeypatch.setattr(inf, "_get_provider_config", AsyncMock(return_value=("", "", None)))
    req = SimpleNamespace(
        prompt="p", negative_prompt="", enhance_prompt=True, cfg_scale=3.5, seed=0, steps=4, width=1024, height=1024, enhance_cfg_scale=3.5, enhance_steps=4
    )
    with pytest.raises(HTTPException) as e:
        await ai.generate_image_pipeline(req, _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_image_pipeline_happy(monkeypatch):
    import app.services.inference as inf

    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    monkeypatch.setattr(ai.chroma_client, "query_similar", AsyncMock(return_value=[]))
    monkeypatch.setattr(ai.chroma_client, "add_content", AsyncMock())
    monkeypatch.setattr(inf, "_get_provider_config", AsyncMock(return_value=("http://nv", "m", "key")))
    monkeypatch.setattr(ai, "_call_nvidia_flux_pipeline", AsyncMock(return_value=b"px"))
    monkeypatch.setattr(
        ai,
        "persist_generated_image",
        AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), storage_path="g.png", generation_prompt="p", ai_caption="", alt_text="", tags=[])),
    )
    import app.services.media_quality as mq

    monkeypatch.setattr(mq, "apply_media_quality", AsyncMock(return_value=SimpleNamespace(to_dict=lambda: {"q": 1})))
    monkeypatch.setattr(mq, "persist_media_quality_metadata", AsyncMock())
    req = SimpleNamespace(
        prompt="p", negative_prompt="", enhance_prompt=True, cfg_scale=3.5, seed=0, steps=4, width=1024, height=1024, enhance_cfg_scale=3.5, enhance_steps=4
    )
    out = await ai.generate_image_pipeline(req, _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert out.format == "base64" and out.image_base64


@pytest.mark.asyncio
async def test_generate_blog_article_existing_slug(monkeypatch):
    import app.services.blog_articles as ba

    monkeypatch.setattr(ba, "slugify", lambda s: "my-slug")
    monkeypatch.setattr(
        ba, "get_published_article", AsyncMock(return_value={"title": "Old", "excerpt": "e", "category": "Cloud", "readTime": "5m", "socialPost": "sp"})
    )
    body = SimpleNamespace(slug="x", topic="t", publish=False, extra_context="")
    out = await ai.generate_blog_article(_REQ, body, _team_id(), current_user=_user(), db=_DB())
    assert not out.created and out.title == "Old"


@pytest.mark.asyncio
async def test_generate_blog_article_happy(monkeypatch):
    import app.services.blog_articles as ba

    monkeypatch.setattr(ba, "slugify", lambda s: "")
    monkeypatch.setattr(ba, "default_slug", lambda t: "2026-10-10-t")
    monkeypatch.setattr(ba, "get_published_article", AsyncMock(return_value=None))
    monkeypatch.setattr(ba, "build_article_prompt", lambda *a: "prompt")
    monkeypatch.setattr(ba, "ARTICLE_SCHEMA", {"type": "object"})
    monkeypatch.setattr(ba, "assemble_article", lambda slug, gen, topic: {"slug": slug, "title": "T", "excerpt": "e", "category": "Cloud", "readTime": "4m"})
    monkeypatch.setattr(ba, "publish_article", AsyncMock())
    monkeypatch.setattr(ba, "clean_social_post", lambda s: "clean")
    monkeypatch.setattr(ai, "call_inference", AsyncMock(return_value={"json": {"title": "T", "socialPost": "s"}}))
    body = SimpleNamespace(slug=None, topic="trends", publish=True, extra_context="")
    out = await ai.generate_blog_article(_REQ, body, _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert out.created and out.slug == "2026-10-10-t"
    ba.publish_article.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_blog_article_no_usable_json(monkeypatch):
    import app.services.blog_articles as ba

    monkeypatch.setattr(ba, "slugify", lambda s: "s")
    monkeypatch.setattr(ba, "get_published_article", AsyncMock(return_value=None))
    monkeypatch.setattr(ba, "build_article_prompt", lambda *a: "p")
    monkeypatch.setattr(ba, "ARTICLE_SCHEMA", {})
    monkeypatch.setattr(ai, "call_inference", AsyncMock(return_value={"text": "no json"}))
    body = SimpleNamespace(slug="s", topic="t", publish=False, extra_context="")
    with pytest.raises(HTTPException) as e:
        await ai.generate_blog_article(_REQ, body, _team_id(), current_user=_user(), db=_DB(team=None))
    assert e.value.status_code == 502


# ── generate_carousel / pipeline / run-and-publish ───────────────────


def _carousel_patches(monkeypatch, *, similar=None, inference=None):
    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    monkeypatch.setattr(ai.chroma_client, "query_similar", AsyncMock(return_value=similar or []))
    monkeypatch.setattr(ai.chroma_client, "add_content", AsyncMock())
    monkeypatch.setattr(
        ai,
        "call_inference",
        AsyncMock(
            return_value=inference
            or {
                "slides": [
                    {"title": "T1", "body": "b1", "slide_type": "cover"},
                    {"title": "T2", "body": "b2", "slide_type": "content"},
                ],
                "suggested_caption": "cap",
                "hashtags": ["cloud", "devops"],
            }
        ),
    )
    import app.services.brand_compliance as bc

    monkeypatch.setattr(bc, "load_brand_context", AsyncMock(return_value=(None, None, "brand-ctx")))
    import app.services.plain_english as pe

    monkeypatch.setattr(
        pe,
        "run_nlp_check_and_fix",
        AsyncMock(return_value=([{"title": "T1", "body": "b1", "slide_type": "cover"}], "clean cap", SimpleNamespace(to_dict=lambda: {"nlp": 1}))),
    )
    import app.services.spellcheck as sp

    monkeypatch.setattr(sp, "auto_correct", AsyncMock(side_effect=lambda s: s))
    import app.services.seo as seomod

    monkeypatch.setattr(seomod, "analyze_seo", AsyncMock(return_value={"score": {"total": 88}}))


def _carousel_req(**kw):
    return ai.GenerateCarouselRequest(topic="cloud ops", **kw)


@pytest.mark.asyncio
async def test_generate_carousel_happy(monkeypatch):
    _carousel_patches(monkeypatch)
    out = await ai.generate_carousel(_carousel_req(), _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert len(out.slides) == 1 and out.slides[0].title == "T1"
    assert out.suggested_caption == "clean cap"
    assert out.seo_score == {"total": 88}
    assert out.nlp_report == {"nlp": 1}


@pytest.mark.asyncio
async def test_generate_carousel_similar_injects_note(monkeypatch):
    _carousel_patches(monkeypatch, similar=["old-carousel"])
    req = _carousel_req()
    await ai.generate_carousel(req, _team_id(), current_user=_user(), db=_DB(team=_team()))
    call = ai.call_inference.call_args
    assert "avoid repeating" in call.args[0]


@pytest.mark.asyncio
async def test_generate_carousel_inference_timeout_504(monkeypatch):
    _carousel_patches(monkeypatch)
    import httpx as _h

    monkeypatch.setattr(ai, "call_inference", AsyncMock(side_effect=_h.ReadTimeout("slow")))
    with pytest.raises(HTTPException) as e:
        await ai.generate_carousel(_carousel_req(), _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert e.value.status_code == 504


@pytest.mark.asyncio
async def test_generate_carousel_inference_error_500(monkeypatch):
    _carousel_patches(monkeypatch)
    monkeypatch.setattr(ai, "call_inference", AsyncMock(side_effect=RuntimeError("boom")))
    with pytest.raises(HTTPException) as e:
        await ai.generate_carousel(_carousel_req(), _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert e.value.status_code == 500


@pytest.mark.asyncio
async def test_generate_carousel_pipeline_happy(monkeypatch):
    # Inner generate_carousel is patched wholesale — exercise the
    # per-slide image + compose + persist orchestration.
    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    monkeypatch.setattr(ai.chroma_client, "add_content", AsyncMock())
    copy = SimpleNamespace(slides=[SimpleNamespace(slide_type="cover", title="T", body="b", highlight=None)], suggested_caption="cap", hashtags=["a"])
    monkeypatch.setattr(ai, "generate_carousel", AsyncMock(return_value=copy))

    import app.services.plain_english as pe

    monkeypatch.setattr(
        pe, "run_nlp_check_and_fix", AsyncMock(return_value=([{"slide_type": "cover", "title": "T", "body": "b"}], "cap", SimpleNamespace(to_dict=lambda: {})))
    )

    monkeypatch.setattr(ai, "_call_cf_image_pipeline", AsyncMock(side_effect=RuntimeError("bg down")))


    from PIL import Image as _Img

    import app.services.carousel_pipeline as cp

    monkeypatch.setattr(cp, "compose_branded_slide", lambda *a, **kw: _Img.new("RGB", (64, 64)))
    monkeypatch.setattr(ai, "persist_generated_image", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), storage_path="c.png")))

    import app.services.spellcheck as sp

    monkeypatch.setattr(sp, "auto_correct", AsyncMock(side_effect=lambda s: s))
    import app.services.seo as seomod

    monkeypatch.setattr(seomod, "analyze_seo", AsyncMock(return_value={"score": {"total": 90}}))
    monkeypatch.setattr(ai, "_save_generation_template", AsyncMock())

    req = ai.GenerateCarouselPipelineRequest(topic="cloud ops")
    out = await ai.generate_carousel_pipeline(req, _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert len(out.slides) == 1 and out.media_ids
    assert out.seo_score == {"total": 90}


@pytest.mark.asyncio
async def test_generate_carousel_pipeline_no_team_400(monkeypatch):
    req = ai.GenerateCarouselPipelineRequest(topic="t")
    with pytest.raises(HTTPException) as e:
        await ai.generate_carousel_pipeline(req, _team_id(), current_user=_user(), db=_DB(team=None))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_run_carousel_and_publish(monkeypatch):
    import app.services.carousel_pipeline as cp

    monkeypatch.setattr(cp, "run_cloudless_carousel_pipeline", AsyncMock(return_value={"post_id": "p1", "published": True}))
    monkeypatch.setattr(ai, "_save_generation_template", AsyncMock())
    req = SimpleNamespace(
        topic="ops",
        num_slides=5,
        tone="pro",
        include_cta=True,
        text_model="m",
        text_provider="dmr",
        txt2img_model="f",
        target_account_id=None,
        publish=True,
        wait_for_publish=False,
        custom_slides=None,
        custom_caption=None,
        custom_hashtags=None,
    )
    out = await ai.run_carousel_and_publish(req, _team_id(), current_user=_user(), db=_DB(team=_team()))
    assert out["published"] is True
    ai._save_generation_template.assert_awaited_once()

    with pytest.raises(HTTPException) as e:
        await ai.run_carousel_and_publish(req, _team_id(), current_user=_user(), db=_DB(team=None))
    assert e.value.status_code == 400
