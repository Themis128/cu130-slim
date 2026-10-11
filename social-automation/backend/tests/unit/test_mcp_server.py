"""Coverage for app/mcp/server.py."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp.types import CallToolRequestParams

import app.mcp.server as S


class _Resp:
    def __init__(self, status=200, data=None, content=b"x"):
        self.status_code = status
        self._data = data or {}
        self.content = content

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"http {self.status_code}")


class _HTTP:
    """Fake httpx.AsyncClient returning queued responses / recording calls."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self.handler("GET", url, kw)

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.handler("POST", url, kw)

    async def put(self, url, **kw):
        self.calls.append(("PUT", url, kw))
        return self.handler("PUT", url, kw)

    async def delete(self, url, **kw):
        self.calls.append(("DELETE", url, kw))
        return self.handler("DELETE", url, kw)

    async def patch(self, url, **kw):
        self.calls.append(("PATCH", url, kw))
        return self.handler("PATCH", url, kw)


def _patch_http(monkeypatch, handler):
    holder = {}

    def _factory(**kw):
        c = _HTTP(handler)
        holder["client"] = c
        return c

    monkeypatch.setattr(S.httpx, "AsyncClient", _factory)
    return holder


@pytest.fixture(autouse=True)
def _reset_token(monkeypatch):
    monkeypatch.setattr(S, "API_TOKEN", "tok")
    yield


class TestGetToken:
    @pytest.mark.asyncio
    async def test_env_token(self):
        assert await S._get_token() == "tok"

    @pytest.mark.asyncio
    async def test_no_creds(self, monkeypatch):
        monkeypatch.setattr(S, "API_TOKEN", "")
        monkeypatch.delenv("SOCIALAUTO_ADMIN_EMAIL", raising=False)
        monkeypatch.delenv("SOCIAL_ADMIN_EMAIL", raising=False)
        monkeypatch.delenv("SOCIALAUTO_ADMIN_PASSWORD", raising=False)
        monkeypatch.delenv("SOCIAL_ADMIN_PASSWORD", raising=False)
        with pytest.raises(RuntimeError, match="SOCIALAUTO_TOKEN"):
            await S._get_token()

    @pytest.mark.asyncio
    async def test_login(self, monkeypatch):
        monkeypatch.setattr(S, "API_TOKEN", "")
        monkeypatch.setenv("SOCIALAUTO_ADMIN_EMAIL", "a@x.co")
        monkeypatch.setenv("SOCIALAUTO_ADMIN_PASSWORD", "pw")
        holder = _patch_http(monkeypatch, lambda m, u, kw: _Resp(200, {"access_token": "fresh"}))
        assert await S._get_token() == "fresh"
        assert S.API_TOKEN == "fresh"
        assert holder["client"].calls[0][2]["data"]["username"] == "a@x.co"

    @pytest.mark.asyncio
    async def test_login_totp(self, monkeypatch):
        monkeypatch.setattr(S, "API_TOKEN", "")
        monkeypatch.setenv("SOCIAL_ADMIN_EMAIL", "a@x.co")
        monkeypatch.setenv("SOCIAL_ADMIN_PASSWORD", "pw")
        resps = iter(
            [
                _Resp(401, {"detail": "two_factor_required"}),
                _Resp(200, {"access_token": "t2"}),
            ]
        )
        holder = _patch_http(monkeypatch, lambda m, u, kw: next(resps))
        monkeypatch.setattr(S, "_totp_code", AsyncMock(return_value="123456"))
        assert await S._get_token() == "t2"
        assert holder["client"].calls[1][2]["data"]["otp"] == "123456"

    @pytest.mark.asyncio
    async def test_login_401_not_totp(self, monkeypatch):
        monkeypatch.setattr(S, "API_TOKEN", "")
        monkeypatch.setenv("SOCIALAUTO_ADMIN_EMAIL", "a")
        monkeypatch.setenv("SOCIALAUTO_ADMIN_PASSWORD", "p")
        _patch_http(monkeypatch, lambda m, u, kw: _Resp(401, {"detail": "bad creds"}))
        with pytest.raises(httpx.HTTPError):
            await S._get_token()


