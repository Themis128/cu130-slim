"""Unit tests for the Facebook Graph API client."""

import json
from unittest.mock import patch

import httpx
import pytest

from app.services import facebook_api as api


class _FakeResponse:
    def __init__(self, status_code: int, body, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("response body is not JSON")

    @property
    def text(self) -> str:
        if isinstance(self._body, bytes):
            return self._body.decode("utf-8", errors="replace")
        return str(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,
                response=self,
            )


class _FakeAsyncClient:
    """httpx.AsyncClient stand-in that records last request and returns a preset response."""

    def __init__(self, responses: list[_FakeResponse] | _FakeResponse | None = None):
        if isinstance(responses, _FakeResponse):
            responses = [responses]
        self._responses = list(responses or [])
        self._call_index = 0
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        return self._next_response()

    async def post(self, url, headers=None, params=None, _json=None, data=None, content=None):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "params": params,
                "json": _json,
                "data": data,
                "content": content,
            }
        )
        return self._next_response()

    async def delete(self, url, headers=None, params=None):
        self.calls.append({"method": "DELETE", "url": url, "headers": headers, "params": params})
        return self._next_response()

    def _next_response(self):
        if self._call_index >= len(self._responses):
            return _FakeResponse(500, "out of preset responses")
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


@pytest.fixture
def client():
    return api.FacebookAPIClient(access_token="tok-123", page_id="123456789")


@pytest.mark.asyncio
async def test_validate_token(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "me-1", "name": "Test User"}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.validate_token()

    assert result["id"] == "me-1"
    assert result["name"] == "Test User"
    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["url"] == f"{api.FACEBOOK_GRAPH_BASE}/{api.DEFAULT_API_VERSION}/me"
    assert fake.calls[0]["params"]["fields"] == "id,name"
    assert fake.calls[0]["params"]["access_token"] == "tok-123"


@pytest.mark.asyncio
async def test_get_pages(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "data": [
                    {
                        "id": "111",
                        "name": "Page One",
                        "access_token": "page-token-1",
                        "category": "Community",
                    }
                ]
            },
        )
    )
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        pages = await client.get_pages()

    assert len(pages) == 1
    assert pages[0]["id"] == "111"
    assert pages[0]["name"] == "Page One"
    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["url"].endswith("/me/accounts")
    assert fake.calls[0]["params"]["fields"] == "id,name,access_token,category,tasks"


@pytest.mark.asyncio
async def test_exchange_long_lived_token(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"access_token": "long-lived-token"}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        token = await client.exchange_long_lived_token("client-id", "client-secret")

    assert token == "long-lived-token"
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/oauth/access_token")
    assert call["params"]["grant_type"] == "fb_exchange_token"
    assert call["params"]["client_id"] == "client-id"
    assert call["params"]["client_secret"] == "client-secret"
    assert call["params"]["fb_exchange_token"] == "tok-123"


@pytest.mark.asyncio
async def test_exchange_long_lived_token_missing_token_raises(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        with pytest.raises(ValueError, match="long-lived access token"):
            await client.exchange_long_lived_token("client-id", "client-secret")


@pytest.mark.asyncio
async def test_get_long_lived_page_tokens(client):
    me_resp = _FakeResponse(200, {"id": "user-42", "name": "Test User"})
    accounts_resp = _FakeResponse(
        200,
        {"data": [{"id": "222", "name": "Page Two", "access_token": "page-token-2"}]},
    )
    fake = _FakeAsyncClient([me_resp, accounts_resp])
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        pages = await client.get_long_lived_page_tokens("long-user-token")

    assert len(pages) == 1
    assert pages[0]["id"] == "222"
    assert pages[0]["access_token"] == "page-token-2"
    assert fake.calls[0]["url"].endswith("/me")
    assert fake.calls[0]["params"]["access_token"] == "long-user-token"
    assert fake.calls[1]["url"].endswith("/user-42/accounts")
    assert fake.calls[1]["params"]["access_token"] == "long-user-token"


@pytest.mark.asyncio
async def test_create_post(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "post-1"}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.create_post("Hello Facebook!", "https://example.com")

    assert result["id"] == "post-1"
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/123456789/feed")
    assert call["data"]["message"] == "Hello Facebook!"
    assert call["data"]["link"] == "https://example.com"
    assert call["params"]["access_token"] == "tok-123"


@pytest.mark.asyncio
async def test_create_photo_post(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "photo-1", "post_id": "post-2"}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.create_photo_post("https://example.com/photo.jpg", "My caption")

    assert result["id"] == "photo-1"
    assert result["post_id"] == "post-2"
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/123456789/photos")
    assert call["data"]["url"] == "https://example.com/photo.jpg"
    assert call["data"]["caption"] == "My caption"


@pytest.mark.asyncio
async def test_create_video_post(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"id": "video-1"}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.create_video_post("https://example.com/video.mp4", "My description")

    assert result["id"] == "video-1"
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/123456789/videos")
    assert call["data"]["file_url"] == "https://example.com/video.mp4"
    assert call["data"]["description"] == "My description"


@pytest.mark.asyncio
async def test_get_page_insights(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "data": [
                    {
                        "name": "page_impressions_unique",
                        "values": [{"value": 42}],
                    }
                ]
            },
        )
    )
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.get_page_insights(
            metric="page_impressions_unique",
            period="day",
            since="2023-01-01",
            until="2023-01-02",
        )

    assert result["data"][0]["name"] == "page_impressions_unique"
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/123456789/insights")
    assert call["params"]["metric"] == "page_impressions_unique"
    assert call["params"]["period"] == "day"
    assert call["params"]["since"] == "2023-01-01"
    assert call["params"]["until"] == "2023-01-02"


