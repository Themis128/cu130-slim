"""Unit tests for app.api.messenger — the big Messenger router.

Covers page-account authz, messenger client construction, icebreaker lead
upsert, setup/profile/send/conversation endpoints, webhook signature +
receive, inline auto-reply pipeline, personal (browser-bridge) endpoints,
thread controls, and the bot builder. Route functions are invoked directly
with fake sessions; MessengerAPIClient, browser bridge, httpx and the
messenger_chatbot service are monkeypatched. No network, no database.
"""
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import messenger
from app.core.security import encrypt_token
from app.models.social_account import SocialAccount
from app.models.user import TeamMember, User

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Scalars:
    def __init__(self, items):
        self._items = list(items)

    def first(self):
        return self._items[0] if self._items else None

    def all(self):
        return list(self._items)


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        if isinstance(self._v, list):
            return self._v[0] if self._v else None
        return self._v

    def scalars(self):
        v = self._v if isinstance(self._v, list) else ([self._v] if self._v else [])
        return _Scalars(v)


class FakeDB:
    def __init__(self, accounts=(), memberships=(), brands=()):
        self.accounts = list(accounts)
        self.memberships = list(memberships)
        self.brands = list(brands)
        self.commits = 0

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        cparams = dict(stmt.compile().params)

        def _vals(prefix):
            return [v for k, v in cparams.items() if k == prefix or k.startswith(prefix + "_")]

        if entity is SocialAccount:
            res = list(self.accounts)
            ids = _vals("id")
            if ids:
                res = [a for a in res if a.id in ids]
            ext = _vals("account_id")
            if ext:
                res = [a for a in res if a.account_id in ext]
            types = _vals("account_type")
            if types:
                res = [a for a in res if a.account_type in types]
            return _Result(res)
        if entity is TeamMember:
            return _Result([
                m for m in self.memberships
                if m.user_id in _vals("user_id") or m.team_id in _vals("team_id")
            ])
        # Brand or other models — match on team_id when present
        teams = _vals("team_id")
        res = [b for b in self.brands if not teams or getattr(b, "team_id", None) in teams]
        return _Result(res)

    async def commit(self):
        self.commits += 1


def _user(email="u@x.io") -> User:
    return User(id=uuid.uuid4(), email=email, name="U")


def _admin() -> User:
    # email must equal SOCIAL_ADMIN_EMAIL exactly — including "" when unset —
    # for the helper's admin bypass to trigger
    return _user(email=messenger.settings.SOCIAL_ADMIN_EMAIL)


def _page(**kw) -> SocialAccount:
    meta = kw.pop("meta_data", {"page_token": "EApagetoken"})
    return SocialAccount(
        id=kw.get("id", uuid.uuid4()),
        team_id=kw.get("team_id", uuid.uuid4()),
        platform="facebook",
        account_id=kw.get("account_id", "pg-1"),
        username="pg", display_name="Page",
        status="active", is_business=True, account_type="page",
        access_token_enc=encrypt_token("at"), scopes=[],
        meta_data=meta,
    )


def _fb_user(**kw) -> SocialAccount:
    a = _page(**kw)
    a.account_type = "user"
    a.is_business = False
    return a


class _MsgClient:
    """Fake MessengerAPIClient — records calls, configurable results."""
    calls = []
    page_info = {"name": "Cloudless", "website": "https://cloudless.gr"}
    profile = {"greeting": [{"locale": "default"}], "get_started": {"payload": "GO"}}
    subscribed_apps = [{"id": "app"}]
    conversations = [{"id": "t1", "snippet": "hi", "updated_time": "now",
                      "participants": {"data": [{"id": "u1"}]}}]
    messages = [{"id": "m1", "message": "hello", "from": {"id": "u1"},
                 "created_time": "t"}]
    error: Exception | None = None
    fail_methods: set = set()

    def __init__(self, **kw):
        type(self).calls = []
        self.kw = kw

    async def _maybe(self, name):
        type(self).calls.append(name)
        if name in type(self).fail_methods:
            raise RuntimeError(f"{name} boom")

    async def get_page_info(self):
        await self._maybe("get_page_info")
        return type(self).page_info

    async def subscribe_page(self):
        await self._maybe("subscribe_page")
        return {"success": True}

    async def setup_default_profile(self, **kw):
        await self._maybe("setup_default_profile")
        return {"result": "ok"}

    async def get_messenger_profile(self):
        await self._maybe("get_messenger_profile")
        return type(self).profile

    async def get_subscribed_apps(self):
        await self._maybe("get_subscribed_apps")
        return type(self).subscribed_apps

    async def set_messenger_profile(self, profile):
        await self._maybe("set_messenger_profile")
        return {"result": "ok", "fields": list(profile)}

    async def delete_messenger_profile_fields(self, fields):
        await self._maybe("delete_messenger_profile_fields")
        return {"result": "deleted"}

    async def unsubscribe_page(self):
        await self._maybe("unsubscribe_page")
        return {"success": True}

    async def send_text(self, psid, text, **kw):
        await self._maybe("send_text")
        return {"recipient_id": psid, "message_id": "mid"}

    async def send_image_url(self, psid, url):
        await self._maybe("send_image_url")
        return {"recipient_id": psid}

    async def send_quick_replies(self, psid, text, qr):
        await self._maybe("send_quick_replies")
        return {"recipient_id": psid}

    async def get_conversations(self, **kw):
        await self._maybe("get_conversations")
        return type(self).conversations

    async def get_conversation_messages(self, cid, **kw):
        await self._maybe("get_conversation_messages")
        return type(self).messages

    async def get_user_profile(self, psid):
        await self._maybe("get_user_profile")
        return {"first_name": "F", "id": psid}

    async def send_sender_action(self, psid, action):
        await self._maybe(f"sender_action:{action}")
        return {}


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    monkeypatch.setattr(messenger, "MessengerAPIClient", _MsgClient)
    # _generate_and_send_auto_reply does a local `from app.services.messenger_api
    # import MessengerAPIClient` — patch at the source module too
    monkeypatch.setattr("app.services.messenger_api.MessengerAPIClient", _MsgClient)
    _MsgClient.fail_methods = set()
    _MsgClient.calls = []
    yield


