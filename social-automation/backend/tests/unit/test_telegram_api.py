"""Unit tests for Telegram Bot API client + webhook helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import telegram_api as api


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(payload)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, **kwargs):
        self.calls.append({"url": url, "json": json})
        return self.response


@pytest.fixture
def client():
    return api.TelegramAPIClient(bot_token="123456:ABC-DEF")


def test_client_rejects_bad_token():
    with pytest.raises(ValueError, match="Invalid Telegram bot token"):
        api.TelegramAPIClient(bot_token="not-a-token")


@pytest.mark.asyncio
async def test_get_me_success(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": "cloudless_bot"}})
    )
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        me = await client.get_me()
    assert me["username"] == "cloudless_bot"
    assert fake.calls[0]["url"].endswith("/getMe")


@pytest.mark.asyncio
async def test_get_me_api_error(client):
    fake = _FakeAsyncClient(
        _FakeResponse(401, {"ok": False, "description": "Unauthorized", "error_code": 401})
    )
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        with pytest.raises(api.TelegramAPIError) as exc:
            await client.get_me()
    assert exc.value.detail == "Unauthorized"


@pytest.mark.asyncio
async def test_set_webhook_requires_https(client):
    with pytest.raises(ValueError, match="HTTPS"):
        await client.set_webhook("http://example.com/hook")


@pytest.mark.asyncio
async def test_set_webhook_success(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": True}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        ok = await client.set_webhook(
            "https://social.cloudless.gr/api/v1/telegram/webhook/abc",
            secret_token="sec_token_1",
            allowed_updates=["message"],
        )
    assert ok is True
    payload = fake.calls[0]["json"]
    assert payload["url"].startswith("https://")
    assert payload["secret_token"] == "sec_token_1"
    assert payload["allowed_updates"] == ["message"]


@pytest.mark.asyncio
async def test_send_message_truncates(client):
    fake = _FakeAsyncClient(
        _FakeResponse(200, {"ok": True, "result": {"message_id": 9, "text": "x"}})
    )
    long_text = "a" * 5000
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        await client.send_message(42, long_text)
    assert len(fake.calls[0]["json"]["text"]) == api.MAX_MESSAGE_CHARS
    assert fake.calls[0]["json"]["chat_id"] == 42


def test_extract_inbound_text_update():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": "Hello",
            "chat": {"id": 99, "type": "private"},
            "from": {"id": 7, "first_name": "Ada", "username": "ada", "is_bot": False},
        },
    }
    inbound = api.extract_inbound_text_update(update)
    assert inbound is not None
    assert inbound["chat_id"] == 99
    assert inbound["text"] == "Hello"
    assert inbound["sender_name"] == "Ada"


def test_extract_inbound_ignores_sticker():
    update = {
        "update_id": 2,
        "message": {
            "message_id": 11,
            "sticker": {"emoji": "😀"},
            "chat": {"id": 99, "type": "private"},
            "from": {"id": 7, "is_bot": False},
        },
    }
    assert api.extract_inbound_text_update(update) is None


def test_telegram_api_error_detail_alias():
    exc = api.TelegramAPIError(400, "Bad Request", method="sendMessage")
    assert exc.detail == "Bad Request"


# ── Media / poll / keyboard additions ────────────────────────────────


@pytest.mark.asyncio
async def test_send_photo_payload(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": {"message_id": 5}}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        await client.send_photo(
            42, "https://x.test/p.png", caption="cap",
            reply_markup={"inline_keyboard": [[{"text": "Go", "url": "https://x.test"}]]},
        )
    body = fake.calls[0]["json"]
    assert fake.calls[0]["url"].endswith("/sendPhoto")
    assert body["photo"] == "https://x.test/p.png"
    assert body["caption"] == "cap"
    assert body["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://x.test"


@pytest.mark.asyncio
async def test_send_media_group_validation(client):
    with pytest.raises(ValueError, match="2-10"):
        await client.send_media_group(42, [{"type": "photo", "media": "https://x/1.png"}])
    with pytest.raises(ValueError, match="photo or video"):
        await client.send_media_group(
            42,
            [
                {"type": "photo", "media": "https://x/1.png"},
                {"type": "audio", "media": "https://x/2.mp3"},
            ],
        )
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": [{}, {}]}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        result = await client.send_media_group(
            42,
            [
                {"type": "photo", "media": "https://x/1.png"},
                {"type": "photo", "media": "https://x/2.png", "caption": "c"},
            ],
        )
    assert len(result) == 2
    assert fake.calls[0]["json"]["media"][1]["caption"] == "c"


@pytest.mark.asyncio
async def test_send_poll_payload(client):
    with pytest.raises(ValueError, match="2-10"):
        await client.send_poll(42, "q?", ["only one"])
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": {"poll": {"id": "p1"}}}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        await client.send_poll(42, "Pick one", ["a", "b"], allows_multiple_answers=True)
    body = fake.calls[0]["json"]
    assert body["options"] == [{"text": "a"}, {"text": "b"}]
    assert body["allows_multiple_answers"] is True


@pytest.mark.asyncio
async def test_send_message_with_markup(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": {"message_id": 9}}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        await client.send_message_with_markup(
            42, "click", {"inline_keyboard": [[{"text": "B", "callback_data": "d"}]]}
        )
    body = fake.calls[0]["json"]
    assert body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "d"


@pytest.mark.asyncio
async def test_answer_callback_and_pin_delete(client):
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "result": True}))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        assert await client.answer_callback_query("cb1", text="thanks") is True
        assert await client.pin_chat_message(42, 10) is True
        assert await client.delete_message(42, 10) is True
        assert await client.send_chat_action(42, "typing") is True
    assert fake.calls[0]["json"]["text"] == "thanks"
    assert fake.calls[3]["json"]["action"] == "typing"


@pytest.mark.asyncio
async def test_send_chat_action_rejects_unknown(client):
    with pytest.raises(ValueError, match="unknown chat action"):
        await client.send_chat_action(42, "exploding")


def test_inline_keyboard_builder():
    kb = api.inline_keyboard([[{"text": "Audit", "url": "https://c.test"}]])
    assert kb["inline_keyboard"][0][0]["url"] == "https://c.test"
    with pytest.raises(ValueError, match="needs url"):
        api.inline_keyboard([[{"text": "no action"}]])
    with pytest.raises(ValueError, match="text is required"):
        api.inline_keyboard([[{"url": "https://c.test"}]])


def test_extract_callback_query():
    update = {
        "update_id": 3,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": 7, "username": "ada"},
            "data": "cta:audit",
            "message": {"message_id": 20, "chat": {"id": 99}},
        },
    }
    cb = api.extract_callback_query(update)
    assert cb is not None
    assert cb["callback_id"] == "cb-1"
    assert cb["data"] == "cta:audit"
    assert cb["chat_id"] == 99
    assert api.extract_callback_query({"update_id": 4}) is None


# ── remaining coverage ────────────────────────────────────────────────


def test_sanitize_log_text():
    assert api._sanitize_log_text("a\nb\rc\x00d") == "a\\nb\\rcd"
    assert api._sanitize_log_text("x" * 500, 10) == "x" * 10


@pytest.mark.asyncio
async def test_call_invalid_method_name(client):
    with pytest.raises(api.TelegramAPIError):
        await client._call("bad-method!")


@pytest.mark.asyncio
async def test_call_non_json_and_non_dict(client):
    class _BadJson(_FakeResponse):
        def json(self):
            raise ValueError("no json")

    fake = _FakeAsyncClient(_BadJson(500, text="oops"))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        with pytest.raises(api.TelegramAPIError) as ei:
            await client._call("getMe")
    assert "oops" in ei.value.detail

    fake = _FakeAsyncClient(_FakeResponse(200, ["list", "not", "dict"]))
    with patch("app.services.telegram_api.httpx.AsyncClient", return_value=fake):
        with pytest.raises(api.TelegramAPIError, match="Invalid JSON"):
            await client._call("getMe")


def _call_stub(client, ret=None):
    calls = []

    async def _call(method, payload=None):
        calls.append((method, payload))
        return ret

    client._call = _call
    return calls


@pytest.mark.asyncio
async def test_webhook_and_chat_methods(client):
    calls = _call_stub(client, True)
    assert await client.delete_webhook(drop_pending_updates=True) is True
    assert calls[0] == ("deleteWebhook", {"drop_pending_updates": True})

    calls = _call_stub(client, {"url": "https://x"})
    assert await client.get_webhook_info() == {"url": "https://x"}
    calls = _call_stub(client, "non-dict")
    assert await client.get_webhook_info() == {}

    calls = _call_stub(client, True)
    assert await client.set_chat_description(1, "d" * 300) is True
    assert len(calls[0][1]["description"]) == 255

    calls = _call_stub(client, True)
    assert await client.set_my_commands([{"command": "x"}], scope={"type": "all"}) is True
    assert calls[0][1]["scope"] == {"type": "all"}

    calls = _call_stub(client, {"ok": 1})
    assert await client.forward_message(1, 2, 3) == {"ok": 1}
    assert calls[0][0] == "forwardMessage"
    assert await client.copy_message(1, 2, 3) == {"ok": 1}
    assert calls[1][0] == "copyMessage"
    assert await client.get_chat(5) == {"ok": 1}
    assert calls[2] == ("getChat", {"chat_id": 5})
    assert await client.get_chat_member(5, 9) == {"ok": 1}
    assert calls[3] == ("getChatMember", {"chat_id": 5, "user_id": 9})


@pytest.mark.asyncio
async def test_set_webhook_branches(client):
    with pytest.raises(ValueError, match="secret_token"):
        await client.set_webhook("https://x.io/h", secret_token="bad token!")

    calls = _call_stub(client, True)
    assert await client.set_webhook("https://x.io/h", secret_token="good-tok", allowed_updates=["message"]) is True
    assert calls[0][1]["secret_token"] == "good-tok"
    assert calls[0][1]["allowed_updates"] == ["message"]


@pytest.mark.asyncio
async def test_send_message_branches(client):
    with pytest.raises(ValueError, match="text is required"):
        await client.send_message(1, "   ")

    calls = _call_stub(client, {"message_id": 7})
    out = await client.send_message(1, "hi", parse_mode="HTML", reply_to_message_id=9)
    assert out == {"message_id": 7}
    assert calls[0][1]["parse_mode"] == "HTML"
    assert calls[0][1]["reply_parameters"] == {"message_id": 9}


@pytest.mark.asyncio
async def test_media_sends(client):
    calls = _call_stub(client, {"message_id": 1})
    await client.send_photo(1, "http://img", caption="c", parse_mode="HTML", reply_markup={"k": 1})
    assert calls[0][1]["caption"] == "c" and calls[0][1]["reply_markup"] == {"k": 1}
    await client.send_video(1, "http://v", caption="c", parse_mode="HTML", reply_markup={"k": 1})
    assert calls[1][0] == "sendVideo"
    await client.send_document(1, "http://d", caption="c", parse_mode="HTML", reply_markup={"k": 1})
    assert calls[2][0] == "sendDocument"

    with pytest.raises(ValueError, match="missing media"):
        await client.send_media_group(1, [{"type": "photo"}, {"type": "photo", "media": "x"}])

    calls = _call_stub(client, [{"message_id": 5}])
    out = await client.send_media_group(1, [{"type": "photo", "media": "a"}, {"type": "video", "media": "b"}])
    assert out == [{"message_id": 5}]
    calls = _call_stub(client, {"not": "list"})
    assert await client.send_media_group(1, [{"type": "photo", "media": "a"}, {"type": "photo", "media": "b"}]) == []

    with pytest.raises(ValueError, match="question is required"):
        await client.send_poll(1, "  ", ["a", "b"])
    calls = _call_stub(client, {"message_id": 9})
    out = await client.send_poll(1, "q?", ["a", "b"])
    assert out == {"message_id": 9}
    assert calls[0][1]["options"] == [{"text": "a"}, {"text": "b"}]


@pytest.mark.asyncio
async def test_management_methods(client):
    with pytest.raises(ValueError, match="text is required"):
        await client.send_message_with_markup(1, " ", {})
    calls = _call_stub(client, {"message_id": 2})
    await client.send_message_with_markup(1, "hi", {"inline_keyboard": []}, parse_mode="HTML")
    assert calls[0][1]["parse_mode"] == "HTML"

    calls = _call_stub(client, True)
    assert await client.unpin_chat_message(1, 5) is True
    assert calls[0][1] == {"chat_id": 1, "message_id": 5}

    calls = _call_stub(client, {"message_id": 3})
    out = await client.edit_message_text(1, 2, "new", parse_mode="HTML", reply_markup={"k": 1})
    assert out == {"message_id": 3}
    assert calls[0][1]["reply_markup"] == {"k": 1}
    with pytest.raises(ValueError, match="text is required"):
        await client.edit_message_text(1, 2, " ")

    calls = _call_stub(client, 42)
    assert await client.get_chat_member_count(1) == 42
    calls = _call_stub(client, "not-int")
    assert await client.get_chat_member_count(1) == 0

    calls = _call_stub(client, "https://t.me/+abc")
    assert await client.export_chat_invite_link(1) == "https://t.me/+abc"
    calls = _call_stub(client, {"not": "str"})
    assert await client.export_chat_invite_link(1) == ""

    calls = _call_stub(client, {"invite_link": "https://t.me/+xyz", "name": "ig"})
    out = await client.create_chat_invite_link(
        1, name="a-very-long-source-name-that-exceeds-32", member_limit=100
    )
    assert out == {"invite_link": "https://t.me/+xyz", "name": "ig"}
    assert calls[0][0] == "createChatInviteLink"
    assert calls[0][1]["name"] == "a-very-long-source-name-that-exc"
    assert len(calls[0][1]["name"]) == 32
    assert calls[0][1]["member_limit"] == 100
    assert calls[0][1]["creates_join_request"] is False

    calls = _call_stub(client, {"invite_link": "https://t.me/+join"})
    out = await client.create_chat_invite_link(
        1, expire_date=1_800_000_000, creates_join_request=True
    )
    assert calls[0][1] == {
        "chat_id": 1,
        "creates_join_request": True,
        "expire_date": 1_800_000_000,
    }
    calls = _call_stub(client, "not-a-dict")
    assert await client.create_chat_invite_link(1) == {}


def test_extract_inbound_edges():
    # no message key
    assert api.extract_inbound_text_update({}) is None
    # no chat id
    assert api.extract_inbound_text_update({"message": {"text": "hi", "chat": {}}}) is None
    # blank text
    assert api.extract_inbound_text_update({"message": {"text": " ", "chat": {"id": 1}}}) is None
    # edited_message + caption + entities not list + name fallback to username
    upd = {
        "edited_message": {
            "chat": {"id": 1, "type": "private"},
            "caption": "cap",
            "caption_entities": "not-a-list",
            "from": {"username": "u1", "is_bot": True},
        }
    }
    out = api.extract_inbound_text_update(upd)
    assert out["text"] == "cap" and out["entities"] == []
    assert out["sender_name"] == "u1" and out["is_bot"] is True
    # first+last name join
    upd2 = {
        "message": {
            "chat": {"id": 1, "title": "T"},
            "text": "hi",
            "from": {"first_name": "A", "last_name": "B"},
        }
    }
    out2 = api.extract_inbound_text_update(upd2)
    assert out2["sender_name"] == "A B" and out2["chat_title"] == "T"


def test_extract_my_chat_member_edges():
    assert api.extract_my_chat_member_update({}) is None
    assert api.extract_my_chat_member_update({"my_chat_member": {"chat": {}}}) is None
    out = api.extract_my_chat_member_update({
        "my_chat_member": {
            "chat": {"id": 9, "type": "group", "title": "G"},
            "old_chat_member": {"status": "left"},
            "new_chat_member": {"status": "member"},
            "from": {"id": 7},
        }
    })
    assert out == {
        "update_id": None,
        "chat_id": 9,
        "chat_type": "group",
        "chat_title": "G",
        "old_status": "left",
        "new_status": "member",
        "from_user_id": 7,
    }


@pytest.mark.asyncio
async def test_get_chat_administrators(client):
    calls = _call_stub(client, [{"status": "creator", "user": {"id": 1}}])
    out = await client.get_chat_administrators(-100)
    assert out == [{"status": "creator", "user": {"id": 1}}]
    assert calls[0][0] == "getChatAdministrators"
    assert calls[0][1] == {"chat_id": -100}

    _call_stub(client, "not-a-list")
    assert await client.get_chat_administrators(1) == []


def test_extract_chat_member_update():
    upd = {
        "update_id": 9,
        "chat_member": {
            "chat": {"id": -100, "type": "channel", "title": "HQ"},
            "from": {"id": 7},
            "new_chat_member": {"status": "member", "user": {"id": 7, "username": "jane", "first_name": "Jane"}},
            "old_chat_member": {"status": "left", "user": {"id": 7, "username": "jane"}},
            "invite_link": {"invite_link": "https://t.me/+x", "name": "ig"},
        },
    }
    out = api.extract_chat_member_update(upd)
    assert out["chat_id"] == -100
    assert out["chat_type"] == "channel"
    assert out["new_status"] == "member"
    assert out["old_status"] == "left"
    assert out["username"] == "jane"
    assert out["invite_link_name"] == "ig"
    assert out["via_join_request"] is False

    # missing/empty cases
    assert api.extract_chat_member_update({}) is None
    assert api.extract_chat_member_update({"chat_member": {}}) is None
    assert api.extract_chat_member_update({"chat_member": "x"}) is None


def test_extract_chat_member_update_join_request():
    out = api.extract_chat_member_update(
        {
            "chat_member": {
                "chat": {"id": -1, "type": "channel"},
                "new_chat_member": {"status": "member", "user": {"id": 1}},
                "old_chat_member": {"status": "left"},
                "via_join_request": True,
            }
        }
    )
    assert out["via_join_request"] is True
    assert out["invite_link_name"] == ""
