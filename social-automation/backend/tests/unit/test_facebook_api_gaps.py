# -*- coding: utf-8 -*-
"""Coverage gaps: facebook_api reel/multi-photo/insights branch paths."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import facebook_api as api


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.headers = {}

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise api.FacebookAPIError(self.status_code, self.text, "u")


class _Client:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _next(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        return self._responses.pop(0) if self._responses else _Resp(500, "empty")

    async def get(self, url, **kw):
        return await self._next("GET", url, **kw)

    async def post(self, url, **kw):
        return await self._next("POST", url, **kw)


@pytest.fixture
def client():
    return api.FacebookAPIClient(access_token="tok", page_id="123456789")


# ── create_post link branch ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_post_with_and_without_link(client):
    fake = _Client(_Resp(200, {"id": "p1"}), _Resp(200, {"id": "p2"}))
    with patch("app.services.facebook_api.httpx.AsyncClient", return_value=fake):
        await client.create_post("hello", link="https://x.example")
        await client.create_post("world")
    assert fake.calls[0]["data"]["link"] == "https://x.example"
    assert "link" not in fake.calls[1]["data"]


# ── photo/video optional fields ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_photo_no_caption_video_no_description(client):
    fake = _Client(_Resp(200, {"id": "ph"}), _Resp(200, {"id": "vid"}))
    with patch("app.services.facebook_api.httpx.AsyncClient", return_value=fake):
        await client.create_photo_post("https://x/img.jpg")
        await client.create_video_post("https://x/v.mp4")
    assert "caption" not in fake.calls[0]["data"]
    assert "description" not in fake.calls[1]["data"]


# ── create_reel_post ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reel_requires_url(client):
    with pytest.raises(ValueError):
        await client.create_reel_post("")


@pytest.mark.asyncio
async def test_reel_start_missing_ids_raises(client):
    fake = _Client(_Resp(200, {"video_id": ""}))  # no upload_url either
    with patch("app.services.facebook_api.httpx.AsyncClient", return_value=fake):
        with pytest.raises(RuntimeError, match="video_reels start"):
            await client.create_reel_post("https://x/v.mp4")


@pytest.mark.asyncio
async def test_reel_full_flow(client):
    fake = _Client(
        _Resp(200, {"video_id": "vid-1", "upload_url": "https://upload.example/u"}),
        _Resp(200, {"ok": True}),
        _Resp(200, {"id": "post-9"}),
    )
    with patch("app.services.facebook_api.httpx.AsyncClient", return_value=fake):
        out = await client.create_reel_post("https://x/v.mp4", description="desc")
    assert out["id"] == "vid-1"
    assert fake.calls[1]["headers"]["file_url"] == "https://x/v.mp4"
    assert fake.calls[2]["data"]["video_state"] == "PUBLISHED"
    assert fake.calls[2]["data"]["description"] == "desc"


@pytest.mark.asyncio
async def test_reel_no_description(client):
    fake = _Client(
        _Resp(200, {"video_id": "vid-2", "upload_url": "https://upload.example/u"}),
        _Resp(200, {}),
        _Resp(200, {}),
    )
    with patch("app.services.facebook_api.httpx.AsyncClient", return_value=fake):
        await client.create_reel_post("https://x/v.mp4")
    assert "description" not in fake.calls[2]["data"]


# ── insights since/until branches ────────────────────────────────────────


@pytest.mark.asyncio
async def test_page_insights_no_dates(client):
    fake = _Client(_Resp(200, {"data": []}))
    with patch("app.services.facebook_api.httpx.AsyncClient", return_value=fake):
        await client.get_page_insights(metric="m", period="week")
    params = fake.calls[0]["params"]
    assert "since" not in params and "until" not in params


@pytest.mark.asyncio
async def test_post_insights(client):
    fake = _Client(_Resp(200, {"data": [{"name": "post_impressions"}]}))
    with patch("app.services.facebook_api.httpx.AsyncClient", return_value=fake):
        out = await client.get_post_insights("123456_789")
    assert out["data"][0]["name"] == "post_impressions"
    assert "123456_789/insights" in fake.calls[0]["url"]


@pytest.mark.asyncio
async def test_page_insights_until_only(client):
    fake = _Client(_Resp(200, {"data": []}))
    with patch("app.services.facebook_api.httpx.AsyncClient", return_value=fake):
        await client.get_page_insights(metric="m", period="day", until="2024-01-01")
    params = fake.calls[0]["params"]
    assert params.get("until") == "2024-01-01" and "since" not in params


@pytest.mark.asyncio
async def test_multi_photo_post_with_link(client):
    fake = _Client(
        _Resp(200, {"id": "ph1"}),
        _Resp(200, {"id": "post-multi"}),
    )
    with patch("app.services.facebook_api.httpx.AsyncClient", return_value=fake):
        out = await client.create_multi_photo_post(["https://x/1.jpg"], message="m", link="https://lnk")
    assert out["id"] == "post-multi"
    assert fake.calls[-1]["data"]["link"] == "https://lnk"
    assert "attached_media" in fake.calls[-1]["data"]


@pytest.mark.asyncio
async def test_multi_photo_post_no_link(client):
    fake = _Client(_Resp(200, {"id": "ph1"}), _Resp(200, {"id": "post-multi2"}))
    with patch("app.services.facebook_api.httpx.AsyncClient", return_value=fake):
        await client.create_multi_photo_post(["https://x/1.jpg"], message="m")
    assert "link" not in fake.calls[-1]["data"]