# ---------------------------------------------------------------------------
# Authz helpers + client builder
# ---------------------------------------------------------------------------

class TestPageAccountLookup:
    pytestmark = pytest.mark.asyncio

    async def test_missing_404(self):
        with pytest.raises(HTTPException) as e:
            await messenger._get_facebook_page_account(FakeDB(), uuid.uuid4(), _user())
        assert e.value.status_code == 404

    async def test_wrong_type_400(self):
        a = _fb_user()
        with pytest.raises(HTTPException) as e:
            await messenger._get_facebook_page_account(FakeDB(accounts=[a]), a.id, _user())
        assert e.value.status_code == 400

    async def test_non_member_403(self):
        a = _page()
        with pytest.raises(HTTPException) as e:
            await messenger._get_facebook_page_account(
                FakeDB(accounts=[a]), a.id, _user(),
            )
        assert e.value.status_code == 403

    async def test_member_ok(self):
        a = _page()
        u = _user()
        m = TeamMember(team_id=a.team_id, user_id=u.id)
        out = await messenger._get_facebook_page_account(
            FakeDB(accounts=[a], memberships=[m]), a.id, u,
        )
        assert out is a

    async def test_admin_bypass(self):
        a = _page()
        out = await messenger._get_facebook_page_account(
            FakeDB(accounts=[a]), a.id, _admin(),
        )
        assert out is a


class TestMessengerClientBuilder:
    def test_no_page_token_400(self):
        a = _page(meta_data={})
        with pytest.raises(HTTPException) as e:
            messenger._get_messenger_client(a)
        assert e.value.status_code == 400

    def test_plaintext_token_passthrough(self):
        a = _page(meta_data={"page_token": "EAApg123"})
        c = messenger._get_messenger_client(a)
        assert c.kw["access_token"] == "EAApg123"

    def test_encrypted_token_decrypted(self):
        a = _page(meta_data={"page_token": encrypt_token("EAApg123").decode()})
        c = messenger._get_messenger_client(a)
        assert c.kw["access_token"] == "EAApg123"


class TestIcebreakerInterest:
    def test_mapping(self):
        assert messenger._icebreaker_payload_to_interest("LEAD_CLOUD") == "cloud"
        assert messenger._icebreaker_payload_to_interest(" lead_growth ") == "growth"
        assert messenger._icebreaker_payload_to_interest("LEAD_AUDIT") == "audit"

    def test_unknown_returns_none(self):
        assert messenger._icebreaker_payload_to_interest("OTHER") is None
        assert messenger._icebreaker_payload_to_interest("") is None


# ---------------------------------------------------------------------------
# Setup / profile / unsubscribe
# ---------------------------------------------------------------------------

