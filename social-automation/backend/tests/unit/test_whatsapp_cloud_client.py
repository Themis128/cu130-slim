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


# ---------- header / error-envelope parsing ----------


def test_parse_rate_limit_bad_and_missing_values():
    r = _FakeResponse(
        200,
        {},
        headers={
            "Retry-After": "not-a-number",
            "X-App-Usage": '{"call_count":5}',
            "X-Business-Use-Case-Usage": "{bad json",
        },
    )
    info = wac._parse_rate_limit(r)
    assert info.retry_after_s is None
    assert info.x_app_usage == {"call_count": 5}
    assert info.x_business_use_case_usage is None

    info2 = wac._parse_rate_limit(_FakeResponse(200, {}))
    assert info2.retry_after_s is None and info2.x_app_usage is None


def test_extract_meta_error_variants():
    # non-JSON body → None
    assert wac._extract_meta_error(_FakeResponse(500, "oops")) is None
    # non-dict body → None
    assert wac._extract_meta_error(_FakeResponse(500, [1, 2])) is None
    # dict without error → None
    assert wac._extract_meta_error(_FakeResponse(500, {"x": 1})) is None
    # error not a dict → None
    assert wac._extract_meta_error(_FakeResponse(500, {"error": "x"})) is None
    # all typed fields extracted; wrong types skipped
    r = _FakeResponse(
        400,
        {
            "error": {
                "message": "m",
                "type": "t",
                "code": 1,
                "error_subcode": 2,
                "fbtrace_id": "f",
                "is_transient": True,
                "error_user_title": "ut",
                "error_user_msg": "um",
                "error_data": {"details": "d"},
                "code_bad": "ignored",
                "is_transient_bad": "no",
            }
        },
    )
    err = wac._extract_meta_error(r)
    assert err["message"] == "m" and err["code"] == 1
    assert err["error_data"] == {"details": "d"}


def test_extract_meta_error_empty_dict_is_none():
    assert wac._extract_meta_error(_FakeResponse(400, {"error": {}})) is None


def test_human_error_message_precedence():
    # error_data.details wins
    assert wac._human_error_message({"error_data": {"details": " deep details "}, "message": "shallow"}, "fb") == "deep details"
    # user_title + user_msg second
    assert wac._human_error_message({"error_user_title": "T", "error_user_msg": "M", "message": "m"}, "fb") == "T: M"
    # plain message third
    assert wac._human_error_message({"message": " just msg "}, "fb") == "just msg"
    # empty dict / None → fallback
    assert wac._human_error_message({}, "fb") == "fb"
    assert wac._human_error_message(None, "fb") == "fb"


# ---------- error classification ----------


def _err(code):
    e = {"message": "m", "code": code} if code is not None else None
    return wac._classify_error(status_code=400, url="u", response_text="t", error=e, rate_limit=wac.RateLimitInfo(None, None, None, {}))


def test_classify_throttling_codes():
    assert isinstance(_err(4), wac.WhatsAppThrottlingError)
    assert isinstance(_err(130429), wac.WhatsAppThrottlingError)


def test_classify_send_message_codes():
    assert isinstance(_err(131047), wac.WhatsAppSendMessageError)
    assert isinstance(_err(132000), wac.WhatsAppSendMessageError)


def test_classify_authorization_range_and_generic():
    assert isinstance(_err(249), wac.WhatsAppAuthorizationError)  # 200-299 range
    assert isinstance(_err(99999), wac.WhatsAppApiError)
    assert not isinstance(_err(99999), wac.WhatsAppAuthorizationError)
    assert isinstance(_err(None), wac.WhatsAppApiError)


def test_error_properties_on_empty_error():
    e = _err(None)
    assert e.code is None and e.error_subcode is None
    assert e.fbtrace_id == "" and e.is_transient is None
    e2 = wac.WhatsAppApiError(
        status_code=500,
        url="u",
        response_text="t",
        error={"error_subcode": 9, "is_transient": True, "message": "x"},
        rate_limit=wac.RateLimitInfo(None, None, None, {}),
    )
    assert e2.error_subcode == 9 and e2.is_transient is True


