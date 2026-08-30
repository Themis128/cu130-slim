"""Unit tests for the LinkedIn REST API client."""

import httpx
import pytest

from app.services import linkedin_api as api


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

    async def post(self, url, headers=None, json=None, content=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json, "content": content})
        return self._next_response()

    async def put(self, url, headers=None, content=None):
        self.calls.append({"method": "PUT", "url": url, "headers": headers, "content": content})
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
    return api.LinkedInAPIClient(access_token="tok-123")


@pytest.fixture
def no_sleep(monkeypatch):
    """Patch LinkedIn API sleeps so tests run fast."""

    async def _noop(_):
        return None

    monkeypatch.setattr(api.asyncio, "sleep", _noop)


@pytest.mark.asyncio
async def test_validate_token_success(monkeypatch, client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"sub": "user-1", "name": "Test User"}))
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=30.0: fake)

    result = await client.validate_token()

    assert result["sub"] == "user-1"
    assert fake.calls[0]["url"] == "https://api.linkedin.com/v2/userinfo"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-123"


@pytest.mark.asyncio
async def test_validate_token_raises_linkedin_api_error(monkeypatch, client):
    fake = _FakeAsyncClient(_FakeResponse(401, {"status": 401}))
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=30.0: fake)

    with pytest.raises(api.LinkedInAPIError) as exc_info:
        await client.validate_token()

    assert exc_info.value.status_code == 401
    assert "https://api.linkedin.com/v2/userinfo" in exc_info.value.url


@pytest.mark.asyncio
async def test_validate_token_5xx_maps_to_502(monkeypatch, client):
    fake = _FakeAsyncClient(_FakeResponse(500, {"status": 500}))
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=30.0: fake)

    with pytest.raises(api.LinkedInAPIError) as exc_info:
        await client.validate_token()

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_get_member_organizations(monkeypatch, client):
    acl = _FakeResponse(
        200,
        {
            "elements": [
                {
                    "organization": "urn:li:organization:12345",
                    "role": "ADMINISTRATOR",
                }
            ]
        },
    )
    org = _FakeResponse(
        200,
        {
            "localizedName": "cloudless.gr",
            "vanityName": "cloudlessgr",
        },
    )
    fake = _FakeAsyncClient([acl, org])
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=30.0: fake)

    orgs = await client.get_member_organizations()

    assert len(orgs) == 1
    assert orgs[0].id == "12345"
    assert orgs[0].name == "cloudless.gr"
    assert orgs[0].vanity_name == "cloudlessgr"
    assert orgs[0].role == "ADMINISTRATOR"


@pytest.mark.asyncio
async def test_get_member_organizations_uses_fallback_v2(monkeypatch, client):
    rest_acl = _FakeResponse(400, {"status": 400})
    v2_acl = _FakeResponse(
        200,
        {
            "elements": [
                {
                    "organizationalTarget": "urn:li:organization:67890",
                    "role": "ADMINISTRATOR",
                }
            ]
        },
    )
    org = _FakeResponse(200, {"name": {"localized": {"en_US": "Acme Inc"}}, "vanityName": "acme"})
    fake = _FakeAsyncClient([rest_acl, v2_acl, org])
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=30.0: fake)

    orgs = await client.get_member_organizations()

    assert orgs[0].id == "67890"
    assert orgs[0].name == "Acme Inc"


@pytest.mark.asyncio
async def test_get_post_analytics_tries_both_urn_forms(monkeypatch, client):
    # First attempt with ugcPosts fails, second with shares succeeds.
    fail = _FakeResponse(400, {"status": 400, "message": "not found"})
    ok = _FakeResponse(
        200,
        {
            "elements": [
                {
                    "ugcPost": "urn:li:ugcPost:111",
                    "totalShareStatistics": {
                        "impressionCount": 100,
                        "clickCount": 5,
                        "likeCount": 10,
                        "commentCount": 2,
                        "shareCount": 1,
                    },
                }
            ]
        },
    )
    fake = _FakeAsyncClient([fail, ok])
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=60.0: fake)

    stats = await client.get_post_analytics(
        "urn:li:ugcPost:111",
        "urn:li:organization:12345",
    )

    assert stats["totalShareStatistics"]["impressionCount"] == 100
    # One failed request + one successful request.
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_get_post_analytics_raises_on_5xx(monkeypatch, client):
    fake = _FakeAsyncClient(_FakeResponse(500, {"status": 500, "message": "internal error"}))
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=60.0: fake)

    with pytest.raises(api.LinkedInAPIError) as exc_info:
        await client.get_post_analytics(
            "urn:li:ugcPost:111",
            "urn:li:organization:12345",
        )

    assert exc_info.value.status_code == 502
    assert "internal error" in exc_info.value.response_text