class TestSetup:
    pytestmark = pytest.mark.asyncio

    async def test_setup_happy(self, monkeypatch):
        a = _page()
        db = FakeDB(accounts=[a])
        out = await messenger.setup_messenger(a.id, None, db, _admin())
        assert out["status"] == "ok" and out["page_name"] == "Cloudless"
        assert a.meta_data["messenger_setup"]["subscribed"] is True
        assert db.commits == 1

    async def test_setup_page_url_comma_split(self):
        _MsgClient.page_info = {"name": "P", "website": "https://a.gr/, https://wa.me/1"}
        a = _page()
        out = await messenger.setup_messenger(a.id, None, FakeDB(accounts=[a]), _admin())
        assert out["page_url"] == "https://a.gr/"
        _MsgClient.page_info = {"name": "Cloudless", "website": "https://cloudless.gr"}

    async def test_setup_page_info_failure_uses_fallback(self):
        _MsgClient.fail_methods = {"get_page_info"}
        a = _page()
        out = await messenger.setup_messenger(a.id, None, FakeDB(accounts=[a]), _admin())
        assert out["status"] == "ok" and out["page_name"] == "Page"

    async def test_setup_subscribe_failure_502(self):
        _MsgClient.fail_methods = {"subscribe_page"}
        a = _page()
        with pytest.raises(HTTPException) as e:
            await messenger.setup_messenger(a.id, None, FakeDB(accounts=[a]), _admin())
        assert e.value.status_code == 502

    async def test_setup_rate_limited_partial_success(self, monkeypatch):
        async def rl(self, **kw):
            raise RuntimeError("(#613) rate limit exceeded")
        monkeypatch.setattr(_MsgClient, "setup_default_profile", rl)
        a = _page()
        out = await messenger.setup_messenger(a.id, None, FakeDB(accounts=[a]), _admin())
        assert out["profile"]["result"] == "rate_limited"

    async def test_get_profile_happy(self):
        a = _page()
        out = await messenger.get_messenger_profile(a.id, FakeDB(accounts=[a]), _admin())
        assert out.subscribed is True and out.page_name == "Cloudless"
        assert out.greeting == [{"locale": "default"}]

    async def test_get_profile_error_502(self):
        _MsgClient.fail_methods = {"get_messenger_profile"}
        a = _page()
        with pytest.raises(HTTPException) as e:
            await messenger.get_messenger_profile(a.id, FakeDB(accounts=[a]), _admin())
        assert e.value.status_code == 502

    async def test_update_profile_no_fields_400(self):
        a = _page()
        with pytest.raises(HTTPException) as e:
            await messenger.update_messenger_profile(
                a.id, messenger.MessengerProfileUpdate(), FakeDB(accounts=[a]), _admin(),
            )
        assert e.value.status_code == 400

    async def test_update_profile_happy(self):
        a = _page()
        out = await messenger.update_messenger_profile(
            a.id,
            messenger.MessengerProfileUpdate(greeting=[{"locale": "en"}]),
            FakeDB(accounts=[a]), _admin(),
        )
        assert out["fields"] == ["greeting"]

    async def test_delete_profile_fields(self):
        a = _page()
        out = await messenger.delete_messenger_profile_fields(
            a.id, ["persistent_menu"], FakeDB(accounts=[a]), _admin(),
        )
        assert out["result"] == "deleted"

    async def test_unsubscribe_clears_meta(self):
        a = _page(meta_data={"page_token": "EAApg",
                             "messenger_setup": {"subscribed": True}})
        db = FakeDB(accounts=[a])
        out = await messenger.unsubscribe_messenger(a.id, db, _admin())
        assert out["success"] is True
        assert a.meta_data["messenger_setup"]["subscribed"] is False


# ---------------------------------------------------------------------------
# Send + conversations
# ---------------------------------------------------------------------------

class TestSendAndConversations:
    pytestmark = pytest.mark.asyncio

    async def test_send_no_content_400(self):
        a = _page()
        with pytest.raises(HTTPException) as e:
            await messenger.send_message(
                a.id, messenger.SendMessageRequest(recipient_psid="p1"),
                FakeDB(accounts=[a]), _admin(),
            )
        assert e.value.status_code == 400

    async def test_send_text(self):
        a = _page()
        out = await messenger.send_message(
            a.id, messenger.SendMessageRequest(recipient_psid="p1", text="hi"),
            FakeDB(accounts=[a]), _admin(),
        )
        assert out["message_id"] == "mid" and "send_text" in _MsgClient.calls

    async def test_send_image(self):
        a = _page()
        await messenger.send_message(
            a.id, messenger.SendMessageRequest(recipient_psid="p1",
                                               image_url="https://x/i.png"),
            FakeDB(accounts=[a]), _admin(),
        )
        assert "send_image_url" in _MsgClient.calls

    async def test_send_quick_replies(self):
        a = _page()
        out = await messenger.send_quick_replies(
            a.id,
            messenger.SendQuickRepliesRequest(
                recipient_psid="p1", text="pick", quick_replies=[{"title": "A"}],
            ),
            FakeDB(accounts=[a]), _admin(),
        )
        assert out["recipient_id"] == "p1"

    async def test_list_conversations_shape(self):
        a = _page()
        out = await messenger.list_conversations(a.id, 25, "messenger",
                                                 FakeDB(accounts=[a]), _admin())
        assert out[0].id == "t1" and out[0].participants == [{"id": "u1"}]

    async def test_conversation_messages_shape(self):
        a = _page()
        out = await messenger.get_conversation_messages(
            a.id, "t1", 20, FakeDB(accounts=[a]), _admin(),
        )
        assert out[0].message == "hello" and out[0].from_id == "u1"

    async def test_get_user_profile(self):
        a = _page()
        out = await messenger.get_user_profile(a.id, "psid-9",
                                               FakeDB(accounts=[a]), _admin())
        assert out["first_name"] == "F"


# ---------------------------------------------------------------------------
# Auto-reply config
# ---------------------------------------------------------------------------

