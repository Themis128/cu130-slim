"""Coverage for app/api/inbox.py — unified inbox aggregation."""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.inbox as INBOX


def _user():
    return SimpleNamespace(id=uuid.uuid4())


def _acc(platform, account_type="person", **kw):
    d = dict(id=uuid.uuid4(), platform=platform, account_type=account_type,
             account_id="pg-1", username="own",
             display_name=None, access_token_enc=None, meta_data={})
    d.update(kw)
    return SimpleNamespace(**d)


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _Result:
    def __init__(self, scalars=None, rows=None):
        self._scalars = scalars or []
        self._rows = rows

    def scalars(self):
        return _Scalars(self._scalars)

    def all(self):
        return self._rows if self._rows is not None else self._scalars


class _SessionMaker:
    def __init__(self, results):
        self._results = list(results)

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def execute(self, stmt, *a):
        return self._results.pop(0) if self._results else _Result()


def _conv(platform, sender, unread=False):
    return INBOX.UnifiedConversation(
        platform=platform, account_id="a", account_name="n",
        sender_name=sender, unread=unread)


class TestUnifiedInbox:
    @pytest.mark.asyncio
    async def test_dispatch_and_sort(self, monkeypatch):
        accounts = [
            _acc("facebook", "page"),
            _acc("facebook", "user"),
            _acc("instagram"),
            _acc("whatsapp"),
            _acc("tiktok"),          # skipped — no fetcher
            _acc("Twitter"),         # skipped — unknown platform
        ]
        monkeypatch.setattr(INBOX, "async_session_maker",
                            _SessionMaker([_Result(scalars=accounts)]))
        monkeypatch.setattr(INBOX, "_fetch_page_messenger",
                            AsyncMock(return_value=[_conv("messenger", "Zoe", True)]))
        monkeypatch.setattr(INBOX, "_fetch_personal_messenger",
                            AsyncMock(return_value=[_conv("personal_messenger", "al")]))
        monkeypatch.setattr(INBOX, "_fetch_instagram_dms",
                            AsyncMock(return_value=[]))
        monkeypatch.setattr(INBOX, "_fetch_whatsapp",
                            AsyncMock(return_value=[_conv("whatsapp", "bo")]))
        team = uuid.uuid4()
        out = await INBOX.get_unified_inbox(current_user=_user(), team_id=team)
        assert out.total == 3
        # unread first, then alphabetical sender
        assert out.conversations[0].sender_name == "Zoe"
        assert out.by_platform == {"messenger": 1, "personal_messenger": 1,
                                   "whatsapp": 1}
        # cache hit — second call returns same response without fetch
        INBOX._fetch_page_messenger.reset_mock()
        out2 = await INBOX.get_unified_inbox(current_user=_user(), team_id=team)
        assert out2 is out
        INBOX._fetch_page_messenger.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_errors_nonfatal(self, monkeypatch):
        accounts = [_acc("facebook", "page"), _acc("instagram")]
        monkeypatch.setattr(INBOX, "async_session_maker",
                            _SessionMaker([_Result(scalars=accounts)]))
        monkeypatch.setattr(INBOX, "_fetch_page_messenger",
                            AsyncMock(side_effect=RuntimeError("bridge down")))
        monkeypatch.setattr(INBOX, "_fetch_instagram_dms",
                            AsyncMock(return_value=[_conv("instagram", "ok")]))
        out = await INBOX.get_unified_inbox(current_user=_user(),
                                            team_id=uuid.uuid4())
        assert out.total == 1

    @pytest.mark.asyncio
    async def test_no_accounts(self, monkeypatch):
        monkeypatch.setattr(INBOX, "async_session_maker",
                            _SessionMaker([_Result(scalars=[])]))
        out = await INBOX.get_unified_inbox(current_user=_user(),
                                            team_id=uuid.uuid4())
        assert out.total == 0 and out.by_platform == {}