class TestTotpCode:
    @pytest.mark.asyncio
    async def test_no_secret(self, monkeypatch):
        import app.db.session as ds

        class _CM:
            async def __aenter__(self):
                return SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(ds, "async_session_maker", lambda: _CM())
        with pytest.raises(RuntimeError, match="no TOTP secret"):
            await S._totp_code("a@x.co")

    @pytest.mark.asyncio
    async def test_generates_code(self, monkeypatch):
        import app.db.session as ds

        secret = base64.b32encode(b"0123456789abcdef0123").decode()

        class _CM:
            async def __aenter__(self):
                return SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: secret)))

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(ds, "async_session_maker", lambda: _CM())
        code = await S._totp_code("a@x.co")
        assert len(code) == 6 and code.isdigit()

    @pytest.mark.asyncio
    async def test_unpadded_secret(self, monkeypatch):
        import app.db.session as ds

        # strip padding → exercises the padding-add branch
        secret = base64.b32encode(b"0123456789abcdef0123").decode().rstrip("=")

        class _CM:
            async def __aenter__(self):
                return SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: secret)))

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(ds, "async_session_maker", lambda: _CM())
        assert (await S._totp_code("a")).isdigit()


class TestApiRequest:
    @pytest.mark.asyncio
    async def test_get(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, kw: _Resp(200, {"ok": 1}))
        out = await S._api_request("GET", "/p", params={"a": 1})
        assert out == {"ok": 1}

    @pytest.mark.asyncio
    async def test_post(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, kw: _Resp(200, {"id": 5}))
        out = await S._api_request("POST", "/p", json_body={"x": 1})
        assert out["id"] == 5

    @pytest.mark.asyncio
    async def test_delete_patch_and_put(self, monkeypatch):
        seen = []

        def h(m, u, kw):
            seen.append(m)
            return _Resp(200, {"m": m})

        _patch_http(monkeypatch, h)
        await S._api_request("DELETE", "/d")
        await S._api_request("PATCH", "/p", json_body={"y": 2})
        await S._api_request("PUT", "/p", json_body={"z": 3})
        assert seen == ["DELETE", "PATCH", "PUT"]

    @pytest.mark.asyncio
    async def test_unsupported_method(self):
        with pytest.raises(ValueError, match="Unsupported"):
            await S._api_request("OPTIONS", "/x")

    @pytest.mark.asyncio
    async def test_empty_content(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, kw: _Resp(200, content=b""))
        assert await S._api_request("GET", "/x") == {}

    @pytest.mark.asyncio
    async def test_error_propagates(self, monkeypatch):
        _patch_http(monkeypatch, lambda m, u, kw: _Resp(500))
        with pytest.raises(httpx.HTTPError):
            await S._api_request("GET", "/x")


class TestListTools:
    @pytest.mark.asyncio
    async def test_returns_tools(self):
        out = await S._handle_list_tools(None, None)
        assert len(out.tools) == len(S.TOOLS)
        assert {t.name for t in out.tools} >= {"list_accounts", "create_post", "messenger_setup"}


def _params(name, **arguments):
    return CallToolRequestParams(name=name, arguments=arguments)


def _text(result):
    return json.loads(result.content[0].text)


@pytest.fixture
def api(monkeypatch):
    calls = []

    async def _req(method, path, json_body=None, params=None):
        calls.append((method, path, json_body, params))
        return {"method": method, "path": path}

    monkeypatch.setattr(S, "_api_request", _req)
    return calls