class TestAutoReplyConfig:
    pytestmark = pytest.mark.asyncio

    async def test_get_defaults(self):
        a = _page()
        out = await messenger.get_auto_reply_config(a.id, FakeDB(accounts=[a]), _admin())
        assert out.enabled is False and out.max_tokens == 200

    async def test_update_disabled_no_plan_check(self):
        a = _page()
        db = FakeDB(accounts=[a])
        cfg = messenger.AutoReplyConfig(enabled=False, fallback_text="fb")
        out = await messenger.update_auto_reply_config(a.id, cfg, db, _admin())
        assert out.fallback_text == "fb"
        assert a.meta_data["messenger_auto_reply"]["fallback_text"] == "fb"
        assert db.commits == 1

    async def test_update_enabled_calls_plan_check(self, monkeypatch):
        called = []

        async def chk(feature, team_id, db):
            called.append(feature)

        monkeypatch.setattr("app.api.deps.check_plan_feature", chk)
        a = _page()
        cfg = messenger.AutoReplyConfig(enabled=True)
        await messenger.update_auto_reply_config(a.id, cfg, FakeDB(accounts=[a]), _admin())
        assert called == ["dm_auto_reply"]


# ---------------------------------------------------------------------------
# Webhook: signature + verify + receive
# ---------------------------------------------------------------------------

class TestWebhookVerify:
    def test_signature_valid(self):
        body = b'{"a":1}'
        secret = "s3cret"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert messenger._verify_webhook_signature(body, f"sha256={sig}", secret) is True

    def test_signature_invalid(self):
        assert messenger._verify_webhook_signature(b"{}", "sha256=deadbeef", "s") is False
        assert messenger._verify_webhook_signature(b"{}", "", "s") is False
        assert messenger._verify_webhook_signature(b"{}", "sha1=x", "s") is False

    @pytest.mark.asyncio
    async def test_verify_challenge_echo(self, monkeypatch):
        monkeypatch.setattr(messenger.settings, "MESSENGER_VERIFY_TOKEN", "tok")
        out = await messenger.verify_webhook("subscribe", "tok", "CHALLENGE42")
        assert out.body == b"CHALLENGE42"

    @pytest.mark.asyncio
    async def test_verify_bad_token_403(self, monkeypatch):
        monkeypatch.setattr(messenger.settings, "MESSENGER_VERIFY_TOKEN", "tok")
        with pytest.raises(HTTPException) as e:
            await messenger.verify_webhook("subscribe", "wrong", "x")
        assert e.value.status_code == 403


async def _async(v):
    return v


class _H:
    def __init__(self, d):
        self._d = {k.lower(): v for k, v in d.items()}

    def get(self, k, default=None):
        return self._d.get(k.lower(), default)


def _request(body: bytes, headers: dict | None = None):
    async def body_fn():
        return body
    return SimpleNamespace(body=body_fn, headers=_H(headers or {}))