class TestPageMessenger:
    @pytest.mark.asyncio
    async def test_no_token_no_pageid(self):
        assert await INBOX._fetch_page_messenger(_acc("facebook", "page")) == []
        acc = _acc("facebook", "page",
                   meta_data={"page_token": "EAtok"}, account_id=None)
        assert await INBOX._fetch_page_messenger(acc) == []

    @pytest.mark.asyncio
    async def test_plaintext_and_encrypted_token(self, monkeypatch):
        convos = [{"id": "t1",
                   "participants": {"data": [{"name": "Ann"}]},
                   "unread_count": 2, "snippet": "hi",
                   "updated_time": "2026-01-01"},
                  {"id": "t2", "participants": {"data": []}}]
        client = SimpleNamespace(
            get_conversations=AsyncMock(return_value=convos))
        created = {}

        def factory(**kw):
            created.update(kw)
            return client
        monkeypatch.setattr(INBOX, "MessengerAPIClient", factory)

        # plaintext EA* token
        acc = _acc("facebook", "page",
                   meta_data={"page_token": "EAtok"},
                   display_name="MyPage")
        out = await INBOX._fetch_page_messenger(acc)
        assert len(out) == 2
        assert out[0].unread and out[0].sender_name == "Ann"
        assert out[1].sender_name == "Unknown"
        assert created["access_token"] == "EAtok"

        # encrypted token (not starting with EA) → decrypt attempted
        import app.core.security as sec
        monkeypatch.setattr(sec, "decrypt_token", lambda t: "EAdec")
        acc2 = _acc("facebook", "page",
                    meta_data={"access_token": "encblob"})
        await INBOX._fetch_page_messenger(acc2)
        assert created["access_token"] == "EAdec"

        # decrypt failure → falls back to raw token
        monkeypatch.setattr(sec, "decrypt_token",
                            lambda t: (_ for _ in ()).throw(ValueError()))
        acc3 = _acc("facebook", "page",
                    meta_data={"page_access_token": "zz9"})
        await INBOX._fetch_page_messenger(acc3)
        assert created["access_token"] == "zz9"

    @pytest.mark.asyncio
    async def test_api_error(self, monkeypatch):
        client = SimpleNamespace(
            get_conversations=AsyncMock(side_effect=RuntimeError()))
        monkeypatch.setattr(INBOX, "MessengerAPIClient", lambda **kw: client)
        acc = _acc("facebook", "page", meta_data={"page_token": "EAx"})
        assert await INBOX._fetch_page_messenger(acc) == []


class TestPersonalMessenger:
    @pytest.mark.asyncio
    async def test_happy(self, monkeypatch):
        bridge = SimpleNamespace(
            get_personal_messenger_conversations_fast=AsyncMock(
                return_value={"conversations": [
                    {"thread_id": "t", "name": "Bob", "preview": "yo",
                     "unread": True, "url": "http://m", "e2ee": True}]}))
        monkeypatch.setattr(INBOX, "BrowserBridgeClient",
                            lambda *a, **kw: bridge)
        out = await INBOX._fetch_personal_messenger(_acc("facebook", "user"))
        assert len(out) == 1
        assert out[0].platform == "personal_messenger"
        assert out[0].e2ee and out[0].unread
        assert out[0].account_name == "Personal Messenger"

    @pytest.mark.asyncio
    async def test_error(self, monkeypatch):
        bridge = SimpleNamespace(
            get_personal_messenger_conversations_fast=AsyncMock(
                side_effect=TimeoutError()))
        monkeypatch.setattr(INBOX, "BrowserBridgeClient",
                            lambda *a, **kw: bridge)
        assert await INBOX._fetch_personal_messenger(
            _acc("facebook", "user")) == []