@pytest.mark.asyncio
async def test_get_post_insights(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {"data": [{"name": "post_impressions", "values": [{"value": 10}]}]},
        )
    )
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.get_post_insights("987654321")

    assert result["data"][0]["name"] == "post_impressions"
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/987654321/insights")
    assert call["params"]["metric"] == "post_impressions,post_engaged_users"


@pytest.mark.asyncio
async def test_delete_post(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"success": True}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        ok = await client.delete_post("987654321")

    assert ok is True
    call = fake.calls[0]
    assert call["method"] == "DELETE"
    assert call["url"].endswith("/987654321")
    assert call["params"]["access_token"] == "tok-123"


@pytest.mark.asyncio
async def test_delete_post_empty_response_defaults_to_true(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        ok = await client.delete_post("987654321")

    assert ok is True


@pytest.mark.asyncio
async def test_error_handling_4xx(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            400,
            {
                "error": {
                    "message": "Invalid token",
                    "type": "OAuthException",
                    "code": 190,
                }
            },
        )
    )
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        with pytest.raises(api.FacebookAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 400
    assert "Invalid token" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_error_handling_5xx(client):
    fake = _FakeAsyncClient(_FakeResponse(500, {"error": {"message": "Internal server error"}}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        with pytest.raises(api.FacebookAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 502
    assert "Internal server error" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_create_multi_photo_post_success(client):
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"id": "photo-1"}),
            _FakeResponse(200, {"id": "photo-2"}),
            _FakeResponse(200, {"id": "post-1"}),
        ]
    )
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        result = await client.create_multi_photo_post(
            image_urls=["https://example.com/one.jpg", "https://example.com/two.jpg"],
            message="Hello multi",
            link="https://example.com",
        )

    assert result["id"] == "post-1"
    assert len(fake.calls) == 3
    photo_call = fake.calls[0]
    assert photo_call["method"] == "POST"
    assert photo_call["url"].endswith("/123456789/photos")
    assert photo_call["data"]["published"] == "false"
    assert photo_call["data"]["url"] == "https://example.com/one.jpg"
    assert photo_call["params"]["access_token"] == "tok-123"
    feed_call = fake.calls[2]
    assert feed_call["method"] == "POST"
    assert feed_call["url"].endswith("/123456789/feed")
    assert feed_call["data"]["message"] == "Hello multi"
    assert feed_call["data"]["link"] == "https://example.com"
    attached = json.loads(feed_call["data"]["attached_media"])
    assert attached == [{"media_fbid": "photo-1"}, {"media_fbid": "photo-2"}]


@pytest.mark.asyncio
async def test_create_multi_photo_post_empty_image_urls(client):
    with pytest.raises(ValueError, match="At least one image_url"):
        await client.create_multi_photo_post(image_urls=[], message="Hello")


