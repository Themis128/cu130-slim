# -*- coding: utf-8 -*-
"""Coverage gaps: instagrapi_client story uploads + retry-exhaust paths."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.instagrapi_client as IC


def _settings(**over):
    base = dict(INSTAGRAM_PROXY_POOL="", INSTAGRAM_PROXY="", INSTAGRAM_RATE_LIMIT_RPM=30, REDIS_URL="")
    base.update(over)
    return SimpleNamespace(**base)


def _client(monkeypatch, tmp_path, **sover):
    monkeypatch.setattr(IC, "get_settings", lambda: _settings(**sover))
    monkeypatch.setattr(IC, "_SESSION_DIR", tmp_path / ".ig")
    c = IC.InstagrapiClient("user", "pw", rate_limit=False)
    c._session_file = tmp_path / ".ig" / "user.json"
    return c


@pytest.mark.asyncio
async def test_story_uploads_and_own_stories(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    media = SimpleNamespace(id="m1", pk="p1", code="c1")

    class _CL:
        user_id = "uid"

        def photo_upload_to_story(self, path):
            return media

        def video_upload_to_story(self, path):
            return media

        def user_stories(self, uid):
            return [media]

    monkeypatch.setattr(c, "_ensure_client", lambda: _CL())
    monkeypatch.setattr(c, "_rate_limiter", SimpleNamespace(wait=AsyncMock()))

    out = await c.upload_photo_to_story("/tmp/x.jpg")
    assert out == {"id": "m1", "pk": "p1", "code": "c1"}
    out = await c.upload_video_to_story("/tmp/x.mp4")
    assert out["id"] == "m1"
    stories = await c.get_own_stories()
    assert stories == [{"id": "m1", "pk": "p1"}]


@pytest.mark.asyncio
async def test_sync_call_generic_error_raises(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(c, "_ensure_client", lambda: object())

    def boom(cl):
        raise RuntimeError("weird")

    with pytest.raises(IC.InstagrapiError, match="operation failed"):
        c._sync_call(boom)


def test_ensure_client_fresh_login_path(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    class _CL:
        def __init__(self):
            self.logged_in = False

        def set_proxy(self, p):
            pass

        def login(self, u, p):
            self.logged_in = True

        def set_settings(self, s):
            pass

        def get_settings(self):
            return {}

    fake = SimpleNamespace(Client=_CL)
    monkeypatch.setitem(sys.modules, "instagrapi", fake)
    cl = c._ensure_client()
    assert cl.logged_in is True
    assert c._client is cl


def test_ensure_client_login_failure_raises(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)

    class _CL:
        def set_proxy(self, p):
            pass

        def login(self, u, p):
            raise RuntimeError("bad creds")

    monkeypatch.setitem(sys.modules, "instagrapi", SimpleNamespace(Client=_CL))
    with pytest.raises(IC.InstagrapiError, match="login failed"):
        c._ensure_client()


def test_sync_call_zero_retries_raises(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    c._max_retries = 0
    with pytest.raises(IC.InstagrapiError, match="after 0 retries"):
        c._sync_call(lambda cl: None)


def test_build_client_no_proxy(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    c._proxies = [""]  # falsy proxy -> skip set_proxy branch

    class _CL:
        def set_proxy(self, p):
            raise AssertionError("should not be called")

    monkeypatch.setitem(sys.modules, "instagrapi", SimpleNamespace(Client=_CL))
    assert isinstance(c._build_client(), _CL)


def test_ensure_client_restores_session(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    c._session_file.parent.mkdir(parents=True, exist_ok=True)
    c._session_file.write_text("{}")

    class _CL:
        def __init__(self):
            self.settings = {}

        def set_proxy(self, p):
            pass

        def set_settings(self, s):
            self.settings = s

        def login(self, u, p):
            pass

    monkeypatch.setitem(sys.modules, "instagrapi", SimpleNamespace(Client=_CL))
    cl = c._ensure_client()
    assert c._client is cl