class TestReceiveWebhook:
    pytestmark = pytest.mark.asyncio

    def _payload(self, events):
        return json.dumps({"object": "page", "entry": events}).encode()

    async def test_bad_signature_403(self, monkeypatch):
        monkeypatch.setattr(messenger.settings, "FACEBOOK_APP_SECRET", "sec")
        req = _request(b"{}", {"X-Hub-Signature-256": "sha256=bad"})
        with pytest.raises(HTTPException) as e:
            await messenger.receive_webhook(req, FakeDB())
        assert e.value.status_code == 403

    async def test_valid_signature_passes(self, monkeypatch):
        monkeypatch.setattr(messenger.settings, "FACEBOOK_APP_SECRET", "sec")
        body = self._payload([])
        sig = hmac.new(b"sec", body, hashlib.sha256).hexdigest()
        monkeypatch.setattr(messenger, "parse_webhook_event", lambda b: [])
        out = await messenger.receive_webhook(
            _request(body, {"X-Hub-Signature-256": f"sha256={sig}"}), FakeDB(),
        )
        assert out["status"] == "ok" and out["events_received"] == 0

    async def test_icebreaker_lead_upsert_called(self, monkeypatch):
        monkeypatch.setattr(messenger.settings, "FACEBOOK_APP_SECRET", "")
        monkeypatch.delenv("FACEBOOK_APP_SECRET", raising=False)
        seen = []

        async def upsert(db, **kw):
            seen.append(kw["payload"])

        monkeypatch.setattr(messenger, "_upsert_icebreaker_lead", upsert)
        monkeypatch.setattr(messenger, "parse_webhook_event", lambda b: [
            {"page_id": "pg-1", "sender_psid": "u1", "message_type": "postback",
             "postback_payload": "LEAD_AUDIT", "message_id": "m1", "timestamp": 1},
        ])
        monkeypatch.setattr(messenger, "_process_inline", lambda *a, **k: _async(None))
        monkeypatch.setenv("MESSENGER_SIDECAR_URL", "")
        monkeypatch.setattr(messenger.settings, "MESSENGER_SIDECAR_URL", "")
        out = await messenger.receive_webhook(_request(b"{}"), FakeDB())
        assert seen == ["LEAD_AUDIT"] and out["auto_replies_sent"] == 1

    async def test_inline_dispatch_when_no_sidecar(self, monkeypatch):
        monkeypatch.setattr(messenger.settings, "FACEBOOK_APP_SECRET", "")
        monkeypatch.delenv("FACEBOOK_APP_SECRET", raising=False)
        monkeypatch.setattr(messenger.settings, "MESSENGER_SIDECAR_URL", "")
        monkeypatch.setenv("MESSENGER_SIDECAR_URL", "")
        monkeypatch.setattr(messenger, "parse_webhook_event", lambda b: [
            {"page_id": "pg-1", "sender_psid": "u1", "message_type": "text",
             "message_text": "hi", "postback_payload": "", "timestamp": 1},
        ])
        calls = []

        async def proc(db, page_id, psid, text, mtype, payload):
            calls.append((page_id, psid, text))

        monkeypatch.setattr(messenger, "_process_inline", proc)
        out = await messenger.receive_webhook(_request(b"{}"), FakeDB())
        assert calls == [("pg-1", "u1", "hi")] and out["auto_replies_sent"] == 1

    async def test_sidecar_dispatch_success(self, monkeypatch):
        monkeypatch.setattr(messenger.settings, "FACEBOOK_APP_SECRET", "")
        monkeypatch.delenv("FACEBOOK_APP_SECRET", raising=False)
        monkeypatch.setattr(messenger.settings, "MESSENGER_SIDECAR_URL", "http://sc:9")
        monkeypatch.setattr(messenger, "parse_webhook_event", lambda b: [
            {"page_id": "pg-1", "sender_psid": "u1", "message_type": "text",
             "message_text": "hi", "postback_payload": "", "timestamp": 1},
        ])
        posts = []

        class FakeHTTP:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None):
                posts.append((url, json["sender_psid"]))
                return SimpleNamespace(status_code=200)

        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeHTTP())
        out = await messenger.receive_webhook(_request(b"{}"), FakeDB())
        assert posts == [("http://sc:9/process", "u1")] and out["auto_replies_sent"] == 1

    async def test_sidecar_failure_falls_back_inline(self, monkeypatch):
        monkeypatch.setattr(messenger.settings, "FACEBOOK_APP_SECRET", "")
        monkeypatch.delenv("FACEBOOK_APP_SECRET", raising=False)
        monkeypatch.setattr(messenger.settings, "MESSENGER_SIDECAR_URL", "http://sc:9")
        monkeypatch.setattr(messenger, "parse_webhook_event", lambda b: [
            {"page_id": "pg-1", "sender_psid": "u1", "message_type": "text",
             "message_text": "hi", "postback_payload": "", "timestamp": 1},
        ])

        class FakeHTTP:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                raise ConnectionError("sidecar down")

        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeHTTP())
        calls = []

        async def proc(db, page_id, psid, text, mtype, payload):
            calls.append(psid)

        monkeypatch.setattr(messenger, "_process_inline", proc)
        out = await messenger.receive_webhook(_request(b"{}"), FakeDB())
        assert calls == ["u1"] and out["auto_replies_sent"] == 1


# ---------------------------------------------------------------------------
# Inline processing + AI generation
# ---------------------------------------------------------------------------

class TestProcessInline:
    pytestmark = pytest.mark.asyncio

    async def test_no_account_returns(self):
        await messenger._process_inline(FakeDB(), "pg-x", "u1", "hi", "text")

    async def test_auto_reply_disabled_returns(self):
        a = _page(meta_data={"page_token": "EAApg"})
        await messenger._process_inline(
            FakeDB(accounts=[a]), "pg-1", "u1", "hi", "text",
        )
        assert not _MsgClient.calls  # lead capture produced nothing, no bot

    async def test_auto_reply_enabled_sends(self, monkeypatch):
        a = _page(meta_data={
            "page_token": "EAApg",
            "messenger_auto_reply": {"enabled": True},
        })

        monkeypatch.setattr(
            messenger, "_generate_and_send_auto_reply",
            lambda *a: _async(_MsgClient.calls.append("auto_reply")),
        )
        await messenger._process_inline(
            FakeDB(accounts=[a]), "pg-1", "u1", "hi", "text",
        )
        assert "auto_reply" in _MsgClient.calls


class TestGenerateAIResponse:
    pytestmark = pytest.mark.asyncio

    async def test_cloudflare_path(self, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")

        class Resp:
            status_code = 200

            def json(self):
                return {"result": {"response": "  AI says hi  "}}

        class FakeHTTP:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return Resp()

        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeHTTP())
        out = await messenger._generate_ai_response("sys", "hi", "m", 50, "fb")
        assert out == "AI says hi"

    async def test_dmr_fallback(self, monkeypatch):
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)

        class Resp:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "DMR reply"}}]}

        class FakeHTTP:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, **kw):
                assert "chat/completions" in url
                return Resp()

        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeHTTP())
        out = await messenger._generate_ai_response("sys", "hi", "m", 50, "fb")
        assert out == "DMR reply"

    async def test_all_fail_returns_fallback(self, monkeypatch):
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)

        class FakeHTTP:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                raise ConnectionError("down")

        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeHTTP())
        out = await messenger._generate_ai_response("sys", "hi", "m", 50, "fb")
        assert out == "fb"


