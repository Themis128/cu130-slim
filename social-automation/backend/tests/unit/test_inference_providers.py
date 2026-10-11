"""Unit tests for app/services/inference.py — provider callables and helpers.

Complements test_inference_fallback.py (chain/circuit coverage) with the
per-provider HTTP call functions, pure helpers (JSON extraction, local-
diffusers param guard, model validation), provider config resolution,
and the Workers AI batch/STT/model-list surfaces.
"""

from __future__ import annotations

import base64
import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

import app.services.inference as I


def _png_b64(size=(32, 32)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _Resp:
    def __init__(self, status=200, json_body=None, content=b"", headers=None, text=""):
        self.status_code = status
        self._json = json_body
        self.content = content
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeHTTP:
    """Patches httpx.AsyncClient; serves queued post/get responses."""

    def __init__(self, posts=None, gets=None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls = []
        self.get_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.post_calls.append({"url": url, **kw})
        item = self.posts.pop(0) if self.posts else _Resp(500, text="empty")
        if isinstance(item, Exception):
            raise item
        return item

    async def get(self, url, **kw):
        self.get_calls.append({"url": url, **kw})
        item = self.gets.pop(0) if self.gets else _Resp(500, text="empty")
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def _cf(monkeypatch):
    monkeypatch.setattr(I.settings, "CLOUDFLARE_ACCOUNT_ID", "acct-1", raising=False)
    monkeypatch.setattr(I.settings, "CLOUDFLARE_AI_API_TOKEN", "tok-ai", raising=False)
    monkeypatch.setattr(I.settings, "CLOUDFLARE_API_TOKEN", "", raising=False)


def _http(monkeypatch, posts=None, gets=None):
    fake = _FakeHTTP(posts, gets)
    monkeypatch.setattr(I.httpx, "AsyncClient", lambda **kw: fake)
    return fake


# ── pure helpers ─────────────────────────────────────────────────────


def test_extract_cf_neurons():
    assert I._extract_cf_neurons(httpx.Headers({"cf-ai-neurons-spent": "12.9"})) == 12
    assert I._extract_cf_neurons(httpx.Headers({"x-cf-ai-neurons": "7"})) == 7
    assert I._extract_cf_neurons(httpx.Headers({"cf-ai-neurons-spent": "abc"})) is None
    assert I._extract_cf_neurons(httpx.Headers({})) is None


def test_estimate_cost_free_tier():
    assert I._estimate_cost("cloudflare", "m", "p", 100) == 0.0


def test_ai_token_precedence(monkeypatch):
    monkeypatch.setattr(I.settings, "CLOUDFLARE_AI_API_TOKEN", "ai-tok", raising=False)
    monkeypatch.setattr(I.settings, "CLOUDFLARE_API_TOKEN", "cf-tok", raising=False)
    assert I._ai_token() == "ai-tok"
    monkeypatch.setattr(I.settings, "CLOUDFLARE_AI_API_TOKEN", " ", raising=False)
    assert I._ai_token() == "cf-tok"


def test_validate_workers_ai_model():
    assert I._validate_workers_ai_model("@cf/meta/llama-3.2-3b") == "@cf/meta/llama-3.2-3b"
    for bad in ("", None, "no-at-sign", "@cf/../escape", "@cf/a b"):
        with pytest.raises(HTTPException) as ei:
            I._validate_workers_ai_model(bad)
        assert ei.value.status_code == 400


def test_is_workers_ai_image_model():
    assert I._is_workers_ai_image_model("@cf/black-forest-labs/flux-1-schnell")
    assert I._is_workers_ai_image_model("@cf/stabilityai/sdxl")
    assert not I._is_workers_ai_image_model("@cf/meta/llama-3.2-3b")


def test_logsafe_and_family():
    assert I._logsafe("a\nb\x00c") == "a_b_c"
    assert I._local_diffusers_family("stabilityai/stable-diffusion-xl") == "sdxl"
    assert I._local_diffusers_family("runwayml/stable-diffusion-v1-5") == "sd15"
    assert I._local_diffusers_family("flux.1-schnell") == "other"


def test_native_generation_size():
    # sd15: native ~512 area, aspect preserved, 64-rounded
    w, h = I._native_generation_size(1024, 512, "sd15")
    assert w <= 768 and h <= 768 and w % 64 == 0 and h % 64 == 0
    assert abs((w / h) - 2.0) < 0.3
    # sdxl: ~1024 area
    w, h = I._native_generation_size(2048, 2048, "sdxl")
    assert w == h and 512 <= w <= 1536 and w % 64 == 0
    # passthrough for unknown family / bad dims
    assert I._native_generation_size(640, 480, "other") == (640, 480)
    assert I._native_generation_size(0, 480, "sd15") == (0, 480)
    # never render larger than requested
    w, h = I._native_generation_size(128, 128, "sd15")
    assert w <= 128 and h <= 128


def test_normalize_local_diffusers_params():
    out = I._normalize_local_diffusers_params(model="stable-diffusion-v1-5", width=1024, height=1024, steps=4, cfg_scale=3.5, negative_prompt="")
    assert out["family"] == "sd15"
    assert out["steps"] == I._SD_DEFAULT_STEPS  # floored up from 4
    assert out["cfg_scale"] == I._SD_DEFAULT_CFG
    assert "blurry" in out["negative_prompt"]  # baseline merged in

    # explicit good params pass through; existing negative deduped
    out = I._normalize_local_diffusers_params(model="stable-diffusion-v1-5", width=512, height=512, steps=30, cfg_scale=8.0, negative_prompt="blurry, extra")
    assert out["steps"] == 30 and out["cfg_scale"] == 8.0
    assert out["negative_prompt"].lower().count("blurry") == 1
    assert "extra" in out["negative_prompt"]

    # non-SD models untouched
    out = I._normalize_local_diffusers_params(model="flux.1-schnell", width=1024, height=1024, steps=4, cfg_scale=3.5, negative_prompt="")
    assert out["family"] == "other" and out["steps"] == 4


def test_resize_b64_png():
    big = _png_b64((64, 64))
    out = I._resize_b64_png(big, 16, 16)
    from PIL import Image

    assert Image.open(io.BytesIO(base64.b64decode(out))).size == (16, 16)
    # undecodable → original back
    assert I._resize_b64_png("not-b64!!!", 8, 8) == "not-b64!!!"
    # same size → passthrough
    same = _png_b64((16, 16))
    assert I._resize_b64_png(same, 16, 16) == same


def test_is_cf_quota_error():
    assert I._is_cf_quota_error(HTTPException(429))
    assert I._is_cf_quota_error(HTTPException(503))
    assert I._is_cf_quota_error(HTTPException(403, "Neuron budget exceeded"))
    assert I._is_cf_quota_error(HTTPException(502, "rate limit"))
    assert not I._is_cf_quota_error(HTTPException(400, "bad"))
    assert not I._is_cf_quota_error(HTTPException(403, "forbidden token"))


def test_extract_json_object():
    assert I._extract_json_object('pre {"a": {"b": 1}} post') == '{"a": {"b": 1}}'
    assert I._extract_json_object('{"s": "has } brace"}') == '{"s": "has } brace"}'
    assert I._extract_json_object('{"s": "esc\\"}"}') == '{"s": "esc\\"}"}'
    assert I._extract_json_object("no braces") is None
    assert I._extract_json_object("{unbalanced") is None


def test_parse_json_response():
    assert I._parse_json_response('{"a": 1}') == {"a": 1}
    assert I._parse_json_response('```json\n{"a": 2}\n```') == {"a": 2}
    assert I._parse_json_response('Here is JSON: {"a": 3} done') == {"a": 3}
    assert I._parse_json_response('[1,2] {"a": 4}') == {"a": 4}
    with pytest.raises(HTTPException) as ei:
        I._parse_json_response("totally not json")
    assert ei.value.status_code == 500


# ── provider config ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_provider_config_static(monkeypatch):
    monkeypatch.setattr(I.settings, "DMR_URL", "http://dmr/v1", raising=False)
    monkeypatch.setattr(I.settings, "DMR_TEXT_MODEL", "ai/m", raising=False)
    url, model, key = await I._get_provider_config("dmr", None, None)
    assert url == "http://dmr/v1" and model == "ai/m" and key is None

    monkeypatch.setattr(I.settings, "DMR_VLLM_URL", "http://vllm/v1", raising=False)
    url, model, _ = await I._get_provider_config("dmr-vllm", None, None)
    assert url == "http://vllm/v1" and "smollm2" in model

    monkeypatch.setattr(I.settings, "LOCAL_DIFFUSERS_URL", "http://sd/v1", raising=False)
    monkeypatch.setattr(I.settings, "LOCAL_DIFFUSERS_MODEL", "sd15", raising=False)
    url, model, _ = await I._get_provider_config("local-diffusers", None, None)
    assert url == "http://sd/v1" and model == "sd15"

    with pytest.raises(HTTPException) as ei:
        await I._get_provider_config("nonsense", None, None)
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_get_provider_config_catalog_and_db(monkeypatch):
    monkeypatch.setattr(I.settings, "GROQ_API_KEY", "groq-key", raising=False)
    url, model, key = await I._get_provider_config("groq", None, None)
    assert key == "groq-key" and "groq" in url

    # DB record with a stored key wins over env
    rec = SimpleNamespace(api_key_enc=b"enc", base_url="https://db.example", default_model="db-model")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: rec)))
    monkeypatch.setattr(I, "decrypt_token", lambda t: "db-key")
    url, model, key = await I._get_provider_config("groq", uuid.uuid4(), db)
    assert url == "https://db.example" and model == "db-model" and key == "db-key"

    # DB record without key → env key + DB base_url/model
    rec.api_key_enc = None
    url, model, key = await I._get_provider_config("groq", uuid.uuid4(), db)
    assert url == "https://db.example" and key == "groq-key"