class TestCallToolBasics:
    @pytest.mark.asyncio
    async def test_unknown_tool(self, api):
        out = await S._handle_call_tool(None, _params("nope"))
        assert out.is_error
        assert "Unknown tool" in _text(out)["error"]

    @pytest.mark.asyncio
    async def test_http_error(self, api, monkeypatch):
        async def _boom(*a, **k):
            raise httpx.HTTPError("down")

        monkeypatch.setattr(S, "_api_request", _boom)
        out = await S._handle_call_tool(None, _params("get_brand"))
        assert out.is_error
        assert "down" in _text(out)["error"]

    @pytest.mark.asyncio
    async def test_generic_error(self, api, monkeypatch):
        async def _boom(*a, **k):
            raise RuntimeError("weird")

        monkeypatch.setattr(S, "_api_request", _boom)
        out = await S._handle_call_tool(None, _params("get_brand"))
        assert out.is_error
        assert "RuntimeError" in _text(out)["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name,args,method,path",
        [
            ("list_accounts", {}, "GET", "/api/v1/accounts"),
            ("get_account", {"account_id": "a1"}, "GET", "/api/v1/accounts/a1"),
            ("list_posts", {"status": "draft", "limit": 5}, "GET", "/api/v1/content/posts"),
            ("publish_post", {"post_id": "p1"}, "POST", "/api/v1/content/posts/p1/publish-now"),
            ("generate_content", {"topic": "t", "platform": "ig"}, "POST", "/api/v1/ai/generate-content"),
            ("suggest_hashtags", {"content": "c", "platform": "x"}, "POST", "/api/v1/ai/generate-hashtags"),
            ("score_content", {"content": "c", "platform": "x"}, "POST", "/api/v1/ai/score-content"),
            ("get_analytics", {"days": 7}, "GET", "/api/v1/analytics/overview"),
            ("list_media", {"type": "image"}, "GET", "/api/v1/media/assets"),
            ("get_profile", {"account_id": "a1"}, "GET", "/api/v1/profile/a1"),
            ("get_brand", {}, "GET", "/api/v1/brand"),
            ("messenger_setup", {"account_id": "a", "greeting_text": "g"}, "POST", "/api/v1/messenger/a/setup"),
            ("messenger_get_profile", {"account_id": "a"}, "GET", "/api/v1/messenger/a/profile"),
            ("messenger_update_profile", {"account_id": "a", "ice_breakers": []}, "PUT", "/api/v1/messenger/a/profile"),
            ("messenger_send_message", {"account_id": "a", "recipient_psid": "p", "text": "t"}, "POST", "/api/v1/messenger/a/send"),
            ("messenger_list_conversations", {"account_id": "a"}, "GET", "/api/v1/messenger/a/conversations"),
            ("messenger_get_messages", {"account_id": "a", "conversation_id": "c"}, "GET", "/api/v1/messenger/a/conversations/c"),
            ("messenger_get_auto_reply", {"account_id": "a"}, "GET", "/api/v1/messenger/a/auto-reply"),
            ("messenger_set_auto_reply", {"account_id": "a", "enabled": True}, "PUT", "/api/v1/messenger/a/auto-reply"),
            ("messenger_unsubscribe", {"account_id": "a"}, "POST", "/api/v1/messenger/a/unsubscribe"),
            ("messenger_personal_conversations", {"account_id": "a"}, "GET", "/api/v1/messenger/a/personal/conversations"),
            ("messenger_personal_messages", {"account_id": "a", "thread_id": "t"}, "GET", "/api/v1/messenger/a/personal/conversations/t"),
            ("messenger_personal_send", {"account_id": "a", "thread_id": "t", "text": "x"}, "POST", "/api/v1/messenger/a/personal/send"),
            ("messenger_personal_get_auto_reply", {"account_id": "a"}, "GET", "/api/v1/messenger/a/personal/auto-reply"),
            ("messenger_personal_set_auto_reply", {"account_id": "a", "enabled": True, "model": "m"}, "PUT", "/api/v1/messenger/a/personal/auto-reply"),
        ],
    )
    async def test_dispatch(self, api, name, args, method, path):
        out = await S._handle_call_tool(None, _params(name, **args))
        assert not out.is_error
        assert api[-1][0] == method and api[-1][1] == path


