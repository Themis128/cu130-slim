"""Unit tests for app/api/messenger_api.py — Messenger Platform router."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import app.api.messenger_api as A


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    monkeypatch.setattr(A.settings, "SOCIAL_ADMIN_EMAIL", "admin@x.y",
                        raising=False)
    monkeypatch.setattr(A.settings, "MESSENGER_VERIFY_TOKEN", "vtok",
                        raising=False)
    monkeypatch.setattr(A, "flag_modified", Mock())


def _user(email="admin@x.y") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), email=email)


def _acct(**kw) -> SimpleNamespace:
    d = dict(
        id=uuid.uuid4(), team_id=uuid.uuid4(), platform="facebook",
        account_type="page", account_id="12345", display_name="MyPage",
        meta_data={"page_token": "EAtoken"})
    d.update(kw)
    return SimpleNamespace(**d)


class _Res:
    def __init__(self, scalars=None, one=None):
        self._scalars = scalars
        self._one = one

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars or [])

    def scalar_one_or_none(self):
        return self._one


class _DB:
    def __init__(self, queue=()):
        self.queue = list(queue)
        self.commits = 0

    async def execute(self, *a, **k):
        return self.queue.pop(0) if self.queue else _Res()

    async def commit(self):
        self.commits += 1

    async def flush(self):
        pass


def _client(**kw) -> SimpleNamespace:
    """Fake MessengerAPIClient — every method an AsyncMock."""
    c = SimpleNamespace()
    for name in ("get_page_info", "subscribe_page", "setup_default_profile",
                 "get_messenger_profile", "get_subscribed_apps",
                 "set_messenger_profile", "delete_messenger_profile_fields",
                 "unsubscribe_page", "send_text", "send_image_url",
                 "send_quick_replies", "get_conversations",
                 "get_conversation_messages", "get_user_profile",
                 "send_sender_action"):
        setattr(c, name, kw.pop(name, AsyncMock(return_value={})))
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _db_with(acct) -> _DB:
    return _DB(queue=[_Res(one=acct)])


# ── account + client helpers ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_facebook_page_account(monkeypatch):
    uid = uuid.uuid4()

    # not found → 404
    with pytest.raises(HTTPException) as e:
        await A._get_facebook_page_account(_DB(), uid, _user())
    assert e.value.status_code == 404

    # wrong platform/type → 400
    for acct in (_acct(platform="instagram"),
                 _acct(account_type="personal")):
        with pytest.raises(HTTPException) as e:
            await A._get_facebook_page_account(
                _db_with(acct), acct.id, _user())
        assert e.value.status_code == 400

    # admin bypass — no member check
    acct = _acct()
    out = await A._get_facebook_page_account(
        _db_with(acct), acct.id, _user())
    assert out is acct

    # non-admin member → allowed; non-member → 403
    acct = _acct()
    db = _DB(queue=[_Res(one=acct), _Res(one=object())])
    out = await A._get_facebook_page_account(db, acct.id, _user("m@x.y"))
    assert out is acct
    db = _DB(queue=[_Res(one=acct), _Res()])
    with pytest.raises(HTTPException) as e:
        await A._get_facebook_page_account(db, acct.id, _user("m@x.y"))
    assert e.value.status_code == 403


def test_get_messenger_client(monkeypatch):
    # no token → 400
    with pytest.raises(HTTPException) as e:
        A._get_messenger_client(_acct(meta_data={}))
    assert e.value.status_code == 400

    # EA-prefixed token → used verbatim
    c = A._get_messenger_client(_acct())
    assert c.access_token == "EAtoken" and c.page_id == "12345"

    # non-EA → decrypt attempt; decrypt failure → plaintext kept
    monkeypatch.setattr(A, "decrypt_token", lambda t: "DEC")
    c = A._get_messenger_client(_acct(meta_data={"page_token": "enc"}))
    assert c.access_token == "DEC"
    monkeypatch.setattr(A, "decrypt_token",
                        Mock(side_effect=Exception("bad")))
    c = A._get_messenger_client(_acct(meta_data={"page_token": "enc"}))
    assert c.access_token == "enc"


# ── setup + profile endpoints ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_messenger(monkeypatch):
    acct = _acct()

    # subscribe failure → 502
    cli = _client(subscribe_page=AsyncMock(
        side_effect=RuntimeError("sub fail")))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    with pytest.raises(HTTPException) as e:
        await A.setup_messenger(acct.id, None, _db_with(acct), _user())
    assert e.value.status_code == 502

    # page-info fails → fallback name; profile rate-limited → partial
    cli = _client(
        get_page_info=AsyncMock(side_effect=RuntimeError("x")),
        subscribe_page=AsyncMock(return_value={"ok": 1}),
        setup_default_profile=AsyncMock(
            side_effect=RuntimeError("613 rate limit")),
    )
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    db = _db_with(acct)
    out = await A.setup_messenger(acct.id, None, db, _user())
    assert out["profile"]["result"] == "rate_limited"
    assert out["page_name"] == "MyPage"
    assert acct.meta_data["messenger_setup"]["subscribed"] is True
    assert db.commits == 1

    # happy — comma-separated website → first URL taken
    cli = _client(
        get_page_info=AsyncMock(return_value={
            "name": "P", "website": "https://cloudless.gr/, https://x.co"}),
        subscribe_page=AsyncMock(return_value={"ok": 1}),
        setup_default_profile=AsyncMock(return_value={"done": 1}),
    )
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    out = await A.setup_messenger(
        acct.id, A.MessengerSetupRequest(greeting_text="hi"), _db_with(acct),
        _user())
    assert out["page_url"] == "https://cloudless.gr/"
    assert out["profile"] == {"done": 1}


@pytest.mark.asyncio
async def test_profile_endpoints(monkeypatch):
    acct = _acct()

    # get profile — sub apps + page info failures tolerated
    cli = _client(
        get_messenger_profile=AsyncMock(return_value={
            "greeting": [{"g": 1}], "get_started": {"p": 1}}),
        get_subscribed_apps=AsyncMock(side_effect=RuntimeError("x")),
        get_page_info=AsyncMock(side_effect=RuntimeError("x")),
    )
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    out = await A.get_messenger_profile(acct.id, _db_with(acct), _user())
    assert out.greeting == [{"g": 1}] and out.subscribed is False
    assert out.page_name == "MyPage"

    # get profile error → 502
    cli = _client(get_messenger_profile=AsyncMock(
        side_effect=RuntimeError("x")))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    with pytest.raises(HTTPException) as e:
        await A.get_messenger_profile(acct.id, _db_with(acct), _user())
    assert e.value.status_code == 502

    # update — empty body → 400; happy
    with pytest.raises(HTTPException) as e:
        await A.update_messenger_profile(
            acct.id, A.MessengerProfileUpdate(), _db_with(acct), _user())
    assert e.value.status_code == 400
    cli = _client(set_messenger_profile=AsyncMock(
        return_value={"r": 1}))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    out = await A.update_messenger_profile(
        acct.id, A.MessengerProfileUpdate(get_started={"p": 1}),
        _db_with(acct), _user())
    assert cli.set_messenger_profile.await_args.args[0] == {
        "get_started": {"p": 1}}

    # delete fields + 502
    cli = _client(delete_messenger_profile_fields=AsyncMock(
        return_value={"d": 1}))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    assert await A.delete_messenger_profile_fields(
        acct.id, ["greeting"], _db_with(acct), _user()) == {"d": 1}
    cli.delete_messenger_profile_fields = AsyncMock(
        side_effect=RuntimeError("x"))
    with pytest.raises(HTTPException) as e:
        await A.delete_messenger_profile_fields(
            acct.id, ["greeting"], _db_with(acct), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_unsubscribe(monkeypatch):
    acct = _acct(meta_data={
        "page_token": "EAt", "messenger_setup": {"subscribed": True}})
    cli = _client(unsubscribe_page=AsyncMock(return_value={"u": 1}))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    db = _db_with(acct)
    out = await A.unsubscribe_messenger(acct.id, db, _user())
    assert out == {"u": 1}
    assert acct.meta_data["messenger_setup"]["subscribed"] is False
    assert db.commits == 1

    cli.unsubscribe_page = AsyncMock(side_effect=RuntimeError("x"))
    with pytest.raises(HTTPException) as e:
        await A.unsubscribe_messenger(acct.id, _db_with(acct), _user())
    assert e.value.status_code == 502


# ── send + conversations + user profile + auto-reply config ───────────


@pytest.mark.asyncio
async def test_send_and_conversations(monkeypatch):
    acct = _acct()

    # neither text nor image → 400
    with pytest.raises(HTTPException) as e:
        await A.send_message(
            acct.id, A.SendMessageRequest(recipient_psid="p"),
            _db_with(acct), _user())
    assert e.value.status_code == 400

    # text + image paths
    cli = _client(send_text=AsyncMock(return_value={"m": 1}),
                  send_image_url=AsyncMock(return_value={"m": 2}))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    out = await A.send_message(
        acct.id, A.SendMessageRequest(recipient_psid="p", text="hi"),
        _db_with(acct), _user())
    assert out == {"m": 1}
    out = await A.send_message(
        acct.id, A.SendMessageRequest(
            recipient_psid="p", image_url="https://x/i.png"),
        _db_with(acct), _user())
    assert out == {"m": 2}
    cli.send_text = AsyncMock(side_effect=RuntimeError("x"))
    with pytest.raises(HTTPException) as e:
        await A.send_message(
            acct.id, A.SendMessageRequest(recipient_psid="p", text="hi"),
            _db_with(acct), _user())
    assert e.value.status_code == 502

    # quick replies
    cli = _client(send_quick_replies=AsyncMock(return_value={"q": 1}))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    out = await A.send_quick_replies(
        acct.id, A.SendQuickRepliesRequest(
            recipient_psid="p", text="t", quick_replies=[{"a": 1}]),
        _db_with(acct), _user())
    assert out == {"q": 1}

    # conversations — participants dict → .data
    cli = _client(get_conversations=AsyncMock(return_value=[
        {"id": "c1", "snippet": "s", "updated_time": "t",
         "message_count": 3, "unread_count": 1,
         "participants": {"data": [{"id": "u1"}]}},
        {"id": "c2", "participants": [{"id": "u2"}]},
    ]))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    out = await A.list_conversations(acct.id, 25, "messenger",
                                     _db_with(acct), _user())
    assert out[0].participants == [{"id": "u1"}]
    assert out[1].participants == [{"id": "u2"}]

    # messages — from dict → id
    cli = _client(get_conversation_messages=AsyncMock(return_value=[
        {"id": "m1", "message": "hi", "from": {"id": "u1"},
         "created_time": "t"},
        {"id": "m2", "message": "yo", "from": "u2"},
    ]))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    out = await A.get_conversation_messages(
        acct.id, "c1", 20, _db_with(acct), _user())
    assert out[0].from_id == "u1" and out[1].from_id is None

    # user profile
    cli = _client(get_user_profile=AsyncMock(
        return_value={"name": "N"}))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    assert await A.get_user_profile(acct.id, "psid", _db_with(acct),
                                    _user()) == {"name": "N"}

    # auto-reply config get/update
    acct2 = _acct(meta_data={"page_token": "EAt",
                             "messenger_auto_reply": {"enabled": True,
                                                      "max_tokens": 50}})
    out = await A.get_auto_reply_config(acct2.id, _db_with(acct2), _user())
    assert out.enabled is True and out.max_tokens == 50
    db = _db_with(acct)
    cfg = A.AutoReplyConfig(enabled=True)
    out = await A.update_auto_reply_config(acct.id, cfg, db, _user())
    assert acct.meta_data["messenger_auto_reply"]["enabled"] is True
    assert db.commits == 1


# ── webhook verify + receive ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook(monkeypatch):
    # verify — echo challenge on match, 403 otherwise
    assert await A.verify_webhook("subscribe", "vtok", "CH") == "CH"
    with pytest.raises(HTTPException) as e:
        await A.verify_webhook("subscribe", "wrong", "CH")
    assert e.value.status_code == 403

    # receive — ignored types, no-account, disabled, success, error paths
    monkeypatch.setattr(A, "parse_webhook_event", lambda body: [
        {"page_id": "1", "sender_psid": "s", "message_type": "delivery"},
        {"page_id": "2", "sender_psid": "s", "message_type": "text",
         "message_text": "hi"},                      # no account
        {"page_id": "3", "sender_psid": "s", "message_type": "text",
         "message_text": "hi"},                      # disabled
        {"page_id": "4", "sender_psid": "s", "message_type": "text",
         "message_text": "hi"},                      # success
        {"page_id": "5", "sender_psid": "s", "message_type": "text",
         "message_text": "hi"},                      # reply raises
    ])
    disabled = _acct(meta_data={"page_token": "t",
                                "messenger_auto_reply": {"enabled": False}})
    enabled = _acct(meta_data={"page_token": "t",
                               "messenger_auto_reply": {"enabled": True}})
    enabled2 = _acct(meta_data={"page_token": "t",
                                "messenger_auto_reply": {"enabled": True}})
    db = _DB(queue=[_Res(), _Res(one=disabled), _Res(one=enabled),
                    _Res(one=enabled2)])
    reply = AsyncMock(side_effect=[None, RuntimeError("boom")])
    monkeypatch.setattr(A, "_generate_and_send_auto_reply", reply)
    req = SimpleNamespace(json=AsyncMock(return_value={}))
    out = await A.receive_webhook(req, db)
    assert out == {"status": "ok", "events_received": 5,
                   "auto_replies_sent": 1}


@pytest.mark.asyncio
async def test_generate_and_send_auto_reply(monkeypatch):
    # no page token → early return
    await A._generate_and_send_auto_reply(
        _acct(meta_data={}), "psid", "hi", {})
    # happy — typing tolerated, reply sent
    import app.services.messenger_api as SVC
    client = _client()
    monkeypatch.setattr(SVC, "MessengerAPIClient", lambda **kw: client)
    monkeypatch.setattr(A, "_generate_ai_response", AsyncMock(
        return_value="reply"))
    await A._generate_and_send_auto_reply(
        _acct(), "999999", "hi", {"system_prompt": "S {page_name}"})
    client.send_text.assert_awaited_once_with("999999", "reply")
    assert client.send_sender_action.await_count == 2

    # AI raises → fallback text sent
    client = _client()
    monkeypatch.setattr(SVC, "MessengerAPIClient", lambda **kw: client)
    monkeypatch.setattr(A, "_generate_ai_response", AsyncMock(
        side_effect=RuntimeError("x")))
    await A._generate_and_send_auto_reply(
        _acct(), "999999", "hi", {"fallback_text": "FB"})
    client.send_text.assert_awaited_once_with("999999", "FB")


@pytest.mark.asyncio
async def test_generate_ai_response(monkeypatch):
    # CF success
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    post = AsyncMock(return_value=SimpleNamespace(
        status_code=200,
        json=lambda: {"result": {"response": " cf out "}}))

    class _C:
        def __init__(self, *a, **k):
            self.post = post

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _C)
    out = await A._generate_ai_response("sys", "msg", "model", 50, "FB")
    assert out == "cf out"

    # CF empty → DMR path
    calls = {"n": 0}
    async def _post(url, **kw):
        calls["n"] += 1
        if "cloudflare" in url:
            return SimpleNamespace(status_code=200,
                                   json=lambda: {"result": {}})
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"message": {"content": " dmr out "}}]})
    post = _post
    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _C)
    out = await A._generate_ai_response("sys", "msg", "model", 50, "FB")
    assert out == "dmr out"

    # both fail → fallback
    async def _post_fail(url, **kw):
        return SimpleNamespace(status_code=500, json=dict)
    post = _post_fail
    assert await A._generate_ai_response("sys", "msg", "model", 50,
                                         "FB") == "FB"

    # no CF creds, DMR throws → fallback
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    async def _post_raise(url, **kw):
        raise ConnectionError("down")
    post = _post_raise
    assert await A._generate_ai_response("sys", "msg", "model", 50,
                                         "FB") == "FB"


@pytest.mark.asyncio
async def test_setup_messenger_remaining_branches(monkeypatch):
    acct = _acct()

    # bare domain website → https:// prefix (line 202)
    cli = _client(
        get_page_info=AsyncMock(return_value={
            "name": "P", "website": "cloudless.gr"}),
        subscribe_page=AsyncMock(return_value={"ok": 1}),
        setup_default_profile=AsyncMock(return_value={"done": 1}))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    await A.setup_messenger(acct.id, None, _db_with(acct), _user())
    assert cli.setup_default_profile.await_args.kwargs[
        "page_url"] == "https://cloudless.gr"

    # profile setup non-rate-limit error → 502 (line 234)
    cli = _client(
        get_page_info=AsyncMock(return_value={}),
        subscribe_page=AsyncMock(return_value={"ok": 1}),
        setup_default_profile=AsyncMock(
            side_effect=RuntimeError("boom")))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    with pytest.raises(HTTPException) as e:
        await A.setup_messenger(acct.id, None, _db_with(acct), _user())
    assert e.value.status_code == 502
    assert "Failed to set Messenger Profile" in e.value.detail


@pytest.mark.asyncio
async def test_profile_endpoints_remaining(monkeypatch):
    acct = _acct()

    # subscribed apps non-empty → subscribed=True (line 276)
    cli = _client(
        get_messenger_profile=AsyncMock(return_value={}),
        get_subscribed_apps=AsyncMock(return_value=[{"id": "a"}]),
        get_page_info=AsyncMock(return_value={}))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    out = await A.get_messenger_profile(acct.id, _db_with(acct), _user())
    assert out.subscribed is True

    # update — every field forwarded (313,317,319,321)
    cli = _client(set_messenger_profile=AsyncMock(return_value={"r": 1}))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    body = A.MessengerProfileUpdate(
        greeting=[{"g": 1}], get_started={"p": 1},
        persistent_menu=[{"m": 1}], whitelisted_domains=["d"],
        ice_breakers=[{"i": 1}])
    await A.update_messenger_profile(acct.id, body, _db_with(acct), _user())
    sent = cli.set_messenger_profile.await_args.args[0]
    assert sent == {"greeting": [{"g": 1}], "get_started": {"p": 1},
                    "persistent_menu": [{"m": 1}],
                    "whitelisted_domains": ["d"],
                    "ice_breakers": [{"i": 1}]}

    # update — set_messenger_profile raises → 502 (328-329)
    cli = _client(set_messenger_profile=AsyncMock(
        side_effect=RuntimeError("x")))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    with pytest.raises(HTTPException) as e:
        await A.update_messenger_profile(
            acct.id, A.MessengerProfileUpdate(greeting=[{"g": 1}]),
            _db_with(acct), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_send_and_conversations_502s(monkeypatch):
    acct = _acct()

    # send_quick_replies error → 502 (429-430)
    cli = _client(send_quick_replies=AsyncMock(
        side_effect=RuntimeError("x")))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    with pytest.raises(HTTPException) as e:
        await A.send_quick_replies(
            acct.id, A.SendQuickRepliesRequest(
                recipient_psid="p", text="t", quick_replies=[{"a": 1}]),
            _db_with(acct), _user())
    assert e.value.status_code == 502

    # list_conversations error → 502 (453-454)
    cli = _client(get_conversations=AsyncMock(
        side_effect=RuntimeError("x")))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    with pytest.raises(HTTPException) as e:
        await A.list_conversations(acct.id, 25, "messenger",
                                   _db_with(acct), _user())
    assert e.value.status_code == 502

    # get_conversation_messages error → 502 (483-484)
    cli = _client(get_conversation_messages=AsyncMock(
        side_effect=RuntimeError("x")))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    with pytest.raises(HTTPException) as e:
        await A.get_conversation_messages(
            acct.id, "c1", 20, _db_with(acct), _user())
    assert e.value.status_code == 502

    # get_user_profile error → 502 (514-515)
    cli = _client(get_user_profile=AsyncMock(
        side_effect=RuntimeError("x")))
    monkeypatch.setattr(A, "_get_messenger_client", lambda a: cli)
    with pytest.raises(HTTPException) as e:
        await A.get_user_profile(acct.id, "psid", _db_with(acct), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_auto_reply_typing_failures_tolerated(monkeypatch):
    # typing_on/off raising is non-fatal (661-662, 690-691)
    import app.services.messenger_api as SVC
    client = _client()
    client.send_sender_action = AsyncMock(side_effect=RuntimeError("x"))
    monkeypatch.setattr(SVC, "MessengerAPIClient", lambda **kw: client)
    monkeypatch.setattr(A, "_generate_ai_response", AsyncMock(
        return_value="reply"))
    await A._generate_and_send_auto_reply(
        _acct(), "999999", "hi", {})
    client.send_text.assert_awaited_once_with("999999", "reply")


@pytest.mark.asyncio
async def test_generate_ai_response_cf_exception_falls_back(monkeypatch):
    # CF POST raises → warn, DMR tried (730-731)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")

    async def _post(url, **kw):
        if "cloudflare" in url:
            raise ConnectionError("cf down")
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"message": {"content": " dmr out "}}]})

    class _C:
        def __init__(self, *a, **k):
            self.post = _post

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _C)
    out = await A._generate_ai_response("sys", "msg", "model", 50, "FB")
    assert out == "dmr out"