@pytest.mark.asyncio
async def test_get_provider_config_cloudflare(monkeypatch, _cf):
    url, model, key = await I._get_provider_config("cloudflare", None, None)
    assert "acct-1" in url and key == "tok-ai"


# ── Workers AI surfaces ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_workers_ai_models(monkeypatch, _cf):
    pages = [
        _Resp(200, {"result": [{"name": "@cf/m1", "task": {"name": "Text Generation"}, "description": "d"}], "result_info": {"total_pages": 2}}),
        _Resp(200, {"result": [{"name": "@cf/m2", "task": "Image"}], "result_info": {"total_pages": 2}}),
    ]
    fake = _http(monkeypatch, gets=pages)
    models = await I.list_workers_ai_models()
    assert [m["id"] for m in models] == ["@cf/m1", "@cf/m2"]
    assert models[0]["task"] == "Text Generation"
    assert len(fake.get_calls) == 2

    # error → 502
    _http(monkeypatch, gets=[_Resp(500, text="boom")])
    with pytest.raises(HTTPException) as ei:
        await I.list_workers_ai_models()
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_list_workers_ai_models_no_creds(monkeypatch):
    monkeypatch.setattr(I.settings, "CLOUDFLARE_ACCOUNT_ID", "", raising=False)
    monkeypatch.setattr(I.settings, "CLOUDFLARE_AI_API_TOKEN", "", raising=False)
    monkeypatch.setattr(I.settings, "CLOUDFLARE_API_TOKEN", "", raising=False)
    with pytest.raises(HTTPException) as ei:
        await I.list_workers_ai_models()
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_transcribe_workers_ai(monkeypatch, _cf):
    # legacy envelope
    fake = _http(monkeypatch, posts=[_Resp(200, {"success": True, "result": {"text": " hello world ", "language": "en"}})])
    out = await I.transcribe_workers_ai(b"audio", "application/octet-stream")
    assert out["text"] == "hello world" and out["language"] == "en"
    # content-type normalized to audio/*
    assert fake.post_calls[0]["headers"]["Content-Type"] == "audio/wav"

    # new direct format + preserved audio content type
    fake = _http(monkeypatch, posts=[_Resp(200, {"text": "hi"})])
    out = await I.transcribe_workers_ai(b"audio", "audio/mp3")
    assert out["text"] == "hi"
    assert fake.post_calls[0]["headers"]["Content-Type"] == "audio/mp3"

    # error → 502
    _http(monkeypatch, posts=[_Resp(400, {"errors": [{"m": "bad input"}]})])
    with pytest.raises(HTTPException) as ei:
        await I.transcribe_workers_ai(b"audio", "audio/wav")
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_workers_ai_chat(monkeypatch, _cf):
    # direct-format response
    fake = _http(monkeypatch, posts=[_Resp(200, {"response": "answer", "usage": {}}, headers={"cf-ai-neurons-spent": "5"})])
    out = await I._call_workers_ai_chat("p", "@cf/meta/llama", "tok")
    assert out == {"text": "answer"}

    # schema → parsed JSON + default max_tokens bump
    fake = _http(monkeypatch, posts=[_Resp(200, {"result": {"response": '{"x": 1}'}})])
    out = await I._call_workers_ai_chat("p", "@cf/meta/llama", "tok", schema={"type": "object"})
    assert out == {"x": 1}
    assert fake.post_calls[0]["json"]["max_tokens"] == I.WORKERS_AI_DEFAULT_STRUCTURED_MAX_TOKENS

    # structured-output model returns dict directly
    _http(monkeypatch, posts=[_Resp(200, {"response": {"x": 2}})])
    out = await I._call_workers_ai_chat("p", "@cf/meta/llama", "tok", schema={"type": "object"})
    assert out == {"x": 2}

    # error → 502
    _http(monkeypatch, posts=[_Resp(500, text="down")])
    with pytest.raises(HTTPException) as ei:
        await I._call_workers_ai_chat("p", "@cf/meta/llama", "tok")
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_workers_ai_batch(monkeypatch, _cf):
    with pytest.raises(HTTPException):
        await I.submit_workers_ai_batch("@cf/m", [])

    fake = _http(monkeypatch, posts=[_Resp(202, {"request_id": "req-1", "status": "queued"})])
    out = await I.submit_workers_ai_batch("@cf/m", [{"prompt": "x"}])
    assert out["request_id"] == "req-1" and out["status"] == "queued"
    assert "queueRequest=true" in fake.post_calls[0]["url"]

    # legacy envelope on retrieve
    _http(monkeypatch, posts=[_Resp(200, {"success": True, "result": {"status": "completed", "responses": [{"r": 1}], "usage": {"n": 3}}})])
    out = await I.retrieve_workers_ai_batch("@cf/m", "req-1")
    assert out["status"] == "completed" and out["responses"] == [{"r": 1}]
    assert out["usage"] == {"n": 3}

    # success:false → 502
    _http(monkeypatch, posts=[_Resp(200, {"success": False, "errors": ["e"]})])
    with pytest.raises(HTTPException) as ei:
        await I.retrieve_workers_ai_batch("@cf/m", "req-1")
    assert ei.value.status_code == 502


