"""Unit tests for Cloudflare Workers AI Batch inference (queueRequest=true)."""
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


BATCH_URL = (
    "https://api.cloudflare.com/client/v4/accounts/account-123"
    "/ai/run/@cf/baai/bge-m3?queueRequest=true"
)


@pytest.mark.asyncio
async def test_submit_batch_success(monkeypatch, cf_settings):
    fake = _FakeAsyncClient(
        200,
        {
            "result": {
                "status": "queued",
                "request_id": "768f15b7-4fd6-4498-906e-ad94ffc7f8d2",
                "model": "@cf/baai/bge-m3",
            },
            "success": True,
            "errors": [],
        },
    )
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=120.0: fake)

    result = await inference.submit_workers_ai_batch(
        "@cf/baai/bge-m3",
        [
            {
                "query": "This is a story about Cloudflare",
                "contexts": [{"text": "This is a story about an orange cloud"}],
                "external_reference": "reference-1",
            }
        ],
    )

    assert result == {
        "request_id": "768f15b7-4fd6-4498-906e-ad94ffc7f8d2",
        "status": "queued",
        "model": "@cf/baai/bge-m3",
    }
    assert fake.last_url == BATCH_URL
    assert fake.last_headers["Authorization"] == "Bearer tok-456"
    assert fake.last_headers["Content-Type"] == "application/json"
    assert set(fake.last_json.keys()) == {"requests"}
    assert fake.last_json["requests"][0]["external_reference"] == "reference-1"


@pytest.mark.asyncio
async def test_submit_batch_requires_account_id(monkeypatch):
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_API_TOKEN", "tok-456")

    with pytest.raises(HTTPException) as exc_info:
        await inference.submit_workers_ai_batch("@cf/baai/bge-m3", [{"query": "hi"}])
    assert exc_info.value.status_code == 400
    assert "CLOUDFLARE_ACCOUNT_ID" in exc_info.value.detail


@pytest.mark.asyncio
async def test_submit_batch_rejects_empty_requests(monkeypatch, cf_settings):
    with pytest.raises(HTTPException) as exc_info:
        await inference.submit_workers_ai_batch("@cf/baai/bge-m3", [])
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_submit_batch_upstream_error(monkeypatch, cf_settings):
    fake = _FakeAsyncClient(400, {"message": "batch queueing not available (code: 10063)"})
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=120.0: fake)

    with pytest.raises(HTTPException) as exc_info:
        await inference.submit_workers_ai_batch("@cf/baai/bge-m3", [{"query": "hi"}])
    assert exc_info.value.status_code == 502
    assert "10063" in exc_info.value.detail


@pytest.mark.asyncio
async def test_retrieve_batch_success(monkeypatch, cf_settings):
    fake = _FakeAsyncClient(
        200,
        {
            "result": {
                "responses": [
                    {
                        "id": 0,
                        "result": {
                            "response": [
                                {"id": 0, "score": 0.73974609375},
                                {"id": 1, "score": 0.642578125},
                            ]
                        },
                        "success": True,
                        "external_reference": "reference-1",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 0, "total_tokens": 12},
            },
            "success": True,
            "errors": [],
        },
    )
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=120.0: fake)

    result = await inference.retrieve_workers_ai_batch(
        "@cf/baai/bge-m3", request_id="768f15b7-4fd6-4498-906e-ad94ffc7f8d2"
    )

    # Same endpoint as submission — only the body differs.
    assert fake.last_url == BATCH_URL
    assert fake.last_json == {"request_id": "768f15b7-4fd6-4498-906e-ad94ffc7f8d2"}
    assert len(result["responses"]) == 1
    assert result["responses"][0]["external_reference"] == "reference-1"
    assert result["usage"]["total_tokens"] == 12


@pytest.mark.asyncio
async def test_retrieve_batch_still_processing(monkeypatch, cf_settings):
    """While queued, the API may return an empty/absent responses list."""
    fake = _FakeAsyncClient(200, {"result": {}, "success": True, "errors": []})
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=120.0: fake)

    result = await inference.retrieve_workers_ai_batch("@cf/baai/bge-m3", request_id="abc")

    assert result["responses"] == []
    assert result["usage"] is None


@pytest.mark.asyncio
async def test_retrieve_batch_missing_credentials(monkeypatch):
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_API_TOKEN", "")
    with pytest.raises(HTTPException) as exc_info:
        await inference.retrieve_workers_ai_batch("@cf/baai/bge-m3", request_id="abc")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_submit_batch_handles_new_direct_format(monkeypatch, cf_settings):
    """Batch submission may return direct format with request_id at top level."""
    fake = _FakeAsyncClient(
        200,
        {
            "request_id": "direct-req-123",
            "status": "queued",
            "model": "@cf/baai/bge-m3",
        },
    )
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=120.0: fake)

    result = await inference.submit_workers_ai_batch(
        "@cf/baai/bge-m3",
        [{"query": "test"}],
    )

    assert result["request_id"] == "direct-req-123"
    assert result["status"] == "queued"
    assert result["model"] == "@cf/baai/bge-m3"


@pytest.mark.asyncio
async def test_retrieve_batch_handles_new_direct_format(monkeypatch, cf_settings):
    """Batch retrieval may return direct format with responses at top level."""
    fake = _FakeAsyncClient(
        200,
        {
            "status": "completed",
            "responses": [
                {
                    "id": 0,
                    "result": {"response": [0.8, 0.2]},
                    "success": True,
                }
            ],
            "usage": {"total_tokens": 10},
        },
    )
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=120.0: fake)

    result = await inference.retrieve_workers_ai_batch(
        "@cf/baai/bge-m3", request_id="direct-req-123"
    )

    assert result["status"] == "completed"
    assert len(result["responses"]) == 1
    assert result["usage"]["total_tokens"] == 10
