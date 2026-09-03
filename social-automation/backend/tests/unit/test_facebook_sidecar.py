"""Unit tests for the Facebook Browser Automation sidecar client."""

from unittest.mock import patch

import pytest

from app.services import facebook_sidecar as fb


class _FakeResponse:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("response body is not JSON")

    @property
    def text(self) -> str:
        return str(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self
            )


class _FakeAsyncClient:
    """httpx.AsyncClient stand-in that records requests and returns preset responses."""

    def __init__(self, responses=None):
        if isinstance(responses, _FakeResponse):
            responses = [responses]
        self._responses = list(responses or [])
        self._idx = 0
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kw):
        self.calls.append({"method": "GET", "url": url})
        return self._next()

    async def post(self, url, json=None, **kw):
        self.calls.append({"method": "POST", "url": url, "json": json})
        return self._next()

    def _next(self):
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            return r
        return _FakeResponse(200, {"status": "ok"})


@pytest.fixture
def client():
    return fb.FacebookSidecarClient(base_url="http://test:9226")


@pytest.mark.asyncio
async def test_health(client):
    with patch.object(fb.httpx, "AsyncClient", return_value=_FakeAsyncClient(
        _FakeResponse(200, {"status": "ok", "service": "facebook-browser-sidecar"})
    )):
        result = await client.health()
        assert result["status"] == "ok"
        assert result["service"] == "facebook-browser-sidecar"


@pytest.mark.asyncio
async def test_set_session(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": "ok", "logged_in": True}))
    with patch.object(fb.httpx, "AsyncClient", return_value=fake):
        result = await client.set_session({"cookies": [], "origins": []})
        assert result["logged_in"] is True
        assert fake.calls[0]["url"] == "/session"
        assert fake.calls[0]["json"]["storage_state"]["cookies"] == []


@pytest.mark.asyncio
async def test_check_session(client):
    with patch.object(fb.httpx, "AsyncClient", return_value=_FakeAsyncClient(
        _FakeResponse(200, {"status": "ok", "logged_in": True})
    )):
        result = await client.check_session()
        assert result["logged_in"] is True


@pytest.mark.asyncio
async def test_get_profile(client):
    with patch.object(fb.httpx, "AsyncClient", return_value=_FakeAsyncClient(
        _FakeResponse(200, {"status": "ok", "profile": {"name": "Test User"}})
    )):
        result = await client.get_profile()
        assert result["profile"]["name"] == "Test User"


@pytest.mark.asyncio
async def test_update_bio(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": "ok", "updated": ["bio"]}))
    with patch.object(fb.httpx, "AsyncClient", return_value=fake):
        result = await client.update_bio("New bio text")
        assert result["updated"] == ["bio"]
        assert fake.calls[0]["json"]["bio"] == "New bio text"


@pytest.mark.asyncio
async def test_upload_picture(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": "ok", "updated": ["profile_picture"]}))
    with patch.object(fb.httpx, "AsyncClient", return_value=fake):
        result = await client.upload_picture(b"\x89PNGfake", "pic.png")
        assert result["updated"] == ["profile_picture"]
        # base64 should be in the payload
        import base64

        expected_b64 = base64.b64encode(b"\x89PNGfake").decode()
        assert fake.calls[0]["json"]["image_base64"] == expected_b64
        assert fake.calls[0]["json"]["filename"] == "pic.png"


@pytest.mark.asyncio
async def test_post_text(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": "ok", "posted": True}))
    with patch.object(fb.httpx, "AsyncClient", return_value=fake):
        result = await client.post_text("Hello world", privacy="public")
        assert result["posted"] is True
        assert fake.calls[0]["json"]["message"] == "Hello world"
        assert fake.calls[0]["json"]["privacy"] == "public"


@pytest.mark.asyncio
async def test_post_photo(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": "ok", "posted": True, "photo_count": 2}))
    images = [
        {"image_base64": "abc", "filename": "a.jpg"},
        {"image_base64": "def", "filename": "b.jpg"},
    ]
    with patch.object(fb.httpx, "AsyncClient", return_value=fake):
        result = await client.post_photo(images, message="Caption", privacy="friends")
        assert result["posted"] is True
        assert fake.calls[0]["json"]["images"] == images
        assert fake.calls[0]["json"]["message"] == "Caption"
        assert fake.calls[0]["json"]["privacy"] == "friends"


@pytest.mark.asyncio
async def test_post_link(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": "ok", "posted": True}))
    with patch.object(fb.httpx, "AsyncClient", return_value=fake):
        result = await client.post_link("https://cloudless.gr", message="Check this out")
        assert result["posted"] is True
        assert fake.calls[0]["json"]["url"] == "https://cloudless.gr"
        assert fake.calls[0]["json"]["message"] == "Check this out"


@pytest.mark.asyncio
async def test_post_video(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": "ok", "posted": True}))
    with patch.object(fb.httpx, "AsyncClient", return_value=fake):
        result = await client.post_video(b"\x00\x00\x00\x18ftyp", "clip.mp4", message="My video")
        assert result["posted"] is True
        import base64

        expected_b64 = base64.b64encode(b"\x00\x00\x00\x18ftyp").decode()
        assert fake.calls[0]["json"]["video_base64"] == expected_b64
        assert fake.calls[0]["json"]["filename"] == "clip.mp4"


@pytest.mark.asyncio
async def test_list_pages(client):
    with patch.object(fb.httpx, "AsyncClient", return_value=_FakeAsyncClient(
        _FakeResponse(200, {"status": "ok", "pages": [{"name": "My Page", "page_id": "123"}]})
    )):
        result = await client.list_pages()
        assert len(result["pages"]) == 1
        assert result["pages"][0]["page_id"] == "123"


@pytest.mark.asyncio
async def test_use_page(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": "ok", "active_page_id": "123"}))
    with patch.object(fb.httpx, "AsyncClient", return_value=fake):
        result = await client.use_page("123")
        assert result["active_page_id"] == "123"
        assert fake.calls[0]["url"] == "/page/123/use"


@pytest.mark.asyncio
async def test_page_post_text(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"status": "ok", "posted": True}))
    with patch.object(fb.httpx, "AsyncClient", return_value=fake):
        result = await client.page_post_text("Page post")
        assert result["posted"] is True
        assert fake.calls[0]["json"]["message"] == "Page post"


@pytest.mark.asyncio
async def test_error_raises(client):
    with patch.object(fb.httpx, "AsyncClient", return_value=_FakeAsyncClient(
        _FakeResponse(401, {"error": "Not logged in"})
    )):
        with pytest.raises(fb.FacebookSidecarError) as exc_info:
            await client.get_profile()
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_default_base_url_from_env():
    with patch.dict("os.environ", {"FACEBOOK_BROWSER_SIDECAR_URL": "http://custom:9999"}):
        c = fb.FacebookSidecarClient()
        assert c.base_url == "http://custom:9999"