# ── cloud text fallbacks ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hf_groq_openai_chat(monkeypatch):
    ok = _Resp(200, {"choices": [{"message": {"content": "hi"}}]})
    _http(monkeypatch, posts=[ok])
    out = await I._call_hf_chat("p", "m", "k")
    assert out == {"text": "hi"}

    _http(monkeypatch, posts=[_Resp(200, {"choices": [{"message": {"content": '{"a":1}'}}]})])
    out = await I._call_groq_chat("p", "m", "k", schema={"type": "object"})
    assert out == {"a": 1}

    # openai-compat: reasoning_content fallback + no temperature for gpt-oss
    fake = _http(monkeypatch, posts=[_Resp(200, {"choices": [{"message": {"content": None, "reasoning_content": "thought"}}]})])
    out = await I._call_openai_compat("p", "http://x/v1", "openai/gpt-oss-120b", "k")
    assert out == {"text": "thought"}
    assert "temperature" not in fake.post_calls[0]["json"]

    _http(monkeypatch, posts=[_Resp(503, text="overloaded")])
    with pytest.raises(HTTPException) as ei:
        await I._call_groq_chat("p", "m", "k")
    assert ei.value.status_code == 502


# ── image providers ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_diffusers_txt2img(monkeypatch):
    monkeypatch.setattr(I.settings, "LOCAL_DIFFUSERS_URL", "http://sd/v1", raising=False)
    big = _png_b64((64, 64))
    fake = _http(monkeypatch, posts=[_Resp(200, {"data": [{"b64_json": big}]})])
    out = await I._call_local_diffusers_txt2img("prompt", model="stable-diffusion-v1-5", width=1024, height=1024, steps=4, cfg_scale=3.5)
    assert out["image_base64"]
    payload = fake.post_calls[0]["json"]
    assert payload["response_format"] == "b64_json"
    assert payload["steps"] == I._SD_DEFAULT_STEPS  # normalized up
    assert payload["size"] != "1024x1024"  # rendered at native res
    # resized back to requested size
    from PIL import Image

    img = Image.open(io.BytesIO(base64.b64decode(out["image_base64"])))
    assert img.size == (1024, 1024)

    # non-200 → 502, transport error → 503
    _http(monkeypatch, posts=[_Resp(500, text="x")])
    with pytest.raises(HTTPException) as ei:
        await I._call_local_diffusers_txt2img("p", model="flux", width=64, height=64)
    assert ei.value.status_code == 502
    _http(monkeypatch, posts=[httpx.ConnectError("down")])
    with pytest.raises(HTTPException) as ei:
        await I._call_local_diffusers_txt2img("p", model="flux", width=64, height=64)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_hf_together_pixazo_images(monkeypatch):
    # HF txt2img: flux skips negative/guidance
    fake = _http(monkeypatch, posts=[_Resp(200, content=b"PNG")])
    out = await I._call_hf_txt2img("p", "black-forest-labs/FLUX.1-schnell", "k", negative_prompt="neg", steps=10)
    params = fake.post_calls[0]["json"]["parameters"]
    assert "negative_prompt" not in params
    assert out["provider"] == "huggingface"

    # HF img2img payload shape
    fake = _http(monkeypatch, posts=[_Resp(200, content=b"PNG2")])
    await I._call_hf_img2img("p", "b64img", "m", "k", strength=2.0)
    params = fake.post_calls[0]["json"]["parameters"]
    assert params["strength"] == 1.0  # clamped
    assert params["target_size"] == {"width": 512, "height": 512}

    # together
    fake = _http(monkeypatch, posts=[_Resp(200, {"data": [{"b64_json": "aGk="}]})])
    out = await I._call_together_txt2img("p", "flux-model", "k", steps=20)
    assert out["image_base64"] == "aGk="
    assert fake.post_calls[0]["json"]["steps"] == 8  # capped

    # pixazo: generate → fetch URL → b64
    fake = _http(monkeypatch, posts=[_Resp(200, {"output": "https://img.x/i.png"})], gets=[_Resp(200, content=b"IMG")])
    out = await I._call_pixazo_txt2img("p", "k", width=512, height=512)
    assert out["provider"] == "pixazo"
    assert base64.b64decode(out["image_base64"]) == b"IMG"
    assert fake.post_calls[0]["json"]["width"] == 512
    assert fake.get_calls[0]["url"] == "https://img.x/i.png"

    # pixazo with no URL → 502
    _http(monkeypatch, posts=[_Resp(200, {"nothing": 1})])
    with pytest.raises(HTTPException) as ei:
        await I._call_pixazo_txt2img("p", "k")
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_nvidia_flux_family(monkeypatch):
    img_b64 = base64.b64encode(b"PNG").decode()
    _http(monkeypatch, posts=[_Resp(200, {"image": img_b64})])
    out = await I._call_nvidia_flux("p", "http://nv/flux", "k")
    assert out == b"PNG"

    _http(monkeypatch, posts=[_Resp(200, {"image": img_b64})])
    out = await I._call_nvidia_flux_dev("p", "http://nv/dev", "k", negative_prompt="n")
    assert out == b"PNG"

    _http(monkeypatch, posts=[_Resp(200, {"artifacts": [{"base64": img_b64}]})])
    out = await I._call_local_sd35("p", "http://sd35/gen")
    assert out == b"PNG"

    # pipeline: dev gen → kontext enhance
    fake = _http(monkeypatch, posts=[_Resp(200, {"image": img_b64}), _Resp(200, {"image": img_b64})])
    out = await I._call_nvidia_flux_pipeline("p", "http://nv/dev", "k1", "http://nv/kontext", "k2")
    assert out == b"PNG"
    kontext_payload = fake.post_calls[1]["json"]
    assert kontext_payload["image"].startswith("data:image/png;base64,")
    assert kontext_payload["text_prompts"][0]["text"]

    # no image in response → 502
    _http(monkeypatch, posts=[_Resp(200, {})])
    with pytest.raises(HTTPException) as ei:
        await I._call_nvidia_flux("p", "http://nv/flux", "k")
    assert ei.value.status_code == 502

    # sd35 no artifacts → 502
    _http(monkeypatch, posts=[_Resp(200, {"artifacts": []})])
    with pytest.raises(HTTPException) as ei:
        await I._call_local_sd35("p", "http://sd35")
    assert ei.value.status_code == 502


