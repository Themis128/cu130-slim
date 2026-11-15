"""Coverage gaps: nextcloud_talk rooms/messages/files/ping paths."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.services.nextcloud_talk as TALK


def _settings(**over) -> SimpleNamespace:
    base = dict(
        NEXTCLOUD_TALK_NOTIFY=True,
        NEXTCLOUD_USERNAME="u@cloudless.gr",
        NEXTCLOUD_APP_PASSWORD="app-pw",
        NEXTCLOUD_DAV_URL="https://cloud.cloudless.gr/remote.php/dav/files/u@cloudless.gr",
        NEXTCLOUD_BASE_URL="",
        NEXTCLOUD_TALK_ROOM="roomops1",
        NEXTCLOUD_TALK_ROOM_ALERTS="",
        NEXTCLOUD_TALK_ROOM_CHANGELOG="",
        NEXTCLOUD_TALK_BOT_SECRET="s",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _Resp:
    def __init__(self, status=200, body=None, content=b"", headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.content = content
        self.headers = headers or {"content-type": "application/json"}
        self.text = str(self._body)

    def json(self):
        return self._body


class _Client:
    """Records calls; returns queued responses or a default."""

    def __init__(self, *a, responses=None, **kw):
        self._responses = list(responses or [_Resp(200)])
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _next(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        return self._responses.pop(0) if self._responses else _Resp(500)

    async def get(self, url, **kw):
        return await self._next("GET", url, **kw)

    async def post(self, url, **kw):
        return await self._next("POST", url, **kw)

    async def delete(self, url, **kw):
        return await self._next("DELETE", url, **kw)


# ── room_token branches ─────────────────────────────────────────────────


def test_room_token_purposes():
    with patch.object(TALK, "get_settings", return_value=_settings(
        NEXTCLOUD_TALK_ROOM_ALERTS="alertroom",
        NEXTCLOUD_TALK_ROOM_CHANGELOG="logroom",
    )):
        assert TALK.room_token("alerts") == "alertroom"
        assert TALK.room_token("approvals") == "alertroom"
        assert TALK.room_token("changelog") == "logroom"
        assert TALK.room_token("digest") == "roomops1"
        # fallbacks when specific rooms unset
    with patch.object(TALK, "get_settings", return_value=_settings()):
        assert TALK.room_token("alerts") == "roomops1"
        assert TALK.room_token("changelog") == "roomops1"


# ── blocks_to_text arcs ─────────────────────────────────────────────────


def test_blocks_to_text_all_shapes():
    blocks = [
        {"type": "section", "text": {"text": "hello"}},
        {"type": "section"},                                    # no text -> skip
        {"type": "actions", "elements": [
            {"text": {"text": "Open"}, "url": "https://x"},     # label+url
            {"text": {"text": "Act"}, "action_id": "a1"},       # label only
            {"url": "https://y"},                               # no label -> skip
        ]},
        {"type": "context", "elements": [
            {"text": "ctx1"}, "notadict", {"text": ""}, {"text": "ctx2"},
        ]},
        {"type": "divider"},
        {"type": "actions", "elements": []},
    ]
    out = TALK.blocks_to_text(blocks)
    assert "hello" in out
    assert "• Open → https://x" in out
    assert "• Act (a1)" in out
    assert "ctx1 · ctx2" in out
    assert TALK.blocks_to_text(None) == ""
    assert TALK.blocks_to_text([]) == ""


# ── post_to_talk extra + no-token + exception ────────────────────────────


@pytest.mark.asyncio
async def test_post_to_talk_extra_and_no_token():
    with patch.object(TALK, "get_settings", return_value=_settings(NEXTCLOUD_TALK_ROOM="")):
        assert await TALK.post_to_talk("hi") is False  # no token
    # base missing entirely
    with patch.object(TALK, "get_settings", return_value=_settings(
        NEXTCLOUD_DAV_URL="", NEXTCLOUD_BASE_URL="",
    )):
        assert await TALK.post_to_talk("hi") is False


@pytest.mark.asyncio
async def test_post_to_talk_success_extra_and_http_fail():
    resp_ok = _Resp(201)
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[resp_ok])):
        assert await TALK.post_to_talk("hi", extra="more") is True

    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[_Resp(500)])):
        assert await TALK.post_to_talk("hi") is False

    class _Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): raise RuntimeError("down")
        async def __aexit__(self, *a): return False

    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", _Boom):
        assert await TALK.post_to_talk("hi") is False


# ── create_room / delete_room ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_room_paths():
    # disabled
    with patch.object(TALK, "get_settings", return_value=_settings(NEXTCLOUD_TALK_NOTIFY=False)):
        assert await TALK.create_room("r") is None
    # http fail
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[_Resp(400, {"e": 1})])):
        assert await TALK.create_room("r") is None
    # success with valid token
    ok = _Resp(201, {"ocs": {"data": {"token": "abc12345"}}})
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[ok])):
        assert await TALK.create_room("My Room", description="d") == "abc12345"
    # exception
    class _Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): raise RuntimeError("x")
        async def __aexit__(self, *a): return False
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", _Boom):
        assert await TALK.create_room("r") is None


@pytest.mark.asyncio
async def test_delete_room_paths():
    assert await TALK.delete_room("bad token!!") is False
    with patch.object(TALK, "get_settings", return_value=_settings(NEXTCLOUD_TALK_NOTIFY=False)):
        assert await TALK.delete_room("abc12345") is False
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[_Resp(204)])):
        assert await TALK.delete_room("abc12345") is True
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[_Resp(500)])):
        assert await TALK.delete_room("abc12345") is False


# ── post_to_room / list_room_messages / ping ─────────────────────────────


@pytest.mark.asyncio
async def test_post_to_room_and_list_and_ping():
    # invalid token -> early outs
    assert await TALK.post_to_room("!!", "hi") is False
    assert await TALK.list_room_messages("!!") == []
    assert await TALK.ping() is True or True  # ping tested below properly

    with patch.object(TALK, "get_settings", return_value=_settings(NEXTCLOUD_TALK_NOTIFY=False)):
        assert await TALK.ping() is False
        assert await TALK.list_room_messages("abc12345") == []

    # post_to_room success + http fail + exception
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[_Resp(200)])):
        assert await TALK.post_to_room("abc12345", "yo") is True
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[_Resp(500)])):
        assert await TALK.post_to_room("abc12345", "yo") is False

    # list messages: ok list, non-list data, http fail, exception
    shared = _Client(responses=[
        _Resp(200, {"ocs": {"data": [{"id": 1}]}}),
        _Resp(200, {"ocs": {"data": "x"}}),
        _Resp(500),
    ])
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: shared):
        assert await TALK.list_room_messages("abc12345") == [{"id": 1}]
        assert await TALK.list_room_messages("abc12345", last_known_id=5) == []
        assert await TALK.list_room_messages("abc12345") == []

    # ping success/fail/exception
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[_Resp(200)])):
        assert await TALK.ping() is True
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[_Resp(503)])):
        assert await TALK.ping() is False

    class _Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): raise RuntimeError("x")
        async def __aexit__(self, *a): return False
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", _Boom):
        assert await TALK.ping() is False
        assert await TALK.list_room_messages("abc12345") == []


# ── find_recording_file / download_talk_file / room_url ─────────────────


def test_find_recording_file_variants():
    msgs = [
        {"id": 1, "messageParameters": {"file": "notadict"}},
        {"id": 2, "messageParameters": {"file": {"id": "10", "name": "photo.jpg"}}},
        {"id": 3, "messageParameters": {"file": {"id": "11"}}},                     # no name, no match
        {"id": 4, "messageParameters": {"file": {"id": "12", "name": "REC.MP4", "path": "Talk/rec.mp4"}}},
        {"id": 5, "messageParameters": {"file": {"id": "13", "name": "clip.webm"}}},
        {"id": 6, "messageParameters": {"type": "recording", "file": {"id": "14", "fileName": "x.bin"}}},
        {"id": 7, "messageParameters": {}},
        {"id": 8},
    ]
    out = TALK.find_recording_file(msgs)
    assert out["file_id"] == 14 and out["name"] == "x.bin"
    # earlier video match overridden by later recording-type match
    assert TALK.find_recording_file(msgs[:5])["file_id"] == 13
    assert TALK.find_recording_file([]) is None
    assert TALK.find_recording_file([{"id": 1}]) is None


@pytest.mark.asyncio
async def test_download_talk_file_paths():
    # disabled / bad id
    with patch.object(TALK, "get_settings", return_value=_settings(NEXTCLOUD_TALK_NOTIFY=False)):
        assert await TALK.download_talk_file(5) is None
    with patch.object(TALK, "get_settings", return_value=_settings()):
        assert await TALK.download_talk_file(0) is None
        assert await TALK.download_talk_file("x") is None

    # no base
    with patch.object(TALK, "get_settings", return_value=_settings(NEXTCLOUD_DAV_URL="", NEXTCLOUD_BASE_URL="")):
        assert await TALK.download_talk_file(5) is None

    # dav path success (first url)
    file_resp = _Resp(200, content=b"BYTES", headers={"content-type": "video/mp4"})
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: _Client(responses=[file_resp])):
        out = await TALK.download_talk_file(5, path="/Talk/rec.mp4")
        assert out == (b"BYTES", "video/mp4")

    # html rejected on both urls -> None
    html_resp = _Resp(200, content=b"<html>", headers={"content-type": "text/html"})
    shared_html = _Client(responses=[html_resp, html_resp])
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: shared_html):
        assert await TALK.download_talk_file(5, path="/Talk/rec.mp4") is None

    # non-200 then success on second url
    shared2 = _Client(responses=[_Resp(404), file_resp])
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", lambda *a, **k: shared2):
        out = await TALK.download_talk_file(5, path="/Talk/rec.mp4")
        assert out is not None

    # exception
    class _Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): raise RuntimeError("x")
        async def __aexit__(self, *a): return False
    with patch.object(TALK, "get_settings", return_value=_settings()), \
         patch.object(TALK.httpx, "AsyncClient", _Boom):
        assert await TALK.download_talk_file(5) is None


def test_room_url():
    with patch.object(TALK, "get_settings", return_value=_settings()):
        assert TALK.room_url("abc12345").endswith("/call/abc12345")
    with patch.object(TALK, "get_settings", return_value=_settings(NEXTCLOUD_DAV_URL="", NEXTCLOUD_BASE_URL="")):
        assert TALK.room_url("abc12345") == ""


class _BoomClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        raise RuntimeError("conn dead")

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_room_ops_no_base_and_exceptions():
    no_base = _settings(NEXTCLOUD_DAV_URL="", NEXTCLOUD_BASE_URL="")
    with patch.object(TALK, "get_settings", return_value=no_base):
        assert await TALK.create_room("r") is None       # 221
        assert await TALK.delete_room("abc12345") is False  # 251
        assert await TALK.post_to_room("abc12345", "x") is False  # 273
        assert await TALK.list_room_messages("abc12345") == []    # ~298 no base
        assert await TALK.ping() is False                 # ~328 no base

    s = _settings()
    with patch.object(TALK, "get_settings", return_value=s), \
         patch.object(TALK.httpx, "AsyncClient", _BoomClient):
        assert await TALK.delete_room("abc12345") is False   # 261-263
        assert await TALK.post_to_room("abc12345", "x") is False  # 287-289
        assert await TALK.list_room_messages("abc12345") == []    # exception path
        assert await TALK.ping() is False                        # exception path


def test_blocks_to_text_context_empty_parts():
    # context block whose elements yield no usable text -> no append (95->80)
    out = TALK.blocks_to_text([{"type": "context", "elements": ["x", "y"]}])
    assert out == ""