def test_map_upstream_status():
    assert wac._map_upstream_status(503) == 503
    assert wac._map_upstream_status(504) == 503
    assert wac._map_upstream_status(500) == 502
    assert wac._map_upstream_status(400) == 400


# ---------- request / get_bytes ----------


@pytest.mark.asyncio
async def test_request_success_and_network_error():
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": 1}))
    with patch("app.services.whatsapp_cloud_client.httpx.AsyncClient", return_value=fake):
        client = wac.WhatsAppCloudClient(access_token="t")
        assert await client.request("GET", "me") == {"ok": 1}


class _NetFailClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, **kw):
        raise ConnectionError("dns fail")

    async def get(self, *a, **kw):
        raise ConnectionError("dns fail")


@pytest.mark.asyncio
async def test_request_network_error_raises():
    with patch("app.services.whatsapp_cloud_client.httpx.AsyncClient", return_value=_NetFailClient()):
        client = wac.WhatsAppCloudClient(access_token="t")
        with pytest.raises(wac.WhatsAppNetworkError):
            await client.request("GET", "me")
        with pytest.raises(wac.WhatsAppNetworkError):
            await client.get_bytes(absolute_url="https://x")


@pytest.mark.asyncio
async def test_request_200_but_not_json():
    fake = _FakeAsyncClient(_FakeResponse(200, "not json"))
    with patch("app.services.whatsapp_cloud_client.httpx.AsyncClient", return_value=fake):
        client = wac.WhatsAppCloudClient(access_token="t")
        with pytest.raises(wac.WhatsAppApiError):
            await client.request("GET", "me")


@pytest.mark.asyncio
async def test_get_bytes_success_and_error():
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, b"raw", content=b"raw-bytes"),
            _FakeResponse(404, {"error": {"message": "x", "code": 190}}),
        ]
    )
    with patch("app.services.whatsapp_cloud_client.httpx.AsyncClient", return_value=fake):
        client = wac.WhatsAppCloudClient(access_token="t")
        assert await client.get_bytes(absolute_url="https://media") == b"raw-bytes"
        with pytest.raises(wac.WhatsAppAuthorizationError):
            await client.get_bytes(absolute_url="https://media")


@pytest.mark.asyncio
async def test_request_extra_headers_and_absolute_url():
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": 1}))
    with patch("app.services.whatsapp_cloud_client.httpx.AsyncClient", return_value=fake):
        client = wac.WhatsAppCloudClient(access_token="t", api_version="/v26.0/")
        await client.request(
            "POST", "p", params={"a": 1}, json_body={"b": 2}, data={"d": 3}, files={"f": 4}, headers={"X-Extra": "yes"}, absolute_url="https://abs.example/x"
        )
    c = fake.calls[0]
    assert c["url"] == "https://abs.example/x"
    assert c["headers"]["X-Extra"] == "yes"
    assert c["params"] == {"a": 1} and c["files"] == {"f": 4}


# ---------- client init / validate_wa_phone ----------


def test_client_requires_token_and_strips_version_slashes():
    with pytest.raises(ValueError):
        wac.WhatsAppCloudClient(access_token="")
    c = wac.WhatsAppCloudClient(access_token="t", api_version="/v26.0/")
    assert c.api_version == "v26.0/"  # only leading slashes are stripped


def test_validate_wa_phone():
    assert wac.validate_wa_phone("+15551234567") == "15551234567"
    assert wac.validate_wa_phone("15551234567") == "15551234567"
    with pytest.raises(ValueError):
        wac.validate_wa_phone("")
    with pytest.raises(ValueError):
        wac.validate_wa_phone("abc123")
    with pytest.raises(ValueError):
        wac.validate_wa_phone("+" + "9" * 20)