# ── chains / misc ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_text_provider_chain(monkeypatch):
    monkeypatch.setattr(I.settings, "DMR_URL", "http://dmr", raising=False)
    monkeypatch.setattr(I.settings, "CLOUDFLARE_ACCOUNT_ID", "a", raising=False)
    monkeypatch.setattr(I.settings, "CLOUDFLARE_AI_API_TOKEN", "t", raising=False)
    # requested provider leads; dmr already credentialed stays second
    chain = await I._text_provider_chain("cloudflare", None, None)
    assert chain == ["cloudflare", "dmr"]

    # provider outside the chain leads; credentialed chain follows
    chain = await I._text_provider_chain("groq", None, None)
    assert chain == ["groq", "dmr", "cloudflare"]

    # DB-driven enabled set + custom fallbacks
    calls = iter(
        [
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["cloudflare"])),
            SimpleNamespace(scalar=lambda: "groq, cloudflare"),
        ]
    )
    db = SimpleNamespace(execute=AsyncMock(side_effect=lambda *a, **k: next(calls)))
    chain = await I._text_provider_chain("cloudflare", uuid.uuid4(), db)
    # groq dropped (no creds), cloudflare kept, dmr appended
    assert chain[0] == "cloudflare" and "dmr" in chain


@pytest.mark.asyncio
async def test_dmr_embedding_wrapper(monkeypatch):
    import app.services.dmr as DMR

    monkeypatch.setattr(DMR, "call_dmr_embedding", AsyncMock(return_value=[0.1, 0.2]))
    assert await I._call_dmr_embedding("t") == [0.1, 0.2]
    monkeypatch.setattr(DMR, "call_dmr_embedding", AsyncMock(side_effect=ConnectionError("down")))
    with pytest.raises(HTTPException) as ei:
        await I._call_dmr_embedding("t")
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_get_team_id_for_user(monkeypatch):
    import app.api.deps as DEPS

    monkeypatch.setattr(DEPS, "get_user_team", AsyncMock(return_value=SimpleNamespace(id="team-1")))
    assert await I.get_team_id_for_user(uuid.uuid4(), None) == "team-1"
    monkeypatch.setattr(DEPS, "get_user_team", AsyncMock(return_value=None))
    assert await I.get_team_id_for_user(uuid.uuid4(), None) is None


