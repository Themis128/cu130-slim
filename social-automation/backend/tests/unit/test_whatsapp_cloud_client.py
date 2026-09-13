from unittest.mock import patch

import pytest

from app.services import whatsapp_cloud_client as wac
from app.services.whatsapp_api import WhatsAppAPIClient


class _FakeResponse:
    def __init__(self, status_code: int, body, headers=None, content: bytes | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.content = content or b""

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("response body is not JSON")

    @property
    def text(self) -> str:
        if isinstance(self._body, bytes):
            return self._body.decode("utf-8", errors="replace")
        return str(self._body)


class _FakeAsyncClient:
    def __init__(self, responses):
        if isinstance(responses, _FakeResponse):
            responses = [responses]
        self._responses = list(responses)
        self._call_index = 0
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, params=None, json=None, data=None, files=None, headers=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
                "data": data,
                "files": files,
                "headers": headers,
            }
        )
        return self._next_response()

    async def get(self, url, headers=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return self._next_response()

    def _next_response(self):
        if self._call_index >= len(self._responses):
            return _FakeResponse(500, "out of preset responses")
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


@pytest.mark.asyncio
async def test_whatsapp_cloud_client_classifies_authorization_error():
    fake = _FakeAsyncClient(
        _FakeResponse(
            400,
            {
                "error": {
                    "message": "Invalid OAuth access token.",
                    "type": "OAuthException",
                    "code": 190,
                    "fbtrace_id": "trace-1",
                }
            },
        )
    )
    with patch("app.services.whatsapp_cloud_client.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        client = wac.WhatsAppCloudClient(access_token="tok-123", api_version="v26.0", timeout_s=1.0)
        with pytest.raises(wac.WhatsAppAuthorizationError) as exc:
            await client.request("GET", "me")

    assert exc.value.code == 190
    assert exc.value.fbtrace_id == "trace-1"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_request_verification_code_skips_when_already_verified():
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "id": "105954558954427",
                "display_phone_number": "15555555555",
                "code_verification_status": "VERIFIED",
            },
        )
    )
    with patch("app.services.whatsapp_cloud_client.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        api = WhatsAppAPIClient(access_token="tok-123", phone_number_id="105954558954427", api_version="v26.0")
        result = await api.request_verification_code("105954558954427", code_method="SMS", language="en_US")

    assert result["success"] is True
    assert result["skipped"] is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["method"] == "GET"


@pytest.mark.asyncio
async def test_request_verification_code_surfaces_136024_with_rate_limit():
    # First GET fails (we proceed anyway), then POST returns 136024.
    headers = {
        "Retry-After": "120",
        "X-Business-Use-Case-Usage": '{"foo":{"call_count":1,"total_cputime":1,"total_time":1}}',
    }
    fake = _FakeAsyncClient(
        [
            _FakeResponse(500, {"error": {"message": "upstream error", "type": "Server", "code": 1, "fbtrace_id": "t0"}}),
            _FakeResponse(200, {"id": "105954558954427"}),  # fallback GET without fields
            _FakeResponse(
                400,
                {
                    "error": {
                        "message": "Phone number already verified",
                        "type": "OAuthException",
                        "code": 136024,
                        "fbtrace_id": "trace-2",
                        "is_transient": False,
                    }
                },
                headers=headers,
            ),
        ]
    )
    with patch("app.services.whatsapp_cloud_client.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        api = WhatsAppAPIClient(access_token="tok-123", phone_number_id="105954558954427", api_version="v26.0")
        with pytest.raises(wac.WhatsAppPhoneVerificationError) as exc:
            await api.request_verification_code("105954558954427", code_method="SMS", language="en_US")

    assert exc.value.code == 136024
    assert exc.value.rate_limit.retry_after_s == 120
    assert exc.value.rate_limit.x_business_use_case_usage is not None

