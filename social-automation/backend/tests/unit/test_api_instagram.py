"""Coverage for app/api/instagram.py router."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx as _httpx_global
import pytest
from fastapi import HTTPException

import app.api.instagram as I
from app.services.instagram_api import InstagramAPIError


class _Res:
    def __init__(self, scalar=None, all_items=None):
        self._scalar = scalar
        self._all = all_items or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._all)


class _DB:
    def __init__(self, team=None, results=None):
        self._team = team
        self._results = list(results or [])

    async def get(self, model, key):
        return self._team

    async def execute(self, *a, **kw):
        return self._results.pop(0)


def _account(**kw):
    d = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform="instagram",
        access_token_enc=b"enc",
        account_id="ig123",
        meta_data={},
        display_name="IG",
        status="active",
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _client(monkeypatch, acct=None, **methods):
    """Patch account lookup + client factory; returns the fake client."""
    monkeypatch.setattr(I, "decrypt_token", lambda enc: "token")
    fake = SimpleNamespace(**{k: AsyncMock(return_value=v) for k, v in methods.items()})
    captured = {}

    def _make_client(**kw):
        captured["kw"] = kw
        return fake

    monkeypatch.setattr(I, "InstagramAPIClient", _make_client)
    return fake, captured


_TEAM = SimpleNamespace(id=uuid.uuid4())


class TestGetIgClient:
    @pytest.mark.asyncio
    async def test_account_missing(self):
        db = _DB(team=_TEAM, results=[_Res(scalar=None)])
        with pytest.raises(HTTPException) as ei:
            await I._get_ig_client(db, _TEAM, uuid.uuid4())
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_business_login_flag(self, monkeypatch):
        acct = _account(meta_data={"login_type": "business_login"})
        fake, captured = _client(monkeypatch, acct)
        db = _DB(team=_TEAM, results=[_Res(scalar=acct)])
        await I._get_ig_client(db, _TEAM, acct.id)
        assert captured["kw"]["use_business_login_api"] is True
        assert captured["kw"]["ig_user_id"] == "ig123"

    @pytest.mark.asyncio
    async def test_default_login(self, monkeypatch):
        acct = _account(meta_data=None)
        fake, captured = _client(monkeypatch, acct)
        db = _DB(team=_TEAM, results=[_Res(scalar=acct)])
        await I._get_ig_client(db, _TEAM, acct.id)
        assert captured["kw"]["use_business_login_api"] is False


class TestQuota:
    @pytest.mark.asyncio
    async def test_team_missing(self):
        db = _DB(team=None)
        with pytest.raises(HTTPException) as ei:
            await I.get_publishing_quota(uuid.uuid4(), uuid.uuid4(), AsyncMock(), db)
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_happy(self, monkeypatch):
        fake, _ = _client(
            monkeypatch,
            None,
            get_remaining_publish_quota=10,
            get_publishing_limit={
                "data": [{"quota_usage": [{"metric": "other", "value": 99}, {"metric": "publish_count", "value": 15}], "config": {"quota_total": 50}}]
            },
        )
        acct = _account()
        db = _DB(team=_TEAM, results=[_Res(scalar=acct)])
        out = await I.get_publishing_quota(uuid.uuid4(), acct.id, AsyncMock(), db)
        assert (out.remaining, out.total, out.used) == (10, 50, 15)

    @pytest.mark.asyncio
    async def test_defaults(self, monkeypatch):
        fake, _ = _client(monkeypatch, None, get_remaining_publish_quota=25, get_publishing_limit={})
        acct = _account()
        db = _DB(team=_TEAM, results=[_Res(scalar=acct)])
        out = await I.get_publishing_quota(uuid.uuid4(), acct.id, AsyncMock(), db)
        assert out.total == 25 and out.used == 0

    @pytest.mark.asyncio
    async def test_api_error(self, monkeypatch):
        err = InstagramAPIError(429, "rate", "url")
        fake, _ = _client(monkeypatch, None)
        fake.get_remaining_publish_quota = AsyncMock(side_effect=err)
        acct = _account()
        db = _DB(team=_TEAM, results=[_Res(scalar=acct)])
        with pytest.raises(HTTPException) as ei:
            await I.get_publishing_quota(uuid.uuid4(), acct.id, AsyncMock(), db)
        assert ei.value.status_code == 429


class TestComments:
    @pytest.mark.asyncio
    async def test_list(self, monkeypatch):
        fake, _ = _client(monkeypatch, None, list_comments={"data": [{"id": 1, "text": "hi", "username": "u", "timestamp": "t", "like_count": "3"}, {"id": 2}]})
        acct = _account()
        db = _DB(team=_TEAM, results=[_Res(scalar=acct)])
        out = await I.list_comments("m1", uuid.uuid4(), acct.id, 50, AsyncMock(), db)
        assert len(out.comments) == 2
        assert out.comments[0].like_count == 3
        fake.list_comments.assert_awaited_once_with("m1", limit=50)

    @pytest.mark.asyncio
    async def test_list_error(self, monkeypatch):
        fake, _ = _client(monkeypatch, None)
        fake.list_comments = AsyncMock(side_effect=InstagramAPIError(500, "e", "u"))
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException):
            await I.list_comments("m", uuid.uuid4(), uuid.uuid4(), 50, AsyncMock(), db)

    @pytest.mark.asyncio
    async def test_reply(self, monkeypatch):
        fake, _ = _client(monkeypatch, None, reply_to_comment={"id": "r1", "text": "re", "username": "me"})
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        req = I.ReplyRequest(message="thanks")
        out = await I.reply_to_comment("c1", req, uuid.uuid4(), uuid.uuid4(), AsyncMock(), db)
        assert out.id == "r1" and out.text == "re"
        fake.reply_to_comment.assert_awaited_once_with("c1", "thanks")

    @pytest.mark.asyncio
    async def test_reply_error(self, monkeypatch):
        fake, _ = _client(monkeypatch, None)
        fake.reply_to_comment = AsyncMock(side_effect=InstagramAPIError(400, "e", "u"))
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException):
            await I.reply_to_comment("c", I.ReplyRequest(message="x"), uuid.uuid4(), uuid.uuid4(), AsyncMock(), db)

    @pytest.mark.asyncio
    async def test_hide_unhide(self, monkeypatch):
        fake, _ = _client(monkeypatch, None, hide_comment={})
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        out = await I.hide_comment("c", uuid.uuid4(), uuid.uuid4(), True, AsyncMock(), db)
        assert out.detail == "hidden"
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        out = await I.hide_comment("c", uuid.uuid4(), uuid.uuid4(), False, AsyncMock(), db)
        assert out.detail == "unhidden"

    @pytest.mark.asyncio
    async def test_hide_error(self, monkeypatch):
        fake, _ = _client(monkeypatch, None)
        fake.hide_comment = AsyncMock(side_effect=InstagramAPIError(403, "e", "u"))
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException):
            await I.hide_comment("c", uuid.uuid4(), uuid.uuid4(), True, AsyncMock(), db)

    @pytest.mark.asyncio
    async def test_delete(self, monkeypatch):
        fake, _ = _client(monkeypatch, None, delete_comment=True)
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        out = await I.delete_comment("c", uuid.uuid4(), uuid.uuid4(), AsyncMock(), db)
        assert out.success and out.detail == "deleted"

    @pytest.mark.asyncio
    async def test_delete_error(self, monkeypatch):
        fake, _ = _client(monkeypatch, None)
        fake.delete_comment = AsyncMock(side_effect=InstagramAPIError(404, "e", "u"))
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException):
            await I.delete_comment("c", uuid.uuid4(), uuid.uuid4(), AsyncMock(), db)


class TestStory:
    @pytest.mark.asyncio
    async def test_publish(self, monkeypatch):
        fake, _ = _client(monkeypatch, None, publish_story="m9")
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        req = I.StoryPublishRequest(media_url="http://x/i.png", media_type="IMAGE", link="http://l", alt_text="alt")
        out = await I.publish_story(req, uuid.uuid4(), uuid.uuid4(), AsyncMock(), db)
        assert out.media_id == "m9"
        fake.publish_story.assert_awaited_once_with(media_url="http://x/i.png", media_type="IMAGE", link="http://l", alt_text="alt")

    @pytest.mark.asyncio
    async def test_publish_api_error(self, monkeypatch):
        fake, _ = _client(monkeypatch, None)
        fake.publish_story = AsyncMock(side_effect=InstagramAPIError(400, "e", "u"))
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException) as ei:
            await I.publish_story(I.StoryPublishRequest(media_url="x"), uuid.uuid4(), uuid.uuid4(), AsyncMock(), db)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_publish_timeout(self, monkeypatch):
        fake, _ = _client(monkeypatch, None)
        fake.publish_story = AsyncMock(side_effect=TimeoutError("slow"))
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException) as ei:
            await I.publish_story(I.StoryPublishRequest(media_url="x"), uuid.uuid4(), uuid.uuid4(), AsyncMock(), db)
        assert ei.value.status_code == 504


class TestMentions:
    @pytest.mark.asyncio
    async def test_team_missing_all_endpoints(self):
        db = _DB(team=None)
        uid = uuid.uuid4()
        with pytest.raises(HTTPException) as ei:
            await I.list_comments("m", uid, uid, 50, AsyncMock(), db)
        assert ei.value.status_code == 404
        with pytest.raises(HTTPException):
            await I.reply_to_comment("c", I.ReplyRequest(message="x"), uid, uid, AsyncMock(), db)
        with pytest.raises(HTTPException):
            await I.hide_comment("c", uid, uid, True, AsyncMock(), db)
        with pytest.raises(HTTPException):
            await I.delete_comment("c", uid, uid, AsyncMock(), db)
        with pytest.raises(HTTPException):
            await I.publish_story(I.StoryPublishRequest(media_url="x"), uid, uid, AsyncMock(), db)
        with pytest.raises(HTTPException):
            await I.get_mentions(uid, uid, 10, AsyncMock(), db)

    @pytest.mark.asyncio
    async def test_mentions(self, monkeypatch):
        fake, _ = _client(
            monkeypatch,
            None,
            get_recent_mentions=[{"id": 1, "caption": "cap", "media_type": "IMAGE", "media_url": "u", "permalink": "p", "timestamp": "t", "username": "fan"}],
        )
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        out = await I.get_mentions(uuid.uuid4(), uuid.uuid4(), 10, AsyncMock(), db)
        assert out.mentions[0].caption == "cap"
        assert out.mentions[0].username == "fan"

    @pytest.mark.asyncio
    async def test_mentions_error(self, monkeypatch):
        fake, _ = _client(monkeypatch, None)
        fake.get_recent_mentions = AsyncMock(side_effect=InstagramAPIError(401, "e", "u"))
        db = _DB(team=_TEAM, results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException):
            await I.get_mentions(uuid.uuid4(), uuid.uuid4(), 10, AsyncMock(), db)


class TestAppReview:
    @pytest.mark.asyncio
    async def test_guide(self):
        out = await I.get_app_review_guide(AsyncMock())
        assert out["permission"] == "instagram_business_manage_messages"
        assert "screencast" in out["screencast_description"]

    @pytest.mark.asyncio
    async def test_status_no_accounts(self):
        db = _DB(results=[_Res(all_items=[])])
        out = await I.get_app_review_status(AsyncMock(), db)
        assert out["accounts"] == []
        assert out["needs_app_review"] is False

    @pytest.mark.asyncio
    async def test_status_granted(self, monkeypatch):
        acct = _account()
        db = _DB(results=[_Res(all_items=[acct])])
        monkeypatch.setattr(I, "decrypt_token", lambda e: "tok")

        class _C:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, **kw):
                return SimpleNamespace(status_code=200, json=lambda: {})

        monkeypatch.setattr(_httpx_global, "AsyncClient", lambda **kw: _C())
        out = await I.get_app_review_status(AsyncMock(), db)
        assert out["accounts"][0]["permission_status"] == "granted"

    @pytest.mark.asyncio
    async def test_status_error_codes(self, monkeypatch):
        monkeypatch.setattr(I, "decrypt_token", lambda e: "tok")

        for code, expected in [(3, "not_granted"), (10, "permission_denied"), (99, "error")]:
            acct = _account()
            db = _DB(results=[_Res(all_items=[acct])])

            class _C:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def get(self, url, **kw):
                    return SimpleNamespace(status_code=400, json=lambda c=code: {"error": {"code": c, "message": "m"}})

            monkeypatch.setattr(_httpx_global, "AsyncClient", lambda **kw: _C())
            out = await I.get_app_review_status(AsyncMock(), db)
            assert out["accounts"][0]["permission_status"] == expected
        # needs_app_review set when code 3
        assert out["needs_app_review"] is False  # last was 99

    @pytest.mark.asyncio
    async def test_status_forbidden_and_other(self, monkeypatch):
        monkeypatch.setattr(I, "decrypt_token", lambda e: "tok")
        for status, expected in [(403, "forbidden"), (500, "error")]:
            acct = _account()
            db = _DB(results=[_Res(all_items=[acct])])

            class _C:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def get(self, url, **kw):
                    return SimpleNamespace(status_code=status, json=lambda: {})

            monkeypatch.setattr(_httpx_global, "AsyncClient", lambda **kw: _C())
            out = await I.get_app_review_status(AsyncMock(), db)
            assert out["accounts"][0]["permission_status"] == expected

    @pytest.mark.asyncio
    async def test_status_exception_and_no_token(self, monkeypatch):
        acct_exc = _account()
        acct_notok = _account(access_token_enc=None)
        acct_noid = _account(account_id=None)
        db = _DB(results=[_Res(all_items=[acct_exc, acct_notok, acct_noid])])
        monkeypatch.setattr(I, "decrypt_token", lambda e: (_ for _ in ()).throw(ValueError("bad")))
        out = await I.get_app_review_status(AsyncMock(), db)
        statuses = out["accounts"]
        assert statuses[0]["permission_status"] == "error"
        assert statuses[1]["permission_status"] == "unknown"
        assert statuses[1]["has_token"] is False
        # acct_noid: decrypt raises → error
        assert statuses[2]["permission_status"] == "error"