class TestCreatePost:
    @pytest.mark.asyncio
    async def test_minimal(self, api):
        await S._handle_call_tool(None, _params("create_post", content="hi"))
        assert api[-1][2] == {"content_text": "hi"}

    @pytest.mark.asyncio
    async def test_all_fields(self, api):
        await S._handle_call_tool(None, _params("create_post", content="hi", media_ids=["m1"], scheduled_at="2026-01-01", account_ids=["a1"]))
        body = api[-1][2]
        assert body["media_ids"] == ["m1"]
        assert body["target_account_ids"] == ["a1"]

    @pytest.mark.asyncio
    async def test_platform_resolution(self, api, monkeypatch):
        accounts = [
            {"id": "fb-page", "platform": "facebook", "status": "active", "account_type": "page"},
            {"id": "fb-user", "platform": "facebook", "status": "active", "account_type": "user"},
            {"id": "ig1", "platform": "instagram", "status": "active"},
            {"id": "ig-off", "platform": "instagram", "status": "revoked"},
        ]
        recorded = []

        async def _final(method, path, json_body=None, params=None):
            if path == "/api/v1/accounts":
                return accounts
            recorded.append((method, path, json_body))
            return {}

        monkeypatch.setattr(S, "_api_request", _final)
        await S._handle_call_tool(None, _params("create_post", content="hi", platforms=["facebook", "INSTAGRAM"]))
        ids = recorded[-1][2]["target_account_ids"]
        # facebook prefers the page over the personal account
        assert "fb-page" in ids and "fb-user" not in ids
        assert "ig1" in ids and "ig-off" not in ids


class TestMessengerListAll:
    @pytest.mark.asyncio
    async def test_filters(self, monkeypatch):
        accounts = [
            {
                "id": "p1",
                "platform": "facebook",
                "account_type": "page",
                "display_name": "Page",
                "account_id": "pg",
                "meta_data": {"messenger_setup": {"subscribed": True}},
            },
            {"id": "u1", "platform": "facebook", "account_type": "user", "username": "me", "account_id": "fbuid", "meta_data": {"browser_storage_state": "x"}},
            {"id": "ig", "platform": "instagram", "account_type": "business"},
        ]
        monkeypatch.setattr(S, "_api_request", AsyncMock(return_value=accounts))
        out = await S._handle_call_tool(None, _params("messenger_list_all_accounts"))
        data = _text(out)
        assert data["count"] == 2
        page = data["accounts"][0]
        assert page["type"] == "page" and page["messenger_subscribed"]
        user = data["accounts"][1]
        assert user["type"] == "personal" and user["browser_logged_in"]

    @pytest.mark.asyncio
    async def test_dict_wrapped(self, monkeypatch):
        monkeypatch.setattr(S, "_api_request", AsyncMock(return_value={"data": [{"platform": "facebook", "account_type": "user", "id": "u", "meta_data": {}}]}))
        out = await S._handle_call_tool(None, _params("messenger_list_all_accounts"))
        assert _text(out)["count"] == 1


class TestDispatchBodyShapes:
    @pytest.mark.asyncio
    async def test_list_posts_params(self, api):
        await S._handle_call_tool(None, _params("list_posts", status="scheduled", limit=7))
        assert api[-1][3] == {"status": "scheduled", "page_size": 7}

    @pytest.mark.asyncio
    async def test_get_analytics_default(self, api):
        await S._handle_call_tool(None, _params("get_analytics"))
        assert api[-1][3] == {"days": 30}

    @pytest.mark.asyncio
    async def test_send_message_image(self, api):
        await S._handle_call_tool(None, _params("messenger_send_message", account_id="a", recipient_psid="p", image_url="u", messaging_type="UPDATE"))
        body = api[-1][2]
        assert body["image_url"] == "u"
        assert body["messaging_type"] == "UPDATE"
        assert "text" not in body

    @pytest.mark.asyncio
    async def test_update_profile_filters(self, api):
        await S._handle_call_tool(None, _params("messenger_update_profile", account_id="a", get_started={"payload": "X"}, persistent_menu=None))
        body = api[-1][2]
        assert body == {"get_started": {"payload": "X"}}

    @pytest.mark.asyncio
    async def test_set_auto_reply_defaults(self, api):
        await S._handle_call_tool(None, _params("messenger_set_auto_reply", account_id="a", enabled=False))
        body = api[-1][2]
        assert body["enabled"] is False
        assert body["model"] == "@cf/meta/llama-3.1-8b-instruct"
        assert body["max_tokens"] == 200

    @pytest.mark.asyncio
    async def test_personal_set_auto_reply_opts(self, api):
        await S._handle_call_tool(None, _params("messenger_personal_set_auto_reply", account_id="a", enabled=True, fallback_text="fb", max_tokens=99))
        body = api[-1][2]
        assert body["enabled"] is True
        assert body["fallback_text"] == "fb"
        assert body["max_tokens"] == 99
        assert "system_prompt" not in body

    @pytest.mark.asyncio
    async def test_setup_no_greeting(self, api):
        await S._handle_call_tool(None, _params("messenger_setup", account_id="a"))
        assert api[-1][2] == {}


