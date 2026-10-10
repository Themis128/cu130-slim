"""Unit tests for the Bluesky (AT Protocol) client and facet builder."""

from unittest.mock import patch

import pytest

from app.services import bluesky_api as api


class _FakeResponse:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self, responses):
        if isinstance(responses, _FakeResponse):
            responses = [responses]
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def _next(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        return self._responses.pop(0)

    async def get(self, url, **kw):
        return await self._next("GET", url, **kw)

    async def post(self, url, **kw):
        return await self._next("POST", url, **kw)


@pytest.fixture
def client():
    return api.BlueskyClient("test.bsky.social", "xxxx-yyyy-zzzz-wwww")


# ---------- facets ----------


def test_facets_detects_url_with_byte_offsets():
    facets = api.build_facets("check https://cloudless.gr/pricing now")
    assert len(facets) == 1
    f = facets[0]
    assert f["features"][0]["$type"] == "app.bsky.richtext.facet#link"
    assert f["features"][0]["uri"] == "https://cloudless.gr/pricing"
    text = b"check https://cloudless.gr/pricing now"
    assert text[f["index"]["byteStart"]:f["index"]["byteEnd"]].decode() == "https://cloudless.gr/pricing"


def test_facets_byte_offsets_with_multibyte_prefix():
    text = "αβγ https://x.com y"  # Greek chars are 2-byte in UTF-8
    facets = api.build_facets(text)
    f = facets[0]
    assert text.encode()[f["index"]["byteStart"]:f["index"]["byteEnd"]].decode() == "https://x.com"


def test_facets_detects_hashtag_and_mention():
    facets = api.build_facets("hi @alice.bsky.social #buildinpublic")
    kinds = {f["features"][0]["$type"] for f in facets}
    assert "app.bsky.richtext.facet#tag" in kinds
    assert "app.bsky.richtext.facet#mention" in kinds


def test_facets_no_false_positive_on_email():
    facets = api.build_facets("mail me at a@b.co")  # a@b.co could look like @mention
    assert not any(
        f["features"][0]["$type"] == "app.bsky.richtext.facet#mention" for f in facets
    )


def test_facets_empty_text():
    assert api.build_facets("plain text nothing") == []


# ---------- client ----------


@pytest.mark.asyncio
async def test_create_session_stores_jwts(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {
        "accessJwt": "acc", "refreshJwt": "ref", "did": "did:plc:xyz",
        "handle": "test.bsky.social",
    }))
    with patch("app.services.bluesky_api.httpx.AsyncClient", return_value=fake):
        data = await client.create_session()

    assert data["did"] == "did:plc:xyz"
    assert client.did == "did:plc:xyz"
    call = fake.calls[0]
    assert call["url"].endswith("/xrpc/com.atproto.server.createSession")
    assert call["json"]["identifier"] == "test.bsky.social"


@pytest.mark.asyncio
async def test_create_session_error_raises(client):
    fake = _FakeAsyncClient(_FakeResponse(401, {"error": "AuthenticationRequired"}))
    with patch("app.services.bluesky_api.httpx.AsyncClient", return_value=fake):
        with pytest.raises(api.BlueskyAPIError) as excinfo:
            await client.create_session()
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_create_post_with_images_and_facets(client):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"accessJwt": "a", "refreshJwt": "r", "did": "did:plc:me"}),
        _FakeResponse(200, {"uri": "at://did:plc:me/app.bsky.feed.post/abc", "cid": "c1"}),
    ])
    with patch("app.services.bluesky_api.httpx.AsyncClient", return_value=fake):
        await client.create_session()
        result = await client.create_post(
            "see https://cloudless.gr",
            images=[{"blob": {"$link": "b1"}, "alt": "alt", "aspectRatio": {"width": 1, "height": 1}}],
        )

    assert result["uri"].endswith("/abc")
    record = fake.calls[1]["json"]["record"]
    assert record["$type"] == "app.bsky.feed.post"
    assert record["embed"]["$type"] == "app.bsky.embed.images"
    assert len(record["embed"]["images"]) == 1
    assert record["facets"][0]["features"][0]["$type"] == "app.bsky.richtext.facet#link"


@pytest.mark.asyncio
async def test_create_post_caps_images_at_four(client):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"accessJwt": "a", "refreshJwt": "r", "did": "did:plc:me"}),
        _FakeResponse(200, {"uri": "u", "cid": "c"}),
    ])
    with patch("app.services.bluesky_api.httpx.AsyncClient", return_value=fake):
        await client.create_session()
        await client.create_post("t", images=[{"blob": {}, "alt": ""}] * 6)
    assert len(fake.calls[1]["json"]["record"]["embed"]["images"]) == 4


@pytest.mark.asyncio
async def test_create_post_requires_session(client):
    with pytest.raises(api.BlueskyAPIError):
        await client.create_post("hi")
