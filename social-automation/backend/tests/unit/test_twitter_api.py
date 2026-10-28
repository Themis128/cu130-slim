"""Unit tests for the Twitter/X REST API client."""

from unittest.mock import patch

import httpx
import pytest

from app.services import twitter_api as api


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

    def __init__(self, responses=None):
        if isinstance(responses, _FakeResponse):
            responses = [responses]
        self._responses = list(responses or [])
        self._call_index = 0
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers, "params": params})
        return self._next_response()

    async def post(self, url, headers=None, json=None, content=None, files=None, data=None):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "headers": headers,
                "json": json,
                "content": content,
                "files": files,
                "data": data,
            }
        )
        return self._next_response()

    async def delete(self, url, headers=None):
        self.calls.append({"method": "DELETE", "url": url, "headers": headers})
        return self._next_response()

    def _next_response(self):
        if self._call_index >= len(self._responses):
            return _FakeResponse(500, "out of preset responses")
        resp = self._responses[self._call_index]
        self._call_index += 1
        return resp


@pytest.fixture
def client():
    return api.TwitterAPIClient(access_token="tok-123")


@pytest.mark.asyncio
async def test_validate_token_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": {"id": "12345", "name": "Test User"}}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.validate_token()

    assert result["data"]["id"] == "12345"
    assert fake.calls[0]["url"] == "https://api.x.com/2/users/me"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_validate_token_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(401, {"status": 401, "title": "Unauthorized"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 401
    assert "https://api.x.com/2/users/me" in exc_info.value.url


@pytest.mark.asyncio
async def test_validate_token_5xx_maps_to_502(client):
    fake = _FakeAsyncClient(_FakeResponse(500, {"status": 500, "title": "Internal error"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.validate_token()

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_create_tweet_success(client):
    fake = _FakeAsyncClient(_FakeResponse(201, {"data": {"id": "111", "text": "Hello"}}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_tweet("Hello")

    assert result["data"]["id"] == "111"
    assert fake.calls[0]["url"] == "https://api.x.com/2/tweets"
    assert fake.calls[0]["json"]["text"] == "Hello"
    assert "media" not in fake.calls[0]["json"]


@pytest.mark.asyncio
async def test_create_tweet_with_media(client):
    fake = _FakeAsyncClient(_FakeResponse(201, {"data": {"id": "222"}}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        await client.create_tweet("With media", media_ids=["m1", "m2"])

    payload = fake.calls[0]["json"]
    assert payload["media"]["media_ids"] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_create_tweet_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(403, {"status": 403, "detail": "Forbidden"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.create_tweet("Hello")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_tweet_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"deleted": True}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.delete_tweet("1234567890")

    assert result is True
    assert fake.calls[0]["method"] == "DELETE"
    assert fake.calls[0]["url"] == "https://api.x.com/2/tweets/1234567890"


@pytest.mark.asyncio
async def test_delete_tweet_not_found_returns_false(client):
    fake = _FakeAsyncClient(_FakeResponse(404, {"errors": [{"message": "Not found"}]}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.delete_tweet("1234567890")

    assert result is False


@pytest.mark.asyncio
async def test_delete_tweet_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(401, {"status": 401, "title": "Unauthorized"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.delete_tweet("1234567890")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_tweet_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "data": {
                    "id": "1234567890",
                    "text": "A tweet",
                    "public_metrics": {"like_count": 5},
                }
            },
        )
    )

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_tweet("1234567890")

    assert result["data"]["id"] == "1234567890"
    assert fake.calls[0]["url"] == "https://api.x.com/2/tweets/1234567890"
    assert fake.calls[0]["params"]["tweet.fields"] == "created_at,public_metrics,entities"


@pytest.mark.asyncio
async def test_get_tweet_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(404, {"status": 404, "detail": "Not found"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.get_tweet("1234567890")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_upload_media_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": {"id": "123"}}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        media_id = await client.upload_media(b"image-bytes", media_category="tweet_image")

    assert media_id == "123"
    assert fake.calls[0]["url"] == api.X_MEDIA_UPLOAD_URL == "https://api.x.com/2/media/upload"
    assert fake.calls[0]["files"]["media"][1] == b"image-bytes"
    assert fake.calls[0]["data"]["media_category"] == "tweet_image"


@pytest.mark.asyncio
async def test_upload_media_raises_when_no_id_returned(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.upload_media(b"image-bytes")

    assert "Media upload returned no media id" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_upload_media_raises_on_4xx(client):
    fake = _FakeAsyncClient(_FakeResponse(400, {"errors": [{"message": "Invalid media"}]}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as exc_info:
            await client.upload_media(b"image-bytes")

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_user_tweets_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "data": [{"id": "1", "text": "tweet 1"}],
                "meta": {"result_count": 1},
            },
        )
    )

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_user_tweets("12345", max_results=5)

    assert result["data"][0]["id"] == "1"
    assert fake.calls[0]["url"] == "https://api.x.com/2/users/12345/tweets"
    assert fake.calls[0]["params"]["max_results"] == 5


@pytest.mark.asyncio
async def test_get_user_tweets_invalid_max_results(client):
    with pytest.raises(ValueError):
        await client.get_user_tweets("12345", max_results=200)


@pytest.mark.asyncio
async def test_create_tweet_with_reply(client):
    fake = _FakeAsyncClient(_FakeResponse(201, {"data": {"id": "111"}}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.create_tweet("Hello", reply_tweet_id="999")

    assert result["data"]["id"] == "111"
    assert fake.calls[0]["url"] == "https://api.x.com/2/tweets"
    assert fake.calls[0]["json"]["text"] == "Hello"
    assert fake.calls[0]["json"]["reply"]["in_reply_to_tweet_id"] == "999"


@pytest.mark.asyncio
async def test_get_me_metrics_requests_public_metrics(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": {"id": "1", "username": "x", "public_metrics": {"followers_count": 3}}}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_me_metrics()

    assert result["data"]["public_metrics"]["followers_count"] == 3
    assert fake.calls[0]["url"] == "https://api.x.com/2/users/me"
    assert "public_metrics" in fake.calls[0]["params"]["user.fields"]


@pytest.mark.asyncio
async def test_get_user_by_username_strips_at_and_encodes_fields(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"data": {"id": "9", "username": "someone"}}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        result = await client.get_user_by_username("@someone")

    assert result["data"]["username"] == "someone"
    assert fake.calls[0]["url"] == "https://api.x.com/2/users/by/username/someone"


@pytest.mark.asyncio
async def test_get_user_by_username_requires_name(client):
    with pytest.raises(ValueError):
        await client.get_user_by_username("  ")


@pytest.mark.asyncio
async def test_get_user_by_username_propagates_api_error(client):
    fake = _FakeAsyncClient(_FakeResponse(402, {"title": "Payment Required", "detail": "credits depleted"}))

    with patch("app.services.twitter_api.httpx.AsyncClient") as mock_client:
        mock_client.return_value = fake
        with pytest.raises(api.TwitterAPIError) as excinfo:
            await client.get_user_by_username("someone")

    assert excinfo.value.status_code == 402


# ── coverage: validators + auth helpers ───────────────────────────────


def test_validate_ids():
    with pytest.raises(ValueError, match="empty"):
        api._validate_tweet_id("  ")
    with pytest.raises(ValueError, match="Invalid Twitter tweet ID"):
        api._validate_tweet_id("abc!")
    assert api._validate_tweet_id(" 123 ") == "123"

    with pytest.raises(ValueError, match="empty"):
        api._validate_user_id("")
    with pytest.raises(ValueError, match="Invalid Twitter user ID"):
        api._validate_user_id("x")
    assert api._validate_user_id("42") == "42"


def test_client_requires_token_and_media_signer():
    with pytest.raises(ValueError, match="access token is required"):
        api.TwitterAPIClient(access_token="")

    c = api.TwitterAPIClient(
        access_token="tok",
        media_signer=lambda method, url, params=None: f"SIG {method} {url}",
    )
    auth = c._media_auth("POST", "https://api.x.com/2/media/upload")
    assert auth["Authorization"].startswith("SIG POST")

    c2 = api.TwitterAPIClient(access_token="tok")
    assert c2._media_auth("GET", "u")["Authorization"] == "Bearer tok"


def test_log_api_error_and_headers_except(client, caplog):
    # _log_api_error logs for >=400, no-op below
    client._log_api_error("u", _FakeResponse(200, {}))
    client._log_api_error("u?token=secret", _FakeResponse(500, "body"))

    # _raise_for_status with a response whose .headers raises -> hdrs = {}
    class _NoHeaders:
        status_code = 500
        text = "x"

        @property
        def headers(self):
            raise RuntimeError("no headers")

        def json(self):
            return {}

    with pytest.raises(api.TwitterAPIError):
        client._raise_for_status(_NoHeaders(), "u")


# ── create_tweet guards + upload_media guards ─────────────────────────


@pytest.mark.asyncio
async def test_create_tweet_validation(client):
    with pytest.raises(ValueError, match="required"):
        await client.create_tweet("   ")
    with pytest.raises(ValueError, match="at most 4"):
        await client.create_tweet("t", media_ids=["1", "2", "3", "4", "5"])


@pytest.mark.asyncio
async def test_upload_media_empty(client):
    with pytest.raises(ValueError, match="empty"):
        await client.upload_media(b"")


# ── chunked upload ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_media_chunked_validation(client):
    with pytest.raises(ValueError, match="empty"):
        await client.upload_media_chunked(b"", "video/mp4", "tweet_video")
    with pytest.raises(ValueError, match="chunk_size"):
        await client.upload_media_chunked(b"x", "video/mp4", "tweet_video", chunk_size=0)
    with pytest.raises(ValueError, match="chunk_size"):
        await client.upload_media_chunked(b"x", "video/mp4", "tweet_video", chunk_size=9 * 1024 * 1024)


@pytest.mark.asyncio
async def test_upload_media_chunked_success(client):
    # init -> append(x2 for two chunks) -> finalize (no processing_info -> done)
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": {"id": "999"}}),  # initialize
            _FakeResponse(200, {}),  # append 0
            _FakeResponse(200, {}),  # append 1
            _FakeResponse(200, {"data": {"processing_info": None}}),  # finalize
        ]
    )
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        media_id = await client.upload_media_chunked(b"x" * 10, "video/mp4", "tweet_video", chunk_size=5)

    assert media_id == "999"
    assert fake.calls[0]["url"].endswith("/media/upload/initialize")
    assert fake.calls[0]["json"]["total_bytes"] == 10
    assert fake.calls[1]["data"]["segment_index"] == "0"
    assert fake.calls[3]["url"].endswith("/999/finalize")


@pytest.mark.asyncio
async def test_upload_media_chunked_poll_then_done(client, monkeypatch):
    monkeypatch.setattr(api.asyncio, "sleep", __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock())
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": {"id": "999"}}),  # init
            _FakeResponse(200, {}),  # append
            _FakeResponse(200, {"data": {"processing_info": {"state": "in_progress", "check_after_secs": 0}}}),  # finalize
            _FakeResponse(200, {"data": {"processing_info": {"state": "succeeded"}}}),  # status poll
        ]
    )
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        media_id = await client.upload_media_chunked(b"v", "video/mp4", "tweet_video")
    assert media_id == "999"
    assert fake.calls[3]["params"]["command"] == "STATUS"


@pytest.mark.asyncio
async def test_upload_media_chunked_processing_failed(client):
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": {"id": "999"}}),
            _FakeResponse(200, {}),
            _FakeResponse(200, {"data": {"processing_info": {"state": "failed", "error": {"message": "bad codec"}}}}),
        ]
    )
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        with pytest.raises(api.TwitterAPIError, match="bad codec"):
            await client.upload_media_chunked(b"v", "video/mp4", "tweet_video")


@pytest.mark.asyncio
async def test_upload_media_chunked_timeout(client):
    fake = _FakeAsyncClient(
        [
            _FakeResponse(200, {"data": {"id": "999"}}),
            _FakeResponse(200, {}),
            _FakeResponse(200, {"data": {"processing_info": {"state": "pending"}}}),
        ]
    )
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        with pytest.raises(api.TwitterAPIError, match="still processing"):
            await client.upload_media_chunked(b"v", "video/mp4", "tweet_video", processing_timeout=-1)


@pytest.mark.asyncio
async def test_upload_media_chunked_no_media_id(client):
    fake = _FakeAsyncClient([_FakeResponse(200, {"data": {}})])
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        with pytest.raises(api.TwitterAPIError, match="no media id"):
            await client.upload_media_chunked(b"v", "video/mp4", "tweet_video")


# ── set_media_alt_text ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_media_alt_text(client):
    # invalid id -> ValueError
    with pytest.raises(ValueError):
        await client.set_media_alt_text("bad!", "alt")
    # empty alt text -> early return, no HTTP
    await client.set_media_alt_text("123", "   ")

    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        await client.set_media_alt_text("123", "a" * 1200)
    assert fake.calls[0]["url"] == api.X_MEDIA_METADATA_URL
    sent = fake.calls[0]["json"]["metadata"]["alt_text"]["text"]
    assert len(sent) == api.X_ALT_TEXT_MAX

    # api_base override path
    c = api.TwitterAPIClient(access_token="t", api_base="https://custom/2")
    fake2 = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake2
        await c.set_media_alt_text("1", "alt")
    assert fake2.calls[0]["url"] == "https://custom/2/media/metadata"


# ── DM methods ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_dm(client):
    with pytest.raises(ValueError, match="Invalid Twitter user ID"):
        await client.send_dm("bad!", "hi")
    with pytest.raises(ValueError, match="required"):
        await client.send_dm("123", "")
    with pytest.raises(ValueError, match="10000"):
        await client.send_dm("123", "x" * 10001)

    fake = _FakeAsyncClient(_FakeResponse(200, {"dm_event_id": "e1"}))
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        out = await client.send_dm("123", "hi")
    assert out["dm_event_id"] == "e1"
    assert fake.calls[0]["url"] == ("https://api.x.com/2/dm_conversations/with/123/messages")


@pytest.mark.asyncio
async def test_send_dm_to_conversation(client):
    with pytest.raises(ValueError, match="conversation_id"):
        await client.send_dm_to_conversation("", "hi")
    with pytest.raises(ValueError, match="required"):
        await client.send_dm_to_conversation("c1", "")

    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": 1}))
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        out = await client.send_dm_to_conversation("c1", "hi")
    assert out["ok"] == 1
    assert "/dm_conversations/c1/messages" in fake.calls[0]["url"]


@pytest.mark.asyncio
async def test_list_dm_events(client):
    with pytest.raises(ValueError, match="between 1 and 100"):
        await client.list_dm_events(max_results=0)
    with pytest.raises(ValueError, match="between 1 and 100"):
        await client.list_dm_events(max_results=101)

    fake = _FakeAsyncClient(_FakeResponse(200, {"data": []}))
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        out = await client.list_dm_events(max_results=10)
    assert out["data"] == []
    assert fake.calls[0]["params"]["max_results"] == 10
    assert "event_types" in fake.calls[0]["params"]


@pytest.mark.asyncio
async def test_get_dm_conversation_events(client):
    with pytest.raises(ValueError, match="conversation_id"):
        await client.get_dm_conversation_events("")
    with pytest.raises(ValueError, match="between 1 and 100"):
        await client.get_dm_conversation_events("c1", max_results=0)

    fake = _FakeAsyncClient(_FakeResponse(200, {"data": [{"id": "e"}]}))
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        out = await client.get_dm_conversation_events("c1", max_results=5)
    assert out["data"][0]["id"] == "e"
    assert "/dm_conversations/c1/dm_events" in fake.calls[0]["url"]


@pytest.mark.asyncio
async def test_delete_dm(client):
    with pytest.raises(ValueError, match="event_id"):
        await client.delete_dm("")

    fake = _FakeAsyncClient(_FakeResponse(204, {}))
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake
        assert await client.delete_dm("e1") is True
    assert fake.calls[0]["method"] == "DELETE"
    assert "/dm_events/e1" in fake.calls[0]["url"]

    # non-204 success path (e.g. 200) -> raise_for_status passes, returns True
    fake2 = _FakeAsyncClient(_FakeResponse(200, {}))
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake2
        assert await client.delete_dm("e2") is True

    # error -> propagates
    fake3 = _FakeAsyncClient(_FakeResponse(404, {}))
    with patch("app.services.twitter_api.httpx.AsyncClient") as mock:
        mock.return_value = fake3
        with pytest.raises(api.TwitterAPIError):
            await client.delete_dm("e3")
