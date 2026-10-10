"""Unit tests for app/services/messenger_sidecar.py — webhook sidecar."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.messenger_sidecar as M


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for k in M._stats:
        M._stats[k] = 0
    M._recent_events.clear()
    M._seen_message_ids.clear()
    M._api_token = ""
    monkeypatch.setattr(M, "ADMIN_EMAIL", "a@b.c")
    monkeypatch.setattr(M, "ADMIN_PASSWORD", "pw")
    yield


def _resp(status=200, json_body=None, text=""):
    return SimpleNamespace(status_code=status,
                           json=lambda: json_body or {}, text=text)


class _Http:
    """URL-routed fake httpx.AsyncClient."""

    def __init__(self, post=None, get=None):
        self._post = post  # async fn(url, **kw) -> resp
        self._get = get

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kw):
        return await self._post(url, **kw)

    async def get(self, url, **kw):
        return await self._get(url, **kw)


def _patch_http(monkeypatch, post=None, get=None):
    monkeypatch.setattr(M.httpx, "AsyncClient",
                        lambda *a, **k: _Http(post, get))


def _event(**kw) -> M.WebhookEvent:
    d = dict(page_id="12345", sender_psid="999",
             recipient_id="12345", message_text="hi",
             message_type="text", message_mid="m1", timestamp=1)
    d.update(kw)
    return M.WebhookEvent(**d)


# ── pure helpers ─────────────────────────────────────────────────────


def test_sanitize_and_validate():
    assert M._sanitize_log_text("a\nb\rc") == "a\\nb\\rc"
    assert M._sanitize_log_text("x" * 300, max_len=10) == "x" * 10
    assert M._sanitize_log_text(None) == ""

    assert M._validate_fb_id("12345") == "12345"
    assert M._validate_fb_id("123_456") == "123_456"
    assert M._validate_fb_id("  99 ") == "99"
    with pytest.raises(ValueError, match="empty"):
        M._validate_fb_id("")
    with pytest.raises(ValueError, match="format"):
        M._validate_fb_id("../admin")
    with pytest.raises(ValueError, match="format"):
        M._validate_fb_id("12a34")

    url = M._graph_messages_url("12345")
    assert url.endswith("/12345/messages")
    with pytest.raises(ValueError):
        M._graph_messages_url("bad/id")


# ── health / stats / process_event ────────────────────────────────────


@pytest.mark.asyncio
async def test_health_stats_process(monkeypatch):
    assert (await M.health())["service"] == "messenger-sidecar"
    s = await M.stats()
    assert s["events_received"] == 0 and "dedup_cache_size" in s

    # swallow the background task so it doesn't run real work
    created = []
    monkeypatch.setattr(M.asyncio, "create_task",
                        lambda coro: (coro.close(), created.append(coro))[0])
    # close() the coroutine then append — order irrelevant, just don't run it

    # non-reply types → ignored
    r = await M.process_event(_event(message_type="delivery"))
    assert r.status_code == 200
    assert M._stats["events_processed"] == 1
    assert M._stats["events_received"] == 1

    r = await M.process_event(_event(
        message_type="text", sender_psid="", message_mid="m2"))
    assert M._stats["events_processed"] == 2

    # text → processing (async task created)
    r = await M.process_event(_event(message_mid="m3"))
    assert M._stats["events_processed"] == 3

    # same mid → duplicate
    r = await M.process_event(_event(message_mid="m3"))
    import json as j
    assert j.loads(r.body)["status"] == "duplicate"


# ── _handle_message ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_message(monkeypatch):
    # no account → early return
    monkeypatch.setattr(M, "_get_account_and_config", AsyncMock(
        return_value=None))
    await M._handle_message(_event())
    assert M._stats["auto_replies_sent"] == 0

    # auto-reply disabled → early return
    monkeypatch.setattr(M, "_get_account_and_config", AsyncMock(
        return_value=("id", "tok", {"enabled": False}, "Page")))
    await M._handle_message(_event())

    # happy path — typing_on → AI → send → typing_off → counter++
    monkeypatch.setattr(M, "_get_account_and_config", AsyncMock(
        return_value=("id", "tok", {"enabled": True}, "Page")))
    actions = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(M, "_send_sender_action", actions)
    monkeypatch.setattr(M, "_send_text_message", send)
    monkeypatch.setattr(M, "_generate_ai_response", AsyncMock(
        return_value="auto reply"))
    await M._handle_message(_event())
    assert actions.await_count == 2  # typing_on + typing_off
    send.assert_awaited_once()
    assert M._stats["auto_replies_sent"] == 1

    # exception → errors++
    actions.side_effect = RuntimeError("boom")
    await M._handle_message(_event())
    assert M._stats["errors"] == 1


# ── _get_admin_token / _get_account_and_config ────────────────────────


@pytest.mark.asyncio
async def test_get_admin_token(monkeypatch):
    # no creds → ""
    monkeypatch.setattr(M, "ADMIN_EMAIL", "")
    assert await M._get_admin_token() == ""
    monkeypatch.setattr(M, "ADMIN_EMAIL", "a@b.c")

    # login 200 → cached
    post = AsyncMock(return_value=_resp(200, {"access_token": "T1"}))
    _patch_http(monkeypatch, post=post)
    assert await M._get_admin_token() == "T1"
    assert M._api_token == "T1"
    # cached — no second call
    assert await M._get_admin_token() == "T1"
    assert post.await_count == 1

    # login fails → ""
    M._api_token = ""
    post = AsyncMock(return_value=_resp(401))
    _patch_http(monkeypatch, post=post)
    assert await M._get_admin_token() == ""


@pytest.mark.asyncio
async def test_get_account_and_config(monkeypatch):
    # no token → None
    monkeypatch.setattr(M, "_get_admin_token", AsyncMock(return_value=""))
    assert await M._get_account_and_config("12345") is None

    # accounts list — match by platform + account_id
    monkeypatch.setattr(M, "_get_admin_token", AsyncMock(
        return_value="T"))
    get = AsyncMock(return_value=_resp(200, [
        {"platform": "instagram", "account_id": "12345"},
        {"platform": "facebook", "account_id": "12345",
         "id": "acct-1", "display_name": "MyPage",
         "meta_data": {"page_token": "PT",
                       "messenger_auto_reply": {"enabled": True}}},
    ]))
    _patch_http(monkeypatch, get=get)
    out = await M._get_account_and_config("12345")
    assert out == ("acct-1", "PT", {"enabled": True}, "MyPage")

    # dict-shaped payload ({"accounts": [...]})
    get = AsyncMock(return_value=_resp(200, {"accounts": [
        {"platform": "facebook", "account_id": "777", "id": "a",
         "display_name": "P", "meta_data": {}}]}))
    _patch_http(monkeypatch, get=get)
    out = await M._get_account_and_config("777")
    assert out[0] == "a"

    # non-200 → None
    get = AsyncMock(return_value=_resp(500))
    _patch_http(monkeypatch, get=get)
    assert await M._get_account_and_config("12345") is None

    # 401 → token refresh + retry
    calls = {"n": 0}
    async def _get(url, **kw):
        calls["n"] += 1
        return _resp(401) if calls["n"] == 1 else _resp(200, [])
    M._api_token = ""
    _patch_http(monkeypatch, get=_get)
    assert await M._get_account_and_config("x") is None
    assert calls["n"] == 2  # retried once


# ── sender action / text send ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_senders(monkeypatch):
    post = AsyncMock(return_value=_resp(200))
    _patch_http(monkeypatch, post=post)
    await M._send_sender_action("tok", "123", "999", "typing_on")
    body = post.await_args.kwargs["json"]
    assert body["sender_action"] == "typing_on"
    assert body["recipient"]["id"] == "999"
    assert post.await_args.kwargs["params"]["access_token"] == "tok"

    # invalid psid → ValueError propagates
    with pytest.raises(ValueError):
        await M._send_sender_action("tok", "123", "bad/psid", "x")

    # text message — messaging_type + error tolerated
    await M._send_text_message("tok", "123", "999", "hello")
    body = post.await_args.kwargs["json"]
    assert body["messaging_type"] == "RESPONSE"
    assert body["message"]["text"] == "hello"

    post = AsyncMock(return_value=_resp(400, text="err"))
    _patch_http(monkeypatch, post=post)
    await M._send_text_message("tok", "123", "999", "hello")  # no raise


# ── _generate_ai_response ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_ai_response(monkeypatch):
    cfg = {"system_prompt": "You are {page_name}'s bot",
           "fallback_text": "FB"}

    # DMR success — think tags stripped
    post = AsyncMock(return_value=_resp(200, {"choices": [{"message": {
        "content": "<think>x</think>  real answer  "}}]}))
    _patch_http(monkeypatch, post=post)
    out = await M._generate_ai_response(cfg, "hi", "MyPage")
    assert out == "real answer"
    msgs = post.await_args.kwargs["json"]["messages"]
    assert msgs[0]["content"] == "You are MyPage's bot"
    assert msgs[1]["content"] == "hi /no_think"

    # DMR empty content → CF fallback
    monkeypatch.setattr(M, "CLOUDFLARE_API_TOKEN", "cf-tok")
    monkeypatch.setattr(M, "CLOUDFLARE_ACCOUNT_ID", "cf-acct")
    calls = {"n": 0}
    async def _post(url, **kw):
        calls["n"] += 1
        if "chat/completions" in url:
            return _resp(200, {"choices": [{"message": {"content": ""}}]})
        return _resp(200, {"result": {"response": "  cf answer  "}})
    _patch_http(monkeypatch, post=_post)
    out = await M._generate_ai_response(cfg, "hi", "MyPage")
    assert out == "cf answer"

    # both fail → fallback_text
    async def _post_fail(url, **kw):
        return _resp(500)
    _patch_http(monkeypatch, post=_post_fail)
    assert await M._generate_ai_response(cfg, "hi", "MyPage") == "FB"

    # DMR throws, no CF creds → fallback
    monkeypatch.setattr(M, "CLOUDFLARE_API_TOKEN", "")
    async def _post_raise(url, **kw):
        raise ConnectionError("down")
    _patch_http(monkeypatch, post=_post_raise)
    assert await M._generate_ai_response(cfg, "hi", "MyPage") == "FB"

    # default fallback text when not configured
    out = await M._generate_ai_response({}, "hi", None)
    assert "Thanks" in out