class TestGenerateAndSend:
    pytestmark = pytest.mark.asyncio

    async def test_no_page_token_returns(self):
        a = _page(meta_data={})
        await messenger._generate_and_send_auto_reply(a, "u1", "hi", {})
        assert not _MsgClient.calls

    async def test_sends_typing_and_reply(self, monkeypatch):
        a = _page(meta_data={"page_token": "EAApg"})
        monkeypatch.setattr(messenger, "_generate_ai_response",
                            lambda *a: _async("bot says"))
        await messenger._generate_and_send_auto_reply(a, "u1", "hi", {})
        assert "sender_action:typing_on" in _MsgClient.calls
        assert "send_text" in _MsgClient.calls
        assert "sender_action:typing_off" in _MsgClient.calls

    async def test_ai_failure_uses_fallback(self, monkeypatch):
        async def boom(*a):
            raise RuntimeError("ai down")

        monkeypatch.setattr(messenger, "_generate_ai_response", boom)
        a = _page(meta_data={"page_token": "EAApg"})
        await messenger._generate_and_send_auto_reply(
            a, "u1", "hi", {"fallback_text": "sorry"},
        )
        assert "send_text" in _MsgClient.calls


# ---------------------------------------------------------------------------
# Sidecar status
# ---------------------------------------------------------------------------

class TestSidecarStatus:
    pytestmark = pytest.mark.asyncio

    async def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("MESSENGER_SIDECAR_URL", raising=False)
        out = await messenger.sidecar_status(_user())
        assert out["status"] == "not_configured"

    async def test_online(self, monkeypatch):
        monkeypatch.setenv("MESSENGER_SIDECAR_URL", "http://sc:9")

        class Resp:
            status_code = 200

            def json(self):
                return {"ok": True}

        class FakeHTTP:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                return Resp()

        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeHTTP())
        out = await messenger.sidecar_status(_user())
        assert out["status"] == "online" and out["health"] == {"ok": True}

    async def test_error_status(self, monkeypatch):
        monkeypatch.setenv("MESSENGER_SIDECAR_URL", "http://sc:9")

        class Resp:
            status_code = 500

            def json(self):
                return {}

        class FakeHTTP:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                return Resp()

        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeHTTP())
        out = await messenger.sidecar_status(_user())
        assert out["status"] == "error" and "500" in out["message"]

    async def test_offline(self, monkeypatch):
        monkeypatch.setenv("MESSENGER_SIDECAR_URL", "http://sc:9")

        class FakeHTTP:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                raise ConnectionError("down")

        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **kw: FakeHTTP())
        out = await messenger.sidecar_status(_user())
        assert out["status"] == "offline"


# ---------------------------------------------------------------------------
# Personal (browser-bridge) endpoints
# ---------------------------------------------------------------------------

class _FakeBridge:
    session_status = "active"
    session_message = ""
    novnc_url = ""
    error = None
    conversations_result = {"threads": [{"id": "t1"}]}
    messages_result = {"messages": [{"text": "hi"}]}
    send_result = {"sent": True}

    def __init__(self, url, platform=None):
        self.url = url

    async def ensure_session(self, platform):
        return {"status": type(self).session_status,
                "message": type(self).session_message,
                "novnc_url": type(self).novnc_url}

    async def get_personal_messenger_conversations(self):
        if type(self).error:
            raise type(self).error
        return type(self).conversations_result

    async def get_personal_messenger_messages(self, tid, is_e2ee=False):
        if type(self).error:
            raise type(self).error
        return type(self).messages_result

    async def send_personal_messenger_message(self, tid, text, is_e2ee=False):
        if type(self).error:
            raise type(self).error
        return type(self).send_result