@pytest.mark.asyncio
async def test_get_organization_lifetime_stats(monkeypatch, client):
    fake = _FakeAsyncClient(
        _FakeResponse(
            200,
            {
                "elements": [
                    {
                        "organizationalEntity": "urn:li:organization:12345",
                        "totalShareStatistics": {
                            "impressionCount": 1000,
                            "likeCount": 50,
                        },
                    }
                ]
            },
        )
    )
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=60.0: fake)

    stats = await client.get_organization_lifetime_stats("urn:li:organization:12345")
    assert stats["totalShareStatistics"]["impressionCount"] == 1000


@pytest.mark.asyncio
async def test_get_follower_count(monkeypatch, client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"first": {"totalSize": 42}}))
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=30.0: fake)

    count = await client.get_follower_count("urn:li:organization:12345")
    assert count == 42
    assert "networkSizes" in fake.calls[0]["url"]


@pytest.mark.asyncio
async def test_get_follower_count_404_returns_zero(monkeypatch, client):
    fake = _FakeAsyncClient(_FakeResponse(404, {"status": 404}))
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=30.0: fake)

    count = await client.get_follower_count("urn:li:organization:12345")
    assert count == 0


@pytest.mark.asyncio
async def test_create_post_success(monkeypatch, client):
    fake = _FakeAsyncClient(
        _FakeResponse(201, {}, headers={"x-restli-id": "urn:li:share:123"})
    )
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=60.0: fake)

    result = await client.create_post(
        author_urn="urn:li:organization:12345",
        commentary="Hello LinkedIn!",
    )

    assert result.success is True
    assert result.platform_post_id == "urn:li:share:123"
    assert result.platform_url == "https://www.linkedin.com/feed/update/urn:li:share:123"
    assert fake.calls[0]["json"]["commentary"] == "Hello LinkedIn!"
    assert fake.calls[0]["json"]["visibility"] == "PUBLIC"


@pytest.mark.asyncio
async def test_create_post_api_error_403(monkeypatch, client):
    fake = _FakeAsyncClient(_FakeResponse(403, {"status": 403, "message": "unauthorized"}))
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=60.0: fake)

    result = await client.create_post(
        author_urn="urn:li:organization:12345",
        commentary="Hello!",
    )

    assert result.success is False
    assert "403" in (result.error or "")


@pytest.mark.asyncio
async def test_create_post_api_error_400(monkeypatch, client):
    fake = _FakeAsyncClient(_FakeResponse(400, {"status": 400, "message": "invalid request"}))
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=60.0: fake)

    result = await client.create_post(
        author_urn="urn:li:organization:12345",
        commentary="Hello!",
    )

    assert result.success is False
    assert "400" in (result.error or "")


@pytest.mark.asyncio
async def test_create_comment_success(monkeypatch, client):
    fake = _FakeAsyncClient(
        _FakeResponse(201, {}, headers={"x-restli-id": "urn:li:comment:456"})
    )
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=60.0: fake)

    result = await client.create_comment(
        post_urn="urn:li:share:123",
        text="Great post!",
        creator_urn="urn:li:person:789",
    )

    assert result.success is True
    assert result.platform_post_id == "urn:li:comment:456"
    assert fake.calls[0]["json"]["actor"] == "urn:li:person:789"
    assert fake.calls[0]["json"]["message"]["text"] == "Great post!"