class TestTelegramTools:
    @pytest.fixture
    def tg_api(self, monkeypatch):
        calls = []

        async def _req(method, path, json_body=None, params=None):
            calls.append((method, path, json_body, params))
            if path == "/api/v1/accounts":
                return [{"id": "tg-acc-1", "platform": "telegram", "status": "active"}]
            return {"method": method, "path": path}

        monkeypatch.setattr(S, "_api_request", _req)
        return calls

    @pytest.mark.asyncio
    async def test_account_auto_resolution(self, tg_api):
        out = await S._handle_call_tool(None, _params("telegram_list_channels"))
        assert not out.is_error
        assert tg_api[-1][1] == "/api/v1/telegram/tg-acc-1/channels"

    @pytest.mark.asyncio
    async def test_explicit_account_id(self, tg_api):
        await S._handle_call_tool(None, _params("telegram_list_channels", account_id="tg-9"))
        assert tg_api[-1][1] == "/api/v1/telegram/tg-9/channels"

    @pytest.mark.asyncio
    async def test_no_telegram_account(self, monkeypatch):
        async def _req(method, path, json_body=None, params=None):
            return []

        monkeypatch.setattr(S, "_api_request", _req)
        out = await S._handle_call_tool(None, _params("telegram_list_channels"))
        assert out.is_error
        assert "No active Telegram account" in _text(out)["error"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name,args,method,path",
        [
            ("telegram_chat_info", {"chat_id": -100}, "GET", "/api/v1/telegram/tg-acc-1/chat-info"),
            ("telegram_chat_admins", {"chat_id": -100}, "GET", "/api/v1/telegram/tg-acc-1/chat-admins"),
            ("telegram_member_count", {"chat_id": -100}, "GET", "/api/v1/telegram/tg-acc-1/chat-members"),
            ("telegram_set_chat_description", {"chat_id": -100, "description": "d"}, "PUT", "/api/v1/telegram/tg-acc-1/chat-description"),
            ("telegram_create_invite_link", {"chat_id": -100}, "POST", "/api/v1/telegram/tg-acc-1/invite-link"),
            ("telegram_send_message", {"chat_id": -100, "text": "hi"}, "POST", "/api/v1/telegram/tg-acc-1/send"),
            ("telegram_pin_message", {"chat_id": -100, "message_id": 5}, "POST", "/api/v1/telegram/tg-acc-1/pin"),
        ],
    )
    async def test_dispatch(self, tg_api, name, args, method, path):
        out = await S._handle_call_tool(None, _params(name, **args))
        assert not out.is_error
        assert tg_api[-1][0] == method and tg_api[-1][1] == path

    @pytest.mark.asyncio
    async def test_invite_link_body_options(self, tg_api):
        await S._handle_call_tool(None, CallToolRequestParams(
            name="telegram_create_invite_link",
            arguments={"chat_id": -1, "name": "ig", "member_limit": 50},
        ))
        assert tg_api[-1][2] == {"chat_id": -1, "name": "ig", "member_limit": 50}

    @pytest.mark.asyncio
    async def test_send_parse_mode_optional(self, tg_api):
        await S._handle_call_tool(None, _params(
            "telegram_send_message", chat_id=-1, text="hi", parse_mode="HTML"
        ))
        assert tg_api[-1][2]["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_unknown_telegram_tool(self, tg_api):
        out = await S._handle_call_tool(None, _params("telegram_nope"))
        assert out.is_error
        assert "Unknown telegram tool" in _text(out)["error"]