class TestPersonalEndpoints:
    pytestmark = pytest.mark.asyncio

    def _patch(self, monkeypatch):
        import app.services.browser_bridge as bb
        monkeypatch.setattr(bb, "BrowserBridgeClient", _FakeBridge)
        _FakeBridge.session_status = "active"
        _FakeBridge.error = None

    async def test_wrong_account_type_400(self):
        a = _page()  # page, not user
        with pytest.raises(HTTPException) as e:
            await messenger.get_personal_conversations(
                a.id, _admin(), FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 400

    async def test_inactive_session_503(self, monkeypatch):
        self._patch(monkeypatch)
        _FakeBridge.session_status = "needs_login"
        _FakeBridge.session_message = "log in first"
        a = _fb_user()
        with pytest.raises(HTTPException) as e:
            await messenger.get_personal_conversations(
                a.id, _admin(), FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 503 and "log in first" in e.value.detail

    async def test_conversations_ok(self, monkeypatch):
        self._patch(monkeypatch)
        a = _fb_user()
        out = await messenger.get_personal_conversations(
            a.id, _admin(), FakeDB(accounts=[a]),
        )
        assert out["threads"][0]["id"] == "t1"

    async def test_messages_ok(self, monkeypatch):
        self._patch(monkeypatch)
        a = _fb_user()
        out = await messenger.get_personal_messages(
            a.id, "t1", True, _admin(), FakeDB(accounts=[a]),
        )
        assert out["messages"][0]["text"] == "hi"

    async def test_send_ok(self, monkeypatch):
        self._patch(monkeypatch)
        a = _fb_user()
        req = messenger.PersonalMessageSendRequest(thread_id="t1", text="yo")
        out = await messenger.send_personal_message(
            a.id, req, _admin(), FakeDB(accounts=[a]),
        )
        assert out["sent"] is True

    async def test_personal_auto_reply_get_defaults(self):
        a = _fb_user()
        out = await messenger.get_personal_auto_reply(
            a.id, _admin(), FakeDB(accounts=[a]),
        )
        assert out["enabled"] is False and out["max_tokens"] == 300

    async def test_personal_auto_reply_update(self, monkeypatch):
        async def chk(*a, **k):
            return None
        monkeypatch.setattr("app.api.deps.check_plan_feature", chk)
        a = _fb_user()
        db = FakeDB(accounts=[a])
        cfg = messenger.PersonalAutoReplyConfig(enabled=True, model="ai/x")
        out = await messenger.update_personal_auto_reply(
            a.id, cfg, _admin(), db,
        )
        assert out["status"] == "ok"
        assert a.meta_data["personal_messenger_auto_reply"]["model"] == "ai/x"


class TestThreadControls:
    pytestmark = pytest.mark.asyncio

    async def test_pause_thread(self, monkeypatch):
        seen = []

        async def pause(acct, tid, reason):
            seen.append((acct, tid, reason))

        monkeypatch.setattr("app.services.messenger_chatbot.pause_thread", pause)
        a = _fb_user()
        out = await messenger.pause_thread(
            a.id, "t1", messenger.ThreadPauseRequest(thread_id="t1"),
            _admin(), FakeDB(accounts=[a]),
        )
        assert out["status"] == "ok" and seen[0][1] == "t1"

    async def test_resume_thread(self, monkeypatch):
        seen = []

        async def resume(acct, tid):
            seen.append(tid)

        monkeypatch.setattr("app.services.messenger_chatbot.resume_thread", resume)
        a = _fb_user()
        out = await messenger.resume_thread(a.id, "t1", _admin(), FakeDB(accounts=[a]))
        assert seen == ["t1"] and out["status"] == "ok"

    async def test_get_set_thread_config(self, monkeypatch):
        monkeypatch.setattr("app.services.messenger_chatbot.get_thread_config",
                            lambda *a: _async({"model": "ai/x"}))
        set_calls = []

        async def set_cfg(acct, tid, cfg):
            set_calls.append(cfg)

        monkeypatch.setattr("app.services.messenger_chatbot.set_thread_config", set_cfg)
        a = _fb_user()
        got = await messenger.get_thread_config(a.id, "t1", _admin(), FakeDB(accounts=[a]))
        assert got["config"]["model"] == "ai/x"
        await messenger.set_thread_config(
            a.id, "t1", messenger.ThreadConfigRequest(thread_id="t1", model="ai/y"),
            _admin(), FakeDB(accounts=[a]),
        )
        assert set_calls == [{"model": "ai/y"}]

    async def test_thread_memory(self, monkeypatch):
        monkeypatch.setattr("app.services.messenger_chatbot.get_conversation_memory",
                            lambda *a: _async([{"role": "user", "content": "hi"}]))
        a = _fb_user()
        out = await messenger.get_thread_memory(a.id, "t1", _admin(), FakeDB(accounts=[a]))
        assert out["count"] == 1

    async def test_index_brand_no_brand(self):
        a = _fb_user()
        out = await messenger.index_brand(a.id, _admin(), FakeDB(accounts=[a]))
        assert out["status"] == "error"

    async def test_index_brand_ok(self, monkeypatch):
        class B:
            team_id = None
            name = "Cloudless"

        idx = []

        async def index(tid, data):
            idx.append(data["name"])
            return 5

        monkeypatch.setattr("app.services.messenger_chatbot.index_brand_knowledge", index)
        a = _fb_user()
        b = B()
        b.team_id = a.team_id
        out = await messenger.index_brand(a.id, _admin(), FakeDB(accounts=[a], brands=[b]))
        assert out == {"status": "ok", "indexed": 5}


# ---------------------------------------------------------------------------
# Bot builder
# ---------------------------------------------------------------------------

class TestBotBuilder:
    pytestmark = pytest.mark.asyncio

    async def test_messenger_account_404(self):
        with pytest.raises(HTTPException) as e:
            await messenger._get_messenger_account(
                FakeDB(), uuid.uuid4(), uuid.uuid4(), _user(),
            )
        assert e.value.status_code == 404

    async def test_messenger_account_wrong_platform_400(self):
        a = _page()
        a.platform = "twitter"
        with pytest.raises(HTTPException) as e:
            await messenger._get_messenger_account(
                FakeDB(accounts=[a]), a.id, a.team_id, _user(),
            )
        assert e.value.status_code == 400

    async def test_messenger_account_team_mismatch_403(self):
        a = _page()
        with pytest.raises(HTTPException) as e:
            await messenger._get_messenger_account(
                FakeDB(accounts=[a]), a.id, uuid.uuid4(), _user(),
            )
        assert e.value.status_code == 403

    async def test_create_bot_user_account(self, monkeypatch):
        async def chk(*a, **k):
            return None
        monkeypatch.setattr("app.api.deps.check_plan_feature", chk)
        idx = []

        async def index(tid, data):
            idx.append(tid)
            return 3

        monkeypatch.setattr("app.services.messenger_chatbot.index_brand_knowledge", index)
        a = _fb_user()
        db = FakeDB(accounts=[a])
        req = messenger.BotCreateRequest(name="Helper", personality="casual",
                                       language="el")
        out = await messenger.create_bot(a.id, req, a.team_id, _admin(), db)
        assert out["account_type"] == "user"
        bot = a.meta_data["messenger_bot"]
        assert bot["enabled"] is True
        assert "Greek" in bot["system_prompt"]  # language constraint appended
        assert a.meta_data["personal_messenger_auto_reply"]["enabled"] is True
        assert db.commits == 1

    async def test_create_bot_page_subscribes(self, monkeypatch):
        async def chk(*a, **k):
            return None
        monkeypatch.setattr("app.api.deps.check_plan_feature", chk)
        monkeypatch.setattr("app.services.messenger_chatbot.index_brand_knowledge",
                            lambda *a: _async(0))
        a = _page()
        req = messenger.BotCreateRequest(name="B", custom_prompt="Custom for {page_name}")
        out = await messenger.create_bot(a.id, req, a.team_id, _admin(), FakeDB(accounts=[a]))
        assert out["account_type"] == "page"
        assert "subscribe_page" in _MsgClient.calls
        # custom_prompt interpolates the account display_name (prompt is built
        # before the Graph page-info refresh of page_name)
        assert a.meta_data["messenger_bot"]["system_prompt"] == "Custom for Page"
        assert a.meta_data["messenger_setup"]["bot_enabled"] is True

    async def test_get_bot_absent(self):
        a = _page()
        out = await messenger.get_bot(a.id, a.team_id, _admin(), FakeDB(accounts=[a]))
        assert out["exists"] is False

    async def test_get_bot_present(self, monkeypatch):
        monkeypatch.setattr("app.services.messenger_chatbot.is_thread_paused",
                            lambda *a: _async(True))
        a = _page(meta_data={"page_token": "EA",
                             "messenger_bot": {"name": "B", "enabled": True,
                                               "paused_threads": ["t1"]}})
        out = await messenger.get_bot(a.id, a.team_id, _admin(), FakeDB(accounts=[a]))
        assert out["exists"] is True
        assert out["paused_threads_status"] == {"t1": True}

    async def test_update_bot_syncs_personal(self):
        a = _fb_user()
        cfg = messenger.BotConfig(name="X", enabled=True, model="ai/m")
        db = FakeDB(accounts=[a])
        out = await messenger.update_bot(a.id, cfg, a.team_id, _admin(), db)
        assert out["status"] == "ok"
        ar = a.meta_data["personal_messenger_auto_reply"]
        assert ar["enabled"] is True and ar["model"] == "ai/m"

    async def test_activate_no_bot_400(self):
        a = _page()
        with pytest.raises(HTTPException) as e:
            await messenger.activate_bot(a.id, a.team_id, _admin(), FakeDB(accounts=[a]))
        assert e.value.status_code == 400

    async def test_activate_deactivate_cycle(self, monkeypatch):
        async def chk(*a, **k):
            return None
        monkeypatch.setattr("app.api.deps.check_plan_feature", chk)
        a = _page(meta_data={"page_token": "EA",
                             "messenger_bot": {"name": "B", "enabled": False}})
        db = FakeDB(accounts=[a])
        out = await messenger.activate_bot(a.id, a.team_id, _admin(), db)
        assert out["enabled"] is True
        assert a.meta_data["messenger_bot"]["enabled"] is True
        out = await messenger.deactivate_bot(a.id, a.team_id, _admin(), db)
        assert out["enabled"] is False

    async def test_bot_pause_resume_thread(self, monkeypatch):
        pauses, resumes = [], []

        async def pause(acct, tid, reason):
            pauses.append(tid)

        async def resume(acct, tid):
            resumes.append(tid)

        monkeypatch.setattr("app.services.messenger_chatbot.pause_thread", pause)
        monkeypatch.setattr("app.services.messenger_chatbot.resume_thread", resume)
        a = _page(meta_data={"page_token": "EA", "messenger_bot": {"name": "B"}})
        db = FakeDB(accounts=[a])
        out = await messenger.bot_pause_thread(a.id, "t1", a.team_id, _admin(), db)
        assert out["paused"] is True
        assert "t1" in a.meta_data["messenger_bot"]["paused_threads"]
        out = await messenger.bot_resume_thread(a.id, "t1", a.team_id, _admin(), db)
        assert out["paused"] is False
        assert "t1" not in a.meta_data["messenger_bot"]["paused_threads"]
        assert pauses == resumes == ["t1"]

    async def test_get_personalities(self):
        out = await messenger.get_bot_personalities(uuid.uuid4(), _user())
        ids = [p["id"] for p in out["personalities"]]
        assert "professional_friendly" in ids and "sales" in ids