@pytest.mark.asyncio
async def test_create_multi_photo_post_all_uploads_fail(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch("app.services.facebook_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake

        with pytest.raises(api.FacebookAPIError) as exc_info:
            await client.create_multi_photo_post(
                image_urls=["https://example.com/bad.jpg"],
                message="Hello",
            )

    assert exc_info.value.status_code == 400
    assert "No Facebook photo uploads succeeded" in exc_info.value.response_text


# ── coverage append: helpers + uncovered methods ────────────────────


class _HTTP:
    """Minimal AsyncClient fake accepting any kwargs, routing by URL."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _go(self, m, url, kw):
        self.calls.append((m, url, kw))
        return self.handler(m, url, kw)

    async def get(self, url, **kw):
        return await self._go("GET", url, kw)

    async def post(self, url, **kw):
        return await self._go("POST", url, kw)

    async def delete(self, url, **kw):
        return await self._go("DELETE", url, kw)


def _wire(monkeypatch, handler):
    holder = {}

    def _factory(**kw):
        c = _HTTP(handler)
        holder["c"] = c
        return c

    monkeypatch.setattr(api.httpx, "AsyncClient", _factory)
    return holder


def _client():
    return api.FacebookAPIClient(access_token="tok", page_id="123")


class TestHelpers:
    def test_validate_id(self):
        assert api._validate_id(" 123_456 ") == "123_456"
        for bad in ["", "abc", "1-2", "../../x", "12a"]:
            with pytest.raises(ValueError):
                api._validate_id(bad)

    def test_sanitize_log_text(self):
        out = api._sanitize_log_text("a\nb\rc\x01d")
        assert "\\n" in out and "\\r" in out
        assert "\x01" not in out
        assert len(api._sanitize_log_text("x" * 500, 10)) == 10

    def test_mask_sensitive(self):
        out = api._mask_sensitive("+30 699 1234567 called")
        assert "1234567" not in out and "***" in out
        out2 = api._mask_sensitive("access_token=abc123&x=1")
        assert "abc123" not in out2


class TestErrorClass:
    def test_from_response_full(self):
        resp = _FakeResponse(400, {"error": {"message": "bad thing", "type": "OAuthEx", "code": 190}})
        err = api.FacebookAPIError.from_response(resp, "u")
        assert err.status_code == 400
        assert "(OAuthEx)" in str(err) and "[code 190]" in str(err)
        assert "bad thing" in str(err)

    def test_from_response_no_error_obj(self):
        resp = _FakeResponse(400, {"other": 1})
        err = api.FacebookAPIError.from_response(resp, "u")
        assert "u" in str(err)

    def test_from_response_non_json(self):
        resp = _FakeResponse(500, "not json")
        err = api.FacebookAPIError.from_response(resp, "u")
        assert err.status_code == 500

    def test_init_and_map(self):
        c = _client()
        assert c._map_status_code(500) == 502
        assert c._map_status_code(503) == 503
        assert c._map_status_code(504) == 503
        assert c._map_status_code(429) == 429
        assert api.FacebookAPIError(400, "t", "u").status_code == 400


class TestCtor:
    def test_no_token(self):
        with pytest.raises(ValueError):
            api.FacebookAPIClient("", "123")

    def test_bad_page_id(self):
        with pytest.raises(ValueError):
            api.FacebookAPIClient("t", "bad id")

    def test_version_lstrip(self):
        c = api.FacebookAPIClient("t", "123", api_version="/v21.0")
        assert c.api_version == "v21.0"
        assert c._url("/x").endswith("/v21.0/x")


class TestPageTokens:
    @pytest.mark.asyncio
    async def test_long_lived_pages(self, monkeypatch):
        resps = iter([_FakeResponse(200, {"id": "u1"}), _FakeResponse(200, {"data": [{"id": "p"}]})])
        _wire(monkeypatch, lambda m, u, kw: next(resps))
        out = await _client().get_long_lived_page_tokens("lltok")
        assert out == [{"id": "p"}]

    @pytest.mark.asyncio
    async def test_no_user_id(self, monkeypatch):
        _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {}))
        with pytest.raises(ValueError, match="user id"):
            await _client().get_long_lived_page_tokens("lltok")


class TestPublishGuards:
    @pytest.mark.asyncio
    async def test_post_requires_content(self):
        with pytest.raises(ValueError):
            await _client().create_post("")

    @pytest.mark.asyncio
    async def test_post_with_link(self, monkeypatch):
        holder = _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"id": "x"}))
        await _client().create_post("msg", link="https://l")
        assert holder["c"].calls[-1][2]["data"]["link"] == "https://l"

    @pytest.mark.asyncio
    async def test_photo_requires_url(self):
        with pytest.raises(ValueError):
            await _client().create_photo_post("")

    @pytest.mark.asyncio
    async def test_photo_happy(self, monkeypatch):
        holder = _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"id": "p"}))
        await _client().create_photo_post("https://img", "cap")
        assert holder["c"].calls[-1][2]["data"]["caption"] == "cap"

    @pytest.mark.asyncio
    async def test_video_requires_url(self):
        with pytest.raises(ValueError):
            await _client().create_video_post("")

    @pytest.mark.asyncio
    async def test_video_happy(self, monkeypatch):
        holder = _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"id": "v"}))
        await _client().create_video_post("https://v", "desc")
        assert holder["c"].calls[-1][2]["data"]["file_url"] == "https://v"


class TestInsights:
    @pytest.mark.asyncio
    async def test_metric_required(self):
        with pytest.raises(ValueError):
            await _client().get_page_insights("")

    @pytest.mark.asyncio
    async def test_page_insights_params(self, monkeypatch):
        holder = _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"data": []}))
        await _client().get_page_insights("page_views", period="day", since="2026-01-01", until="2026-02-01")
        params = holder["c"].calls[-1][2]["params"]
        assert params["since"] == "2026-01-01"
        assert params["until"] == "2026-02-01"

    @pytest.mark.asyncio
    async def test_post_insights(self, monkeypatch):
        _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"data": [{"x": 1}]}))
        out = await _client().get_post_insights("123_456")
        assert out["data"] == [{"x": 1}]


class TestPageProfile:
    @pytest.mark.asyncio
    async def test_page_info(self, monkeypatch):
        _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"name": "P"}))
        assert (await _client().get_page_info())["name"] == "P"

    @pytest.mark.asyncio
    async def test_update_about_too_long(self):
        with pytest.raises(ValueError, match="100"):
            await _client().update_page_info(about="x" * 101)

    @pytest.mark.asyncio
    async def test_update_no_fields(self):
        with pytest.raises(ValueError):
            await _client().update_page_info()

    @pytest.mark.asyncio
    async def test_update_fields(self, monkeypatch):
        holder = _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"success": True}))
        out = await _client().update_page_info(about="a", website="w", phone="p", description="d")
        assert out is True
        data = holder["c"].calls[-1][2]["data"]
        assert data == {"about": "a", "description": "d", "website": "w", "phone": "p"}

    @pytest.mark.asyncio
    async def test_upload_picture(self, monkeypatch):
        holder = _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"success": True}))
        assert await _client().upload_profile_picture(b"png") is True
        assert "source" in holder["c"].calls[-1][2]["files"]

    @pytest.mark.asyncio
    async def test_cover_two_step(self, monkeypatch):
        resps = iter([_FakeResponse(200, {"id": "ph1"}), _FakeResponse(200, {"success": True})])
        _wire(monkeypatch, lambda m, u, kw: next(resps))
        assert await _client().upload_cover_photo(b"img") == "ph1"

    @pytest.mark.asyncio
    async def test_cover_no_photo_id(self, monkeypatch):
        _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {}))
        with pytest.raises(api.FacebookAPIError, match="photo ID"):
            await _client().upload_cover_photo(b"img")


class TestTasks:
    @pytest.mark.asyncio
    async def test_assigned_users(self, monkeypatch):
        _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"data": [{"id": "u"}]}))
        assert await _client().get_assigned_users("111") == [{"id": "u"}]

    @pytest.mark.asyncio
    async def test_assign_tasks_empty(self):
        with pytest.raises(ValueError):
            await _client().assign_page_tasks("111", [], "222")

    @pytest.mark.asyncio
    async def test_assign_tasks(self, monkeypatch):
        holder = _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"success": True}))
        assert await _client().assign_page_tasks("111", ["MANAGE"], "222") is True
        data = holder["c"].calls[-1][2]["data"]
        assert data["tasks"] == ["MANAGE"]

    @pytest.mark.asyncio
    async def test_page_tasks_found(self, monkeypatch):
        _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"data": [{"id": "999", "tasks": ["X"]}, {"id": "123", "tasks": ["MANAGE", "CREATE_CONTENT"]}]}))
        out = await _client().get_page_tasks()
        assert out == ["MANAGE", "CREATE_CONTENT"]

    @pytest.mark.asyncio
    async def test_page_tasks_not_found(self, monkeypatch):
        _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"data": [{"id": "999"}]}))
        assert await _client().get_page_tasks() == []


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_post(self, monkeypatch):
        holder = _wire(monkeypatch, lambda m, u, kw: _FakeResponse(200, {"success": True}))
        assert await _client().delete_post("123_456") is True
        assert holder["c"].calls[-1][0] == "DELETE"

    @pytest.mark.asyncio
    async def test_delete_bad_id(self):
        with pytest.raises(ValueError):
            await _client().delete_post("../bad")

    @pytest.mark.asyncio
    async def test_api_error(self, monkeypatch):
        _wire(monkeypatch, lambda m, u, kw: _FakeResponse(500, {"error": {"message": "down"}}))
        with pytest.raises(api.FacebookAPIError) as ei:
            await _client().delete_post("123")
        assert ei.value.status_code == 502  # mapped
