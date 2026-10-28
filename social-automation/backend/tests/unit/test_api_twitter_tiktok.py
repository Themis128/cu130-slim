"""Coverage for app/api/twitter_tiktok.py router."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.twitter_tiktok as T


class _Res:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class _DB:
    def __init__(self, account=None):
        self._account = account
        self.commits = 0

    async def execute(self, *a, **kw):
        return _Res(scalar=self._account)

    async def commit(self):
        self.commits += 1


def _user():
    return SimpleNamespace(id=uuid.uuid4())


def _account(**kw):
    d = dict(id=uuid.uuid4(), team_id=uuid.uuid4(), platform="twitter", access_token_enc=b"enc", account_id="999", meta_data={})
    d.update(kw)
    return SimpleNamespace(**d)


class _BridgeCM:
    def __init__(self, bridge):
        self.bridge = bridge

    async def __aenter__(self):
        return self.bridge

    async def __aexit__(self, *a):
        return False


def _wire_bridge(monkeypatch, bridge=None):
    bridge = bridge or SimpleNamespace()
    monkeypatch.setattr(T, "_twitter_bridge", lambda: _BridgeCM(bridge))
    return bridge


class TestGetAccount:
    @pytest.mark.asyncio
    async def test_missing_404(self):
        with pytest.raises(HTTPException) as ei:
            await T._get_account(uuid.uuid4(), _user(), _DB(None), "twitter")
        assert ei.value.status_code == 404
        assert "Twitter" in ei.value.detail


class TestTwitterAutoReply:
    @pytest.mark.asyncio
    async def test_get_defaults(self):
        acct = _account(meta_data=None)
        out = await T.get_twitter_dm_auto_reply(acct.id, _user(), _DB(acct))
        assert out.enabled is False
        assert out.max_tokens == 250

    @pytest.mark.asyncio
    async def test_get_saved(self):
        acct = _account(meta_data={"twitter_auto_reply": {"enabled": True, "cooldown_seconds": 60, "model": "m2"}})
        out = await T.get_twitter_dm_auto_reply(acct.id, _user(), _DB(acct))
        assert out.enabled is True
        assert out.cooldown_seconds == 60
        assert out.model == "m2"

    @pytest.mark.asyncio
    async def test_update_disabled(self, monkeypatch):
        monkeypatch.setattr("sqlalchemy.orm.attributes.flag_modified", lambda *a: None)
        acct = _account()
        db = _DB(acct)
        body = T.DMAutoReplyConfig(enabled=False)
        await T.update_twitter_dm_auto_reply(acct.id, body, _user(), db)
        assert db.commits == 1
        assert acct.meta_data["twitter_auto_reply"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_enabled_quota(self, monkeypatch):
        monkeypatch.setattr("sqlalchemy.orm.attributes.flag_modified", lambda *a: None)
        import app.api.deps as deps

        check = AsyncMock()
        monkeypatch.setattr(deps, "check_plan_feature", check)
        acct = _account()
        db = _DB(acct)
        await T.update_twitter_dm_auto_reply(acct.id, T.DMAutoReplyConfig(enabled=True), _user(), db)
        check.assert_awaited_once()
        assert db.commits == 1


class TestTwitterDmEvents:
    @pytest.mark.asyncio
    async def test_list(self, monkeypatch):
        b = _wire_bridge(monkeypatch)
        b.get_twitter_dm_conversations = AsyncMock(return_value={"conversations": [{"id": "c1"}, {"id": "c2"}]})
        out = await T.list_twitter_dm_events(uuid.uuid4(), 50, _user(), _DB(_account()))
        assert out["meta"]["result_count"] == 2
        assert out["meta"]["source"] == "browser_bridge"

    @pytest.mark.asyncio
    async def test_list_capped(self, monkeypatch):
        b = _wire_bridge(monkeypatch)
        b.get_twitter_dm_conversations = AsyncMock(return_value={"conversations": [{"id": i} for i in range(10)]})
        out = await T.list_twitter_dm_events(uuid.uuid4(), 3, _user(), _DB(_account()))
        assert len(out["data"]) == 3

    @pytest.mark.asyncio
    async def test_list_none_result(self, monkeypatch):
        b = _wire_bridge(monkeypatch)
        b.get_twitter_dm_conversations = AsyncMock(return_value=None)
        out = await T.list_twitter_dm_events(uuid.uuid4(), 50, _user(), _DB(_account()))
        assert out["meta"]["result_count"] == 0

    @pytest.mark.asyncio
    async def test_bridge_error_502(self, monkeypatch):
        b = _wire_bridge(monkeypatch)
        b.get_twitter_dm_conversations = AsyncMock(side_effect=RuntimeError("bridge down"))
        with pytest.raises(HTTPException) as ei:
            await T.list_twitter_dm_events(uuid.uuid4(), 50, _user(), _DB(_account()))
        assert ei.value.status_code == 502


class TestTwitterThreadId:
    def test_pairing(self):
        acct = _account(account_id="100")
        assert T._twitter_thread_id(acct, "50") == "50-100"
        assert T._twitter_thread_id(acct, "500") == "100-500"


class TestSendTwitterDm:
    @pytest.mark.asyncio
    async def test_no_text(self):
        with pytest.raises(HTTPException) as ei:
            await T.send_twitter_dm(uuid.uuid4(), {}, _user(), _DB(_account()))
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_no_thread_or_participant(self):
        with pytest.raises(HTTPException) as ei:
            await T.send_twitter_dm(uuid.uuid4(), {"text": "hi"}, _user(), _DB(_account()))
        assert "required" in ei.value.detail

    @pytest.mark.asyncio
    async def test_bad_participant(self):
        with pytest.raises(HTTPException) as ei:
            await T.send_twitter_dm(uuid.uuid4(), {"text": "hi", "participant_id": "abc"}, _user(), _DB(_account()))
        assert "numeric" in ei.value.detail

    @pytest.mark.asyncio
    async def test_participant_no_account_id(self):
        acct = _account(account_id=None)
        with pytest.raises(HTTPException) as ei:
            await T.send_twitter_dm(uuid.uuid4(), {"text": "hi", "participant_id": "50"}, _user(), _DB(acct))
        assert "account_id missing" in ei.value.detail

    @pytest.mark.asyncio
    async def test_participant_derives_thread(self, monkeypatch):
        b = _wire_bridge(monkeypatch)
        b.send_twitter_dm_message = AsyncMock(return_value={"ok": 1})
        acct = _account(account_id="100")
        out = await T.send_twitter_dm(acct.id, {"text": "hi", "participant_id": "50"}, _user(), _DB(acct))
        b.send_twitter_dm_message.assert_awaited_once_with("50-100", "hi")
        assert out["status"] == "sent"

    @pytest.mark.asyncio
    async def test_url_thread_id(self, monkeypatch):
        b = _wire_bridge(monkeypatch)
        b.send_twitter_dm_message = AsyncMock(return_value={"ok": 1})
        out = await T.send_twitter_dm(uuid.uuid4(), {"text": "hi", "conversation_id": "https://x.com/messages/50-100?x=1"}, _user(), _DB(_account()))
        assert out["thread_id"] == "50-100"

    @pytest.mark.asyncio
    async def test_send_result_error_502(self, monkeypatch):
        b = _wire_bridge(monkeypatch)
        b.send_twitter_dm_message = AsyncMock(return_value={"error": "not logged in"})
        with pytest.raises(HTTPException) as ei:
            await T.send_twitter_dm(uuid.uuid4(), {"text": "hi", "thread_id": "1-2"}, _user(), _DB(_account()))
        assert ei.value.status_code == 502


class TestTikTokAutoReply:
    @pytest.mark.asyncio
    async def test_get_defaults(self):
        acct = _account(platform="tiktok", meta_data=None)
        out = await T.get_tiktok_dm_auto_reply(acct.id, _user(), _DB(acct))
        assert out.enabled is False

    @pytest.mark.asyncio
    async def test_get_saved(self):
        acct = _account(platform="tiktok", meta_data={"tiktok_auto_reply": {"enabled": True, "temperature": 0.2}})
        out = await T.get_tiktok_dm_auto_reply(acct.id, _user(), _DB(acct))
        assert out.temperature == 0.2

    @pytest.mark.asyncio
    async def test_update(self, monkeypatch):
        monkeypatch.setattr("sqlalchemy.orm.attributes.flag_modified", lambda *a: None)
        acct = _account(platform="tiktok")
        db = _DB(acct)
        await T.update_tiktok_dm_auto_reply(acct.id, T.DMAutoReplyConfig(enabled=False), _user(), db)
        assert db.commits == 1
        assert "tiktok_auto_reply" in acct.meta_data

    @pytest.mark.asyncio
    async def test_update_enabled_quota(self, monkeypatch):
        monkeypatch.setattr("sqlalchemy.orm.attributes.flag_modified", lambda *a: None)
        import app.api.deps as deps

        check = AsyncMock()
        monkeypatch.setattr(deps, "check_plan_feature", check)
        acct = _account(platform="tiktok")
        await T.update_tiktok_dm_auto_reply(acct.id, T.DMAutoReplyConfig(enabled=True), _user(), _DB(acct))
        check.assert_awaited_once()


def _tt_client(monkeypatch, **methods):
    import app.services.tiktok_api as ta

    monkeypatch.setattr(T, "decrypt_token", lambda e: "tok")
    fake = SimpleNamespace(**{k: AsyncMock(return_value=v) for k, v in methods.items()})
    monkeypatch.setattr(ta, "TikTokAPIClient", lambda **kw: fake)
    return fake


def _tt_err(code=400):
    import app.services.tiktok_api as ta

    return ta.TikTokAPIError(code, "bad", "url")


class TestTikTokDm:
    @pytest.mark.asyncio
    async def test_convos_no_token(self):
        acct = _account(platform="tiktok", access_token_enc=None)
        with pytest.raises(HTTPException) as ei:
            await T.list_tiktok_dm_conversations(acct.id, _user(), _DB(acct))
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_convos(self, monkeypatch):
        _tt_client(monkeypatch, list_dm_conversations={"data": []})
        acct = _account(platform="tiktok", meta_data={"open_id": "oid"})
        out = await T.list_tiktok_dm_conversations(acct.id, _user(), _DB(acct))
        assert out == {"data": []}

    @pytest.mark.asyncio
    async def test_convos_api_error(self, monkeypatch):
        fake = _tt_client(monkeypatch)
        fake.list_dm_conversations = AsyncMock(side_effect=_tt_err(403))
        acct = _account(platform="tiktok")
        with pytest.raises(HTTPException) as ei:
            await T.list_tiktok_dm_conversations(acct.id, _user(), _DB(acct))
        assert ei.value.status_code == 403

    @pytest.mark.asyncio
    async def test_send_no_token(self):
        acct = _account(platform="tiktok", access_token_enc=None)
        with pytest.raises(HTTPException) as ei:
            await T.send_tiktok_dm(acct.id, {}, _user(), _DB(acct))
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_send_no_convo(self):
        acct = _account(platform="tiktok")
        with pytest.raises(HTTPException) as ei:
            await T.send_tiktok_dm(acct.id, {"text": "hi"}, _user(), _DB(acct))
        assert "conversation_id" in ei.value.detail

    @pytest.mark.asyncio
    async def test_send_no_text(self):
        acct = _account(platform="tiktok")
        with pytest.raises(HTTPException) as ei:
            await T.send_tiktok_dm(acct.id, {"conversation_id": "c"}, _user(), _DB(acct))
        assert "text" in ei.value.detail

    @pytest.mark.asyncio
    async def test_send(self, monkeypatch):
        fake = _tt_client(monkeypatch, send_dm={"ok": True})
        acct = _account(platform="tiktok", meta_data=None)
        out = await T.send_tiktok_dm(acct.id, {"conversation_id": "c1", "text": "hi"}, _user(), _DB(acct))
        fake.send_dm.assert_awaited_once_with("c1", {"text": "hi"})
        assert out == {"ok": True}

    @pytest.mark.asyncio
    async def test_send_api_error(self, monkeypatch):
        fake = _tt_client(monkeypatch)
        fake.send_dm = AsyncMock(side_effect=_tt_err(500))
        acct = _account(platform="tiktok")
        with pytest.raises(HTTPException) as ei:
            await T.send_tiktok_dm(acct.id, {"conversation_id": "c", "text": "t"}, _user(), _DB(acct))
        assert ei.value.status_code == 500


def test_twitter_bridge_factory(monkeypatch):
    import app.core.config as cfg
    import app.services.browser_bridge as bb
    import app.services.browser_orchestrator as bo

    sentinel = object()
    monkeypatch.setattr(bo, "browser_session", lambda *a, **k: sentinel)
    fake_client = object()
    monkeypatch.setattr(bb, "BrowserBridgeClient", lambda *a, **k: fake_client)
    monkeypatch.setattr(cfg, "get_settings", lambda: SimpleNamespace(BROWSER_BRIDGE_URL="u"))
    assert T._twitter_bridge() is sentinel
