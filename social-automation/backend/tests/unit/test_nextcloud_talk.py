"""Nextcloud Talk transport + webhook bot tests.

HTTP boundary (httpx.AsyncClient) and settings are faked; everything else
runs the real code path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.api.talk_bot as BOT
import app.services.nextcloud_talk as TALK
import app.services.slack_notifications as SN


def _settings(**over) -> SimpleNamespace:
    base = dict(
        NEXTCLOUD_TALK_NOTIFY=True,
        NEXTCLOUD_USERNAME="u@cloudless.gr",
        NEXTCLOUD_APP_PASSWORD="app-pw",
        NEXTCLOUD_DAV_URL="https://cloud.cloudless.gr/remote.php/dav/files/u@cloudless.gr",
        NEXTCLOUD_BASE_URL="",
        NEXTCLOUD_TALK_ROOM="room-ops",
        NEXTCLOUD_TALK_ROOM_ALERTS="room-approvals",
        NEXTCLOUD_TALK_BOT_SECRET="bot-secret",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _Resp:
    def __init__(self, status=201, text_="ok"):
        self.status_code = status
        self.text = text_

    def json(self):
        return {}


class _Client:
    """httpx.AsyncClient fake — accepts auth/headers/json kwargs."""

    calls: list[dict] = []

    def __init__(self, *a, resp=None, **kw):
        self._resp = resp or _Resp()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        type(self).calls.append({"url": url, **kw})
        return self._resp


@pytest.fixture(autouse=True)
def _reset_calls():
    _Client.calls = []
    yield


# ── service: config + flattening ─────────────────────────────────────


def test_base_url_derived_from_dav():
    with patch.object(TALK, "get_settings", return_value=_settings()):
        assert TALK._base_url() == "https://cloud.cloudless.gr"


def test_base_url_explicit_wins():
    s = _settings(NEXTCLOUD_BASE_URL="https://nc.example.com/")
    with patch.object(TALK, "get_settings", return_value=s):
        assert TALK._base_url() == "https://nc.example.com"


def test_room_routing():
    with patch.object(TALK, "get_settings", return_value=_settings()):
        assert TALK.room_token("digest") == "room-ops"
        assert TALK.room_token("alerts") == "room-approvals"
        assert TALK.room_token("approvals") == "room-approvals"


def test_room_falls_back_to_ops():
    with patch.object(
        TALK, "get_settings", return_value=_settings(NEXTCLOUD_TALK_ROOM_ALERTS="")
    ):
        assert TALK.room_token("approvals") == "room-ops"


def test_blocks_to_text():
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Headline*"}},
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open"},
                    "url": "https://x",
                    "action_id": "a1",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Heal"},
                    "action_id": "socialauto_session_heal",
                },
            ],
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "foot"}]},
    ]
    out = TALK.blocks_to_text(blocks)
    assert "*Headline*" in out
    assert "• Open → https://x" in out
    assert "socialauto_session_heal" in out
    assert "foot" in out


def test_blocks_to_text_empty():
    assert TALK.blocks_to_text(None) == ""
    assert TALK.blocks_to_text([]) == ""


# ── service: posting ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_to_talk_hits_chat_endpoint():
    with patch.object(TALK, "get_settings", return_value=_settings()), patch.object(
        TALK.httpx, "AsyncClient", _Client
    ):
        assert await TALK.post_to_talk("hello", purpose="digest") is True
    call = _Client.calls[0]
    assert call["url"].endswith("/ocs/v2.php/apps/spreed/api/v1/chat/room-ops")
    assert call["json"]["message"] == "hello"
    assert call["auth"] == ("u@cloudless.gr", "app-pw")


@pytest.mark.asyncio
async def test_post_to_talk_disabled_noop():
    with patch.object(
        TALK, "get_settings", return_value=_settings(NEXTCLOUD_TALK_NOTIFY=False)
    ):
        assert await TALK.post_to_talk("hi") is False
    assert _Client.calls == []


@pytest.mark.asyncio
async def test_post_to_talk_http_error_nonfatal():
    with patch.object(TALK, "get_settings", return_value=_settings()), patch.object(
        TALK.httpx, "AsyncClient", lambda *a, **k: _Client(resp=_Resp(500, "boom"))
    ):
        assert await TALK.post_to_talk("hi") is False


# ── service: bot HMAC ────────────────────────────────────────────────


def test_verify_bot_request():
    body = b'{"type":"Create"}'
    rnd = "abc123"
    sig = hmac.new(b"bot-secret", rnd.encode() + body, hashlib.sha256).hexdigest()
    with patch.object(TALK, "get_settings", return_value=_settings()):
        assert TALK.verify_bot_request(rnd, body, sig) is True
        assert TALK.verify_bot_request(rnd, body, "deadbeef") is False
        assert TALK.verify_bot_request("", body, sig) is False


def test_verify_bot_request_no_secret():
    with patch.object(
        TALK, "get_settings", return_value=_settings(NEXTCLOUD_TALK_BOT_SECRET="")
    ):
        assert TALK.verify_bot_request("r", b"body", "s") is False


@pytest.mark.asyncio
async def test_bot_reply_signed():
    with patch.object(TALK, "get_settings", return_value=_settings()), patch.object(
        TALK.httpx, "AsyncClient", _Client
    ):
        assert await TALK.bot_reply("room-ops", "done", reply_to=7) is True
    call = _Client.calls[0]
    assert "/bot/room-ops/message" in call["url"]
    hdrs = call["headers"]
    rnd, sig = (
        hdrs["X-Nextcloud-Talk-Bot-Random"],
        hdrs["X-Nextcloud-Talk-Bot-Signature"],
    )
    expect = hmac.new(
        b"bot-secret", rnd.encode() + call["json"]["message"].encode(), hashlib.sha256
    ).hexdigest()
    assert sig == expect
    assert call["json"]["replyTo"] == 7


# ── slack dual-post fanout ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_digest_dual_posts_to_talk():
    settings = _settings(
        SLACK_WEBHOOK_URL="https://hooks.slack.test/abc",
        SLACK_BOT_TOKEN="",
        SLACK_ACCESS_TOKEN="",
        SLACK_CHANNEL_ID="C123",
    )
    with patch.object(SN, "get_settings", return_value=settings), patch.object(
        SN.httpx, "AsyncClient", _Client
    ), patch.object(TALK, "get_settings", return_value=settings), patch.object(
        TALK.httpx, "AsyncClient", _Client
    ):
        ok, err, ts = await SN.post_digest_text_to_slack("daily digest")
    assert ok is True
    urls = [c["url"] for c in _Client.calls]
    assert "https://hooks.slack.test/abc" in urls
    assert any("/chat/room-ops" in u for u in urls)


@pytest.mark.asyncio
async def test_digest_skips_talk_when_disabled():
    settings = _settings(
        NEXTCLOUD_TALK_NOTIFY=False,
        SLACK_WEBHOOK_URL="https://hooks.slack.test/abc",
        SLACK_BOT_TOKEN="",
        SLACK_ACCESS_TOKEN="",
        SLACK_CHANNEL_ID="",
    )
    with patch.object(SN, "get_settings", return_value=settings), patch.object(
        SN.httpx, "AsyncClient", _Client
    ), patch.object(TALK, "get_settings", return_value=settings):
        ok, _, _ = await SN.post_digest_text_to_slack("hi")
    assert ok is True
    assert len(_Client.calls) == 1


# ── talk_bot endpoint ────────────────────────────────────────────────


class _Req:
    def __init__(self, body: bytes, headers: dict):
        self._body = body
        self.headers = headers

    async def body(self):
        return self._body


def _signed_request(text: str, room: str = "room-ops"):
    payload = {
        "type": "Create",
        "actor": {"id": "u@cloudless.gr"},
        "object": {"content": json.dumps({"message": text}), "id": 42},
        "target": {"id": room},
    }
    body = json.dumps(payload).encode()
    rnd = "rnd123"
    sig = hmac.new(
        b"bot-secret", rnd.encode() + body, hashlib.sha256
    ).hexdigest()
    return _Req(
        body,
        {
            "x-nextcloud-talk-random": rnd,
            "x-nextcloud-talk-signature": sig,
            "x-nextcloud-talk-backend": "https://cloud.cloudless.gr",
        },
    )


@pytest.mark.asyncio
async def test_webhook_rejects_bad_signature():
    req = _Req(b"{}", {"x-nextcloud-talk-random": "x",
                      "x-nextcloud-talk-signature": "bad"})
    with patch.object(BOT, "verify_bot_request", return_value=False):
        out = await BOT.talk_bot_webhook(req)
    assert out == {"ok": False}


@pytest.mark.asyncio
async def test_webhook_help_command_replies():
    req = _signed_request("help")
    replies = []

    async def _reply(room, msg, *, reply_to=None, backend=""):
        replies.append((room, msg))
        return True

    with patch.object(BOT, "verify_bot_request", return_value=True), patch.object(
        BOT, "bot_reply", _reply
    ), patch.object(
        BOT,
        "get_settings",
        return_value=_settings(
            NEXTCLOUD_TALK_ROOM="room-ops", NEXTCLOUD_TALK_ROOM_ALERTS="room-approvals"
        ),
    ):
        out = await BOT.talk_bot_webhook(req)
    assert out == {"ok": True}
    assert replies and "status" in replies[0][1]
    assert replies[0][0] == "room-ops"


@pytest.mark.asyncio
async def test_webhook_ignores_non_commands():
    req = _signed_request("just chatting")
    replies = []

    async def _reply(room, msg, **kw):
        replies.append(msg)
        return True

    with patch.object(BOT, "verify_bot_request", return_value=True), patch.object(
        BOT, "bot_reply", _reply
    ), patch.object(BOT, "get_settings", return_value=_settings()):
        out = await BOT.talk_bot_webhook(req)
    assert out == {"ok": True}
    assert replies == []


@pytest.mark.asyncio
async def test_webhook_ignores_foreign_room():
    """A message from a room the bot isn't configured for gets no reply."""
    req = _signed_request("help", room="some-other-room")
    replies = []

    async def _reply(room, msg, **kw):
        replies.append(msg)
        return True

    with patch.object(BOT, "verify_bot_request", return_value=True), patch.object(
        BOT, "bot_reply", _reply
    ), patch.object(
        BOT,
        "get_settings",
        return_value=_settings(NEXTCLOUD_TALK_ROOM="room-ops"),
    ):
        out = await BOT.talk_bot_webhook(req)
    assert out == {"ok": True}
    assert replies == []


@pytest.mark.asyncio
async def test_webhook_heal_dispatches_task():
    req = _signed_request("heal")
    sent = []

    class _Celery:
        def send_task(self, name, kwargs=None):
            sent.append(name)

    async def _reply(*a, **kw):
        return True

    fake_celery = _Celery()
    import app.worker.celery_app as CA

    with patch.object(BOT, "verify_bot_request", return_value=True), patch.object(
        BOT, "bot_reply", _reply
    ), patch.object(CA, "celery_app", fake_celery):
        out = await BOT.talk_bot_webhook(req)
    assert out == {"ok": True}
    assert sent == ["app.worker.tasks.session_healer.heal_sessions"]