# ── Workers AI image calls ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_workers_ai_image_payloads(monkeypatch, _cf):
    # FLUX model: prompt+steps only, steps capped at 8
    fake = _http(monkeypatch, posts=[_Resp(200, {"image": "aGk="}, headers={"content-type": "application/json"})])
    out = await I._call_workers_ai_image("p", "@cf/black-forest-labs/flux-1-schnell", steps=20)
    payload = fake.post_calls[0]["json"]
    assert payload == {"prompt": "p", "steps": 8}
    assert out["image_base64"] == "aGk="

    # SDXL model: full payload incl. negative_prompt
    fake = _http(monkeypatch, posts=[_Resp(200, {"result": {"image": "eGk="}}, headers={"content-type": "application/json"})])
    out = await I._call_workers_ai_image(
        "p", "@cf/stabilityai/stable-diffusion-xl-base-1.0", negative_prompt="neg", width=768, height=512, steps=25, cfg_scale=6.0
    )
    payload = fake.post_calls[0]["json"]
    assert payload["width"] == 768 and payload["guidance"] == 6.0
    assert payload["negative_prompt"] == "neg"
    assert out["image_base64"] == "eGk="

    # raw binary image response
    _http(monkeypatch, posts=[_Resp(200, content=b"BYTES", headers={"content-type": "image/png"})])
    out = await I._call_workers_ai_image("p", "@cf/black-forest-labs/flux-x")
    assert base64.b64decode(out["image_base64"]) == b"BYTES"

    # no image data → 502; http error → 502
    _http(monkeypatch, posts=[_Resp(200, {"result": {}}, headers={"content-type": "application/json"})])
    with pytest.raises(HTTPException) as ei:
        await I._call_workers_ai_image("p", "@cf/x/flux")
    assert ei.value.status_code == 502
    _http(monkeypatch, posts=[_Resp(500, text="boom")])
    with pytest.raises(HTTPException):
        await I._call_workers_ai_image("p", "@cf/x/flux")