@pytest.mark.asyncio
async def test_create_article_truncates_long_text(monkeypatch, client):
    fake = _FakeAsyncClient(
        _FakeResponse(201, {}, headers={"x-restli-id": "urn:li:share:789"})
    )
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=60.0: fake)

    long_body = "word " * 2000
    result = await client.create_article(
        author_urn="urn:li:organization:12345",
        title="Long read",
        body=long_body,
    )

    assert result.success is True
    assert len(fake.calls[0]["json"]["commentary"]) <= api.MAX_COMMENTARY_CHARS


@pytest.mark.asyncio
async def test_delete_post_success(monkeypatch, client):
    fake = _FakeAsyncClient(_FakeResponse(204, {}))
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=60.0: fake)

    result = await client.delete_post("urn:li:share:123")

    assert result.success is True
    assert fake.calls[0]["method"] == "DELETE"
    assert "urn%3Ali%3Ashare%3A123" in fake.calls[0]["url"]


@pytest.mark.asyncio
async def test_create_multi_image_post_success(monkeypatch, client, tmp_path, no_sleep):
    img_a = tmp_path / "a.png"
    img_b = tmp_path / "b.png"
    img_a.write_bytes(b"image-a")
    img_b.write_bytes(b"image-b")

    responses = [
        # Image A: init, put, poll
        _FakeResponse(
            200,
            {"value": {"uploadUrl": "https://upload.test/a", "image": "urn:li:image:1"}},
        ),
        _FakeResponse(200, ""),
        _FakeResponse(200, {"status": "AVAILABLE"}),
        # Image B: init, put, poll
        _FakeResponse(
            200,
            {"value": {"uploadUrl": "https://upload.test/b", "image": "urn:li:image:2"}},
        ),
        _FakeResponse(200, ""),
        _FakeResponse(200, {"status": "AVAILABLE"}),
        # Final post
        _FakeResponse(201, {}, headers={"x-restli-id": "urn:li:share:multi1"}),
    ]
    fake = _FakeAsyncClient(responses)
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=120.0: fake)

    result = await client.create_multi_image_post(
        author_urn="urn:li:organization:12345",
        commentary="Carousel post",
        media_paths=[str(img_a), str(img_b)],
    )

    assert result.success is True
    assert result.platform_post_id == "urn:li:share:multi1"

    # Final post must use multiImage with both images
    final_call = fake.calls[-1]
    assert final_call["method"] == "POST"
    assert "posts" in final_call["url"]
    images = final_call["json"]["content"]["multiImage"]["images"]
    assert len(images) == 2
    assert images[0]["id"] == "urn:li:image:1"
    assert images[1]["id"] == "urn:li:image:2"
    assert images[0]["altText"] == "Slide 1"
    assert images[1]["altText"] == "Slide 2"

    # PUT calls carried the raw image bytes
    put_calls = [c for c in fake.calls if c["method"] == "PUT"]
    assert len(put_calls) == 2
    assert put_calls[0]["content"] == b"image-a"
    assert put_calls[1]["content"] == b"image-b"


@pytest.mark.asyncio
async def test_create_document_post_success(monkeypatch, client, no_sleep):
    pdf_bytes = b"%PDF-1.4 fake document"
    responses = [
        _FakeResponse(
            200,
            {"value": {"uploadUrl": "https://upload.test/doc", "document": "urn:li:document:1"}},
        ),
        _FakeResponse(200, ""),
        _FakeResponse(200, {"status": "AVAILABLE"}),
        _FakeResponse(201, {}, headers={"x-restli-id": "urn:li:share:doc1"}),
    ]
    fake = _FakeAsyncClient(responses)
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda timeout=120.0: fake)

    result = await client.create_document_post(
        author_urn="urn:li:organization:12345",
        commentary="Carousel post",
        pdf_bytes=pdf_bytes,
        title="My Carousel",
    )

    assert result.success is True
    assert result.platform_post_id == "urn:li:share:doc1"

    final_call = fake.calls[-1]
    assert final_call["method"] == "POST"
    assert final_call["json"]["content"]["media"]["id"] == "urn:li:document:1"
    assert final_call["json"]["content"]["media"]["title"] == "My Carousel"

    put_call = [c for c in fake.calls if c["method"] == "PUT"][0]
    assert put_call["content"] == pdf_bytes