class TestInstagramDMs:
    @pytest.mark.asyncio
    async def test_missing_token_or_id(self, monkeypatch):
        # no token in meta or enc
        assert await INBOX._fetch_instagram_dms(_acc("instagram")) == []
        # token but no ig_user_id
        acc = _acc("instagram", meta_data={"access_token": "t"},
                   account_id=None)
        acc.account_id = None
        assert await INBOX._fetch_instagram_dms(acc) == []

    @pytest.mark.asyncio
    async def test_decrypt_enc_token(self, monkeypatch):
        import app.core.security as sec
        calls = []
        monkeypatch.setattr(sec, "decrypt_token",
                            lambda t: calls.append(t) or "plain")
        client = SimpleNamespace(get_conversations=AsyncMock(
            return_value={"data": []}))
        monkeypatch.setattr(INBOX, "InstagramAPIClient", lambda **kw: client)
        acc = _acc("instagram", access_token_enc=b"enc",
                   meta_data={"ig_user_id": "ig1"})
        await INBOX._fetch_instagram_dms(acc)
        assert calls == [b"enc"]

        # decrypt fails → returns []
        monkeypatch.setattr(sec, "decrypt_token",
                            lambda t: (_ for _ in ()).throw(KeyError()))
        assert await INBOX._fetch_instagram_dms(acc) == []

    @pytest.mark.asyncio
    async def test_happy_and_sender_pick(self, monkeypatch):
        convos = {"data": [{
            "id": "c1",
            "participants": {"data": [
                {"id": "ig1", "username": "own"},      # self by id
                {"id": "u2", "username": "customer"}]},
            "messages": {"data": [{"message": "hello",
                                   "created_time": "ts"}]},
        }, {
            "id": "c2",
            "participants": {"data": [{"id": "ig1", "username": "own"}]},
            "messages": {"data": [{"created_time": "ts2"}]},  # no text
        }]}
        client = SimpleNamespace(get_conversations=AsyncMock(
            return_value=convos))
        monkeypatch.setattr(INBOX, "InstagramAPIClient", lambda **kw: client)
        acc = _acc("instagram", username="own",
                   meta_data={"access_token": "t", "ig_user_id": "ig1"})
        out = await INBOX._fetch_instagram_dms(acc)
        assert len(out) == 2
        assert out[0].sender_name == "customer"
        assert out[0].preview == "hello"
        # second convo: only self → falls back to participants[0]; no message text → [media]
        assert out[1].sender_name == "own"
        assert out[1].preview == "[media]"

    @pytest.mark.asyncio
    async def test_api_error(self, monkeypatch):
        client = SimpleNamespace(get_conversations=AsyncMock(
            side_effect=RuntimeError()))
        monkeypatch.setattr(INBOX, "InstagramAPIClient", lambda **kw: client)
        acc = _acc("instagram",
                   meta_data={"access_token": "t", "ig_user_id": "i"})
        assert await INBOX._fetch_instagram_dms(acc) == []


def _wa(phone="+30123", text="hi", direction="inbound", mtype="text",
        name="Nikos", ts=None):
    return SimpleNamespace(
        sender_phone=phone, message_text=text, direction=direction,
        message_type=mtype, sender_name=name,
        created_at=ts or datetime(2026, 1, 1, tzinfo=UTC))


class TestWhatsApp:
    @pytest.mark.asyncio
    async def test_no_rows(self, monkeypatch):
        monkeypatch.setattr(INBOX, "async_session_maker",
                            _SessionMaker([_Result(scalars=[])]))
        assert await INBOX._fetch_whatsapp(_acc("whatsapp")) == []

    @pytest.mark.asyncio
    async def test_happy(self, monkeypatch):
        rows = [_wa(), _wa(phone="+30456", text="back",
                         direction="outbound", ts=None)]
        rows[1].created_at = None
        names = [("+30123", "Nikos K"), ("+30456", "")]
        monkeypatch.setattr(INBOX, "async_session_maker",
                            _SessionMaker([_Result(scalars=rows),
                                           _Result(rows=names)]))
        out = await INBOX._fetch_whatsapp(_acc("whatsapp",
                                               display_name="Biz"))
        assert len(out) == 2
        assert out[0].sender_name == "Nikos K"
        assert out[0].preview == "hi"
        assert out[1].preview.startswith("You: ")
        assert out[1].timestamp is None

    @pytest.mark.asyncio
    async def test_non_text_message(self, monkeypatch):
        rows = [_wa(text=None, mtype="image")]
        monkeypatch.setattr(INBOX, "async_session_maker",
                            _SessionMaker([_Result(scalars=rows),
                                           _Result(rows=[])]))
        out = await INBOX._fetch_whatsapp(_acc("whatsapp"))
        assert out[0].preview == "[image]"
        assert out[0].sender_name == "+30123"  # no name → peer

    @pytest.mark.asyncio
    async def test_db_error(self, monkeypatch):
        class _BadMaker:
            def __call__(self):
                return self

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def execute(self, *a):
                raise RuntimeError("db down")
        monkeypatch.setattr(INBOX, "async_session_maker", _BadMaker())
        assert await INBOX._fetch_whatsapp(_acc("whatsapp")) == []