@pytest.mark.asyncio
async def test_workers_ai_img2img(monkeypatch, _cf):
    img_bytes = base64.b64decode(_png_b64((256, 256)))

    # happy JSON path — image downscaled to 512 for the model
    fake = _http(monkeypatch, posts=[_Resp(200, {"image": "eGk="})])
    out = await I._call_workers_ai_img2img("p", img_bytes)
    payload = fake.post_calls[0]["json"]
    assert payload["width"] == 512 and payload["image_b64"]
    assert out["image_base64"] == "eGk="

    # 429 → retries → exhausted; HF fallback kicks in when allowed
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(I.settings, "HUGGINGFACE_API_KEY", "hf-k", raising=False)
    hf_ok = _Resp(200, content=b"HFPNG")
    _http(monkeypatch, posts=[_Resp(429, text="quota"), _Resp(429, text="quota"), hf_ok])
    out = await I._call_workers_ai_img2img("p", img_bytes, max_retries=2, allow_fallback=True)
    assert out["provider"] == "huggingface"

    # fallback disabled → 502 capacity error
    _http(monkeypatch, posts=[_Resp(429, text="quota")])
    with pytest.raises(HTTPException) as ei:
        await I._call_workers_ai_img2img("p", img_bytes, max_retries=1, allow_fallback=False)
    assert ei.value.status_code == 502
    assert "capacity" in ei.value.detail

    # non-429 error → immediate 502
    _http(monkeypatch, posts=[_Resp(500, text="boom")])
    with pytest.raises(HTTPException):
        await I._call_workers_ai_img2img("p", img_bytes, max_retries=3)


