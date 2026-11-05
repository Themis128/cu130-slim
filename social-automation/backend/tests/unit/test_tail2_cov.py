"""Tail coverage — media_sources helpers + media_enhance add_background."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.asyncio


async def test_nextcloud_client(monkeypatch):
    import app.services.media_sources as MS
    s = SimpleNamespace(NEXTCLOUD_USERNAME="u", NEXTCLOUD_APP_PASSWORD="p")
    monkeypatch.setattr(MS, "get_settings", lambda: s)
    c = MS._client()
    assert c.auth == ("u", "p")
    await c.aclose()


async def test_gdrive_exchange_code(monkeypatch):
    import app.services.media_sources as MS
    s = SimpleNamespace(GOOGLE_PHOTOS_CLIENT_ID="id", GOOGLE_PHOTOS_CLIENT_SECRET="sec")
    monkeypatch.setattr(MS, "get_settings", lambda: s)
    resp = SimpleNamespace(json=lambda: {"refresh_token": "rt"})
    cls = AsyncMock()
    inst = cls.return_value.__aenter__.return_value
    inst.post = AsyncMock(return_value=resp)
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: cls(**kw))
    out = await MS.gdrive_exchange_code("code", "uri")
    assert out == "rt"


async def test_add_background_endpoint(monkeypatch):
    import app.api.media_enhance as ME
    asset = SimpleNamespace(id="a1", team_id="t1", user_id="u1", mime_type="image/png")
    monkeypatch.setattr(ME, "_get_asset", AsyncMock(return_value=asset))
    monkeypatch.setattr(ME, "_load_asset_bytes", AsyncMock(return_value=b"png"))
    monkeypatch.setattr(ME, "add_background", lambda *a, **k: (b"out", "image/png"))
    monkeypatch.setattr(ME, "_stream_response", lambda b, m: {"bytes": b, "mime": m})
    out = await ME.add_background(
        "a1",
        SimpleNamespace(style="gradient", color_top=None, color_bottom=None, spotlight=False),
        SimpleNamespace(),
        SimpleNamespace(),
    )
    assert out["mime"] == "image/png"
