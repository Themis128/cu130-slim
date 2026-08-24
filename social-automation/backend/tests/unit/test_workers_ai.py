"""Unit tests for Cloudflare Workers AI speech-to-text integration."""
import pytest
from fastapi import HTTPException

from app.services import inference


@pytest.fixture
def cf_settings(monkeypatch):
    """Point the module-level settings at a fake Cloudflare account."""
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_ACCOUNT_ID", "account-123")
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_API_TOKEN", "tok-456")
    yield


class _FakeResponse:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("response body is not JSON")

    @property
    def text(self):
        return str(self._body)


class _FakeAsyncClient:
    """Context-manager stand-in for httpx.AsyncClient capturing POSTs."""

    def __init__(self, status_code: int, body):
        self._resp = _FakeResponse(status_code, body)
        self.last_url = None
        self.last_headers = None
        self.last_content = None
        self.last_json = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, content=None, json=None):
        self.last_url = url
        self.last_headers = headers
        self.last_content = content
        self.last_json = json
        return self._resp


class _FakeGetClient:
    """Context-manager stand-in for httpx.AsyncClient capturing GETs."""

    def __init__(self, responses: dict[int, _FakeResponse]):
        self._responses = responses
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        page = int((params or {}).get("page", 1))
        return self._responses[page]


@pytest.mark.asyncio
async def test_transcribe_workers_ai_success(monkeypatch, cf_settings):
    fake = _FakeAsyncClient(200, {"result": {"text": "hello world", "language": "en"}})
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=120.0: fake)

    result = await inference.transcribe_workers_ai(
        b"RIFF....wavdata",
        "audio/wav",
        model="@cf/openai/whisper",
    )

    assert result["text"] == "hello world"
    assert result["language"] == "en"
    assert fake.last_url == "https://api.cloudflare.com/client/v4/accounts/account-123/ai/run/@cf/openai/whisper"
    assert fake.last_headers["Authorization"] == "Bearer tok-456"
    assert fake.last_headers["Content-Type"] == "audio/wav"
    assert fake.last_content == b"RIFF....wavdata"


@pytest.mark.asyncio
async def test_transcribe_workers_ai_requires_account_id(monkeypatch):
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_API_TOKEN", "tok-456")

    with pytest.raises(HTTPException) as exc_info:
        await inference.transcribe_workers_ai(b"audio", "audio/wav")
    assert exc_info.value.status_code == 400
    assert "CLOUDFLARE_ACCOUNT_ID" in exc_info.value.detail


@pytest.mark.asyncio
async def test_transcribe_workers_ai_requires_token(monkeypatch, cf_settings):
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_API_TOKEN", "")

    with pytest.raises(HTTPException) as exc_info:
        await inference.transcribe_workers_ai(b"audio", "audio/wav")
    assert exc_info.value.status_code == 400
    assert "CLOUDFLARE_API_TOKEN" in exc_info.value.detail


@pytest.mark.asyncio
async def test_transcribe_workers_ai_upstream_error_is_502(monkeypatch, cf_settings):
    fake = _FakeAsyncClient(404, {"success": False, "errors": [{"message": "model not found"}]})
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=120.0: fake)

    with pytest.raises(HTTPException) as exc_info:
        await inference.transcribe_workers_ai(b"audio", "audio/wav")
    assert exc_info.value.status_code == 502
    assert "404" in exc_info.value.detail


def test_resolve_base_url_substitutes_account_id(monkeypatch, cf_settings):
    url = inference._resolve_base_url(
        "cloudflare",
        "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/",
    )
    assert url == "https://api.cloudflare.com/client/v4/accounts/account-123/ai/run/"


def test_resolve_base_url_leaves_other_providers_untouched():
    url = inference._resolve_base_url("groq", "https://api.groq.com/openai/v1")
    assert url == "https://api.groq.com/openai/v1"


def test_stt_models_catalog_has_whisper():
    assert inference.STT_MODELS["whisper"] == "@cf/openai/whisper"


@pytest.mark.asyncio
async def test_call_workers_ai_chat_success(monkeypatch, cf_settings):
    fake = _FakeAsyncClient(200, {"result": {"response": "Hello there!"}})
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=300.0: fake)

    result = await inference._call_workers_ai_chat(
        "Say hi",
        model="@cf/meta/llama-3.1-8b-instruct",
        api_key="tok-456",
        max_tokens=32,
    )

    assert result == {"text": "Hello there!"}
    assert fake.last_url.endswith("/ai/run/@cf/meta/llama-3.1-8b-instruct")
    assert fake.last_json["messages"][-1]["content"] == "Say hi"
    assert fake.last_json["max_tokens"] == 32