@pytest.mark.asyncio
async def test_workers_ai_flux2_edit(monkeypatch, _cf):
    img_bytes = base64.b64decode(_png_b64((800, 800)))
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    # binary response happy path — multipart files, ref ≤512
    fake = _http(monkeypatch, posts=[_Resp(200, content=b"OUT", headers={"content-type": "image/png"})])
    out = await I._call_workers_ai_flux2_edit("p", img_bytes)
    files = fake.post_calls[0]["files"]
    assert files["input_image_0"][2] == "image/png"
    assert base64.b64decode(out["image_base64"]) == b"OUT"

    # 429s exhausted → 502 capacity
    _http(monkeypatch, posts=[_Resp(429, text="quota")] * 2)
    with pytest.raises(HTTPException) as ei:
        await I._call_workers_ai_flux2_edit("p", img_bytes, max_retries=2)
    assert "capacity" in ei.value.detail

    # json no-image → 502
    _http(monkeypatch, posts=[_Resp(200, {"result": {}})])
    with pytest.raises(HTTPException):
        await I._call_workers_ai_flux2_edit("p", img_bytes, max_retries=1)


# ── _do_call_inference dispatch ──────────────────────────────────────


@pytest.mark.asyncio
async def test_do_call_inference_dispatch(monkeypatch, _cf):
    # dmr route
    monkeypatch.setattr(I, "_call_dmr_chat", AsyncMock(return_value={"text": "dmr"}))
    out = await I._do_call_inference("p", "dmr")
    assert out == {"text": "dmr"}

    # dmr-vllm route
    import app.services.dmr as DMR

    monkeypatch.setattr(DMR, "call_dmr_vllm_chat", AsyncMock(return_value={"text": "vllm"}))
    out = await I._do_call_inference("p", "dmr-vllm")
    assert out == {"text": "vllm"}

    # image model + schema → 400 guard
    with pytest.raises(HTTPException) as ei:
        await I._do_call_inference("p", "cloudflare", schema={"type": "object"}, model_override="@cf/black-forest-labs/flux-1-schnell")
    assert ei.value.status_code == 400

    # cloudflare text path
    monkeypatch.setattr(I, "_call_workers_ai_chat", AsyncMock(return_value={"text": "cf"}))
    out = await I._do_call_inference("p", "cloudflare")
    assert out == {"text": "cf"}

    # non-CF provider → openai-compat route (groq needs env key)
    monkeypatch.setattr(I.settings, "GROQ_API_KEY", "gk", raising=False)
    monkeypatch.setattr(I, "_call_openai_compat", AsyncMock(return_value={"text": "groq"}))
    out = await I._do_call_inference("p", "groq")
    assert out == {"text": "groq"}

    # provider with no key anywhere → 400
    monkeypatch.setattr(I.settings, "MISTRAL_API_KEY", "", raising=False)
    with pytest.raises(HTTPException) as ei:
        await I._do_call_inference("p", "mistral")
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_do_call_inference_cf_quota_chains(monkeypatch, _cf):
    quota = HTTPException(429, "neurons exhausted")
    monkeypatch.setattr(I.settings, "PIXAZO_API_KEY", "", raising=False)
    monkeypatch.setattr(I.settings, "TOGETHER_API_KEY", "tk", raising=False)
    monkeypatch.setattr(I.settings, "HUGGINGFACE_API_KEY", "hk", raising=False)

    # image model quota → together (pixazo unset) → hf
    monkeypatch.setattr(I, "_call_workers_ai_image", AsyncMock(side_effect=quota))
    monkeypatch.setattr(I, "_call_together_txt2img", AsyncMock(side_effect=HTTPException(502, "down")))
    hf_img = AsyncMock(return_value={"image_base64": "hf"})
    monkeypatch.setattr(I, "_call_hf_txt2img", hf_img)
    out = await I._do_call_inference("p", "cloudflare", model_override="@cf/black-forest-labs/flux-1-schnell")
    assert out["image_base64"] == "hf"
    hf_img.assert_awaited_once()

    # non-quota error re-raises immediately
    monkeypatch.setattr(I, "_call_workers_ai_image", AsyncMock(side_effect=HTTPException(400, "bad model")))
    with pytest.raises(HTTPException) as ei:
        await I._do_call_inference("p", "cloudflare", model_override="@cf/black-forest-labs/flux-1-schnell")
    assert ei.value.status_code == 400

    # allow_fallback=False → quota re-raises
    monkeypatch.setattr(I, "_call_workers_ai_image", AsyncMock(side_effect=quota))
    with pytest.raises(HTTPException) as ei:
        await I._do_call_inference("p", "cloudflare", model_override="@cf/black-forest-labs/flux-1-schnell", allow_fallback=False)
    assert ei.value.status_code == 429

    # text quota → groq → hf
    monkeypatch.setattr(I.settings, "GROQ_API_KEY", "gk", raising=False)
    monkeypatch.setattr(I, "_call_workers_ai_chat", AsyncMock(side_effect=quota))
    monkeypatch.setattr(I, "_call_groq_chat", AsyncMock(side_effect=HTTPException(502, "groq down")))
    hf_txt = AsyncMock(return_value={"text": "hf"})
    monkeypatch.setattr(I, "_call_hf_chat", hf_txt)
    out = await I._do_call_inference("p", "cloudflare")
    assert out == {"text": "hf"}


# ── cf image pipeline ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cf_image_pipeline(monkeypatch):
    img_b64 = _png_b64((1024, 1024))

    # local diffusers succeeds → draft-only (no img2img model)
    monkeypatch.setattr(I, "_call_local_diffusers_txt2img", AsyncMock(return_value={"image_base64": img_b64}))
    out = await I._call_cf_image_pipeline("p")
    assert out["image_base64"] and out["draft_base64"]
    assert out["models"]["img2img"] == "draft-only"

    # local fails → CF txt2img fallback
    monkeypatch.setattr(I, "_call_local_diffusers_txt2img", AsyncMock(side_effect=HTTPException(503, "down")))
    cf_img = AsyncMock(return_value={"image_base64": img_b64})
    monkeypatch.setattr(I, "_call_workers_ai_image", cf_img)
    out = await I._call_cf_image_pipeline("p")
    cf_img.assert_awaited_once()

    # local + CF quota → 502 no-more-fallbacks
    monkeypatch.setattr(I, "_call_workers_ai_image", AsyncMock(side_effect=HTTPException(429, "quota")))
    with pytest.raises(HTTPException) as ei:
        await I._call_cf_image_pipeline("p")
    assert ei.value.status_code == 502
    assert "quota" in ei.value.detail

    # non-quota CF error re-raises
    monkeypatch.setattr(I, "_call_workers_ai_image", AsyncMock(side_effect=HTTPException(400, "bad")))
    with pytest.raises(HTTPException) as ei:
        await I._call_cf_image_pipeline("p")
    assert ei.value.status_code == 400

    # img2img enhance path — flux-2 model routed to flux2_edit
    monkeypatch.setattr(I, "_call_workers_ai_image", AsyncMock(return_value={"image_base64": img_b64}))
    flux2 = AsyncMock(return_value={"image_base64": img_b64})
    monkeypatch.setattr(I, "_call_workers_ai_flux2_edit", flux2)
    out = await I._call_cf_image_pipeline("p", img2img_model="@cf/black-forest-labs/flux-2-klein-4b")
    flux2.assert_awaited_once()

    # regular img2img model routed to img2img; its failure → draft-only
    i2i = AsyncMock(side_effect=HTTPException(502, "i2i down"))
    monkeypatch.setattr(I, "_call_workers_ai_img2img", i2i)
    out = await I._call_cf_image_pipeline("p", img2img_model="@cf/runwayml/stable-diffusion-v1-5-img2img")
    i2i.assert_awaited_once()
    assert out["image_base64"]