@pytest.mark.asyncio
async def test_call_workers_ai_chat_parses_schema(monkeypatch, cf_settings):
    fake = _FakeAsyncClient(
        200,
        {"result": {"response": '{"content": "A post", "hashtags": ["#ai"], "suggested_media": null}'}},
    )
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=300.0: fake)

    result = await inference._call_workers_ai_chat(
        "Write a post",
        model="@cf/meta/llama-3.1-8b-instruct",
        api_key="tok-456",
        schema={"type": "object"},
    )

    assert result["content"] == "A post"
    assert result["hashtags"] == ["#ai"]
    # User message was augmented with the JSON instruction
    assert "Return ONLY valid JSON" in fake.last_json["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_call_workers_ai_chat_handles_structured_dict_response(monkeypatch, cf_settings):
    """Newer Workers AI models return `result.response` already parsed as a dict
    when the model emits structured JSON."""
    fake = _FakeAsyncClient(
        200,
        {"result": {"response": {"content": "A post", "hashtags": ["#ai"], "suggested_media": None}}},
    )
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=300.0: fake)

    result = await inference._call_workers_ai_chat(
        "Write a post",
        model="@cf/meta/llama-3.1-8b-instruct",
        api_key="tok-456",
        schema={"type": "object"},
    )

    assert result == {"content": "A post", "hashtags": ["#ai"], "suggested_media": None}


@pytest.mark.asyncio
async def test_call_workers_ai_chat_missing_account_id(monkeypatch):
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_API_TOKEN", "tok-456")

    with pytest.raises(HTTPException) as exc_info:
        await inference._call_workers_ai_chat("hi", model="@cf/meta/llama-3.1-8b-instruct", api_key="tok-456")
    assert exc_info.value.status_code == 400
@pytest.mark.asyncio
async def test_list_workers_ai_models_paginates_and_normalizes(monkeypatch, cf_settings):
    page1 = _FakeResponse(
        200,
        {
            "result": [
                {"name": "@cf/meta/llama-3.1-8b-instruct", "task": {"name": "Text Generation"}, "description": "Llama"},
                {"name": "@cf/openai/whisper", "task": "Automatic Speech Recognition", "description": "Whisper STT"},
            ],
            "result_info": {"page": 1, "total_pages": 2},
        },
    )
    page2 = _FakeResponse(
        200,
        {
            "result": [{"name": "@cf/deepgram/nova-3", "task": None, "description": "Deepgram ASR"}],
            "result_info": {"page": 2, "total_pages": 2},
        },
    )
    fake = _FakeGetClient({1: page1, 2: page2})
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=60.0: fake)

    models = await inference.list_workers_ai_models()

    assert [m["id"] for m in models] == [
        "@cf/meta/llama-3.1-8b-instruct",
        "@cf/openai/whisper",
        "@cf/deepgram/nova-3",
    ]
    assert models[0]["task"] == "Text Generation"
    assert models[1]["task"] == "Automatic Speech Recognition"
    assert models[2]["task"] is None
    # Both pages fetched with bearer auth
    assert len(fake.calls) == 2
    assert all(c["headers"]["Authorization"] == "Bearer tok-456" for c in fake.calls)
    assert "account-123" in fake.calls[0]["url"]


@pytest.mark.asyncio
async def test_list_workers_ai_models_requires_credentials(monkeypatch):
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_API_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        await inference.list_workers_ai_models()
    assert exc.value.status_code == 400

@pytest.mark.asyncio
async def test_transcribe_normalizes_content_type(monkeypatch, cf_settings):
    """Generic content types must be rewritten — Workers AI rejects them (8001)."""
    fake = _FakeAsyncClient(200, {"result": {"text": "hi"}})
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=120.0: fake)

    await inference.transcribe_workers_ai(
        b"RIFF", "application/octet-stream", model="@cf/openai/whisper"
    )
    assert fake.last_headers["Content-Type"] == "audio/wav"

    await inference.transcribe_workers_ai(b"RIFF", "audio/mpeg", model="@cf/openai/whisper")
    assert fake.last_headers["Content-Type"] == "audio/mpeg"

    await inference.transcribe_workers_ai(b"RIFF", None, model="@cf/openai/whisper")
    assert fake.last_headers["Content-Type"] == "audio/wav"
