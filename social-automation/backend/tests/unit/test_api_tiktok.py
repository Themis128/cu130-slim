"""Coverage for app/api/tiktok.py router."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.tiktok as T
from app.services.tiktok_api import TikTokAPIError


class _Res:
    def __init__(self, scalar=None, all_items=None):
        self._scalar = scalar
        self._all = all_items or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._all)


class _DB:
    def __init__(self, results):
        self._q = list(results)

    async def execute(self, *a, **kw):
        return self._q.pop(0)


def _account(**kw):
    d = dict(
        id=uuid.uuid4(),
        username="cloudless",
        status="active",
        scopes=["video.list", "video.publish"],
        access_token_enc=b"e",
        account_id="oid",
        meta_data={},
        token_expires_at=None,
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _user():
    return SimpleNamespace(id=uuid.uuid4())


def _client(monkeypatch, **methods):
    monkeypatch.setattr(T, "decrypt_token", lambda e: "tok")
    fake = SimpleNamespace(**{k: AsyncMock(return_value=v) for k, v in methods.items()})
    monkeypatch.setattr(T, "TikTokAPIClient", lambda **kw: fake)
    return fake


def _err(code=400):
    return TikTokAPIError(code, "oops", "url")


class TestAccount:
    @pytest.mark.asyncio
    async def test_missing_404(self):
        db = _DB([_Res(scalar=None)])
        with pytest.raises(HTTPException) as ei:
            await T._get_tiktok_account(db, uuid.uuid4(), _user())
        assert ei.value.status_code == 404

    def test_client_for(self, monkeypatch):
        monkeypatch.setattr(T, "decrypt_token", lambda e: "tok")
        captured = {}
        monkeypatch.setattr(T, "TikTokAPIClient", lambda **kw: captured.setdefault("kw", kw))
        acct = _account(meta_data={"open_id": "mo"})
        T._client_for(acct)
        assert captured["kw"]["open_id"] == "mo"
        captured.clear()
        T._client_for(_account(meta_data=None))
        assert captured["kw"]["open_id"] == "oid"

    def test_http_exc(self):
        e = T._http_exc(_err(429))
        assert e.status_code == 429


class TestHealth:
    @pytest.mark.asyncio
    async def test_full(self, monkeypatch):
        acct = _account(token_expires_at=datetime.now(UTC))
        db = _DB([_Res(scalar=acct), _Res(all_items=[1, 2])])
        fake = _client(monkeypatch, validate_token=None, get_creator_info={"data": {"creator_username": "cl", "max_video_post_duration_sec": 600}})
        fake.validate_token = AsyncMock(return_value=None)

        class _Browser:
            async def check_session(self):
                return {"status": "ok"}

            async def close(self):
                pass

        monkeypatch.setattr(T, "TikTokBrowserService", lambda: _Browser())
        out = await T.tiktok_health(acct.id, _user(), db)
        assert out.token_valid is True
        assert out.has_video_publish_scope is True
        assert out.creator.creator_username == "cl"
        assert out.sidecar_session == {"status": "ok"}
        assert out.pending_uploads_24h == 2

    @pytest.mark.asyncio
    async def test_token_invalid_creator_skipped_sidecar_down(self, monkeypatch):
        acct = _account(scopes=["video.list"])
        db = _DB([_Res(scalar=acct), _Res(all_items=[])])
        fake = _client(monkeypatch)
        fake.validate_token = AsyncMock(side_effect=_err(401))
        monkeypatch.setattr(T, "TikTokBrowserService", lambda: (_ for _ in ()).throw(OSError("no sidecar")))
        out = await T.tiktok_health(acct.id, _user(), db)
        assert out.token_valid is False
        assert out.creator is None
        assert out.sidecar_session["status"] == "unreachable"
        assert out.has_video_list_scope is True

    @pytest.mark.asyncio
    async def test_creator_info_error_swallowed(self, monkeypatch):
        acct = _account()
        db = _DB([_Res(scalar=acct), _Res(all_items=[])])
        fake = _client(monkeypatch)
        fake.validate_token = AsyncMock(return_value=None)
        fake.get_creator_info = AsyncMock(side_effect=_err(500))

        class _B:
            async def check_session(self):
                return {"status": "dead"}

            async def close(self):
                pass

        monkeypatch.setattr(T, "TikTokBrowserService", lambda: _B())
        out = await T.tiktok_health(acct.id, _user(), db)
        assert out.token_valid is True and out.creator is None


class TestCreatorInfo:
    @pytest.mark.asyncio
    async def test_happy(self, monkeypatch):
        acct = _account()
        db = _DB([_Res(scalar=acct)])
        _client(monkeypatch, get_creator_info={"data": {"creator_username": "c", "comment_disabled": False, "junk_field": "x"}})
        out = await T.tiktok_creator_info(acct.id, _user(), db)
        assert out.creator_username == "c"

    @pytest.mark.asyncio
    async def test_error(self, monkeypatch):
        db = _DB([_Res(scalar=_account())])
        fake = _client(monkeypatch)
        fake.get_creator_info = AsyncMock(side_effect=_err(403))
        with pytest.raises(HTTPException) as ei:
            await T.tiktok_creator_info(uuid.uuid4(), _user(), db)
        assert ei.value.status_code == 403


class TestVideos:
    @pytest.mark.asyncio
    async def test_list(self, monkeypatch):
        acct = _account()
        db = _DB([_Res(scalar=acct)])
        _client(
            monkeypatch,
            list_videos={
                "data": {
                    "videos": [{"id": 123, "create_time": 5, "like_count": "7", "view_count": 100, "title": "t"}, {"id": "x"}],
                    "cursor": 99,
                    "has_more": True,
                }
            },
        )
        out = await T.tiktok_list_videos(acct.id, 0, 20, _user(), db)
        assert len(out.videos) == 2
        assert out.videos[0].id == "123"
        assert out.videos[0].like_count == 7
        assert out.cursor == 99 and out.has_more is True

    @pytest.mark.asyncio
    async def test_list_scope_missing(self, monkeypatch):
        acct = _account(scopes=["video.publish"])
        db = _DB([_Res(scalar=acct)])
        _client(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            await T.tiktok_list_videos(acct.id, 0, 20, _user(), db)
        assert ei.value.status_code == 400
        assert "video.list" in ei.value.detail

    @pytest.mark.asyncio
    async def test_list_error(self, monkeypatch):
        db = _DB([_Res(scalar=_account())])
        fake = _client(monkeypatch)
        fake.list_videos = AsyncMock(side_effect=_err(500))
        with pytest.raises(HTTPException):
            await T.tiktok_list_videos(uuid.uuid4(), 0, 20, _user(), db)

    @pytest.mark.asyncio
    async def test_query(self, monkeypatch):
        db = _DB([_Res(scalar=_account())])
        fake = _client(monkeypatch, query_video={"data": {"videos": [{"id": "v1"}]}})
        body = T.QueryVideosIn(video_ids=["v1"])
        out = await T.tiktok_query_videos(uuid.uuid4(), body, _user(), db)
        assert out.videos[0].id == "v1"
        fake.query_video.assert_awaited_once_with(video_ids=["v1"])

    @pytest.mark.asyncio
    async def test_query_scope_missing(self, monkeypatch):
        db = _DB([_Res(scalar=_account(scopes=[]))])
        _client(monkeypatch)
        with pytest.raises(HTTPException):
            await T.tiktok_query_videos(uuid.uuid4(), T.QueryVideosIn(video_ids=["v"]), _user(), db)

    @pytest.mark.asyncio
    async def test_query_error(self, monkeypatch):
        db = _DB([_Res(scalar=_account())])
        fake = _client(monkeypatch)
        fake.query_video = AsyncMock(side_effect=_err(404))
        with pytest.raises(HTTPException):
            await T.tiktok_query_videos(uuid.uuid4(), T.QueryVideosIn(video_ids=["v"]), _user(), db)


class TestPublishStatusCancel:
    @pytest.mark.asyncio
    async def test_status(self, monkeypatch):
        db = _DB([_Res(scalar=_account())])
        _client(monkeypatch, check_publish_status={"data": {"status": "PROCESSING_UPLOAD", "publicaly_available_post_id": [1, 2], "uploaded_bytes": 100}})
        body = T.PublishIdIn(publish_id="p1")
        out = await T.tiktok_publish_status(uuid.uuid4(), body, _user(), db)
        assert out.status == "PROCESSING_UPLOAD"
        assert out.publicaly_available_post_id == [1, 2]

    @pytest.mark.asyncio
    async def test_status_error(self, monkeypatch):
        db = _DB([_Res(scalar=_account())])
        fake = _client(monkeypatch)
        fake.check_publish_status = AsyncMock(side_effect=_err(400))
        with pytest.raises(HTTPException):
            await T.tiktok_publish_status(uuid.uuid4(), T.PublishIdIn(publish_id="p"), _user(), db)

    @pytest.mark.asyncio
    async def test_cancel(self, monkeypatch):
        db = _DB([_Res(scalar=_account())])
        _client(monkeypatch, cancel_publish={"error": {"code": "ok", "message": "done", "log_id": "L1"}})
        out = await T.tiktok_publish_cancel(uuid.uuid4(), T.PublishIdIn(publish_id="p"), _user(), db)
        assert out["ok"] is True and out["log_id"] == "L1"

    @pytest.mark.asyncio
    async def test_cancel_error(self, monkeypatch):
        db = _DB([_Res(scalar=_account())])
        fake = _client(monkeypatch)
        fake.cancel_publish = AsyncMock(side_effect=_err(500))
        with pytest.raises(HTTPException):
            await T.tiktok_publish_cancel(uuid.uuid4(), T.PublishIdIn(publish_id="p"), _user(), db)


class TestUploads:
    def _targets(self):
        post = SimpleNamespace(platform_specific={"tiktok": {"publish_id": "v_inbox~v2.1"}}, created_at=datetime.now(UTC))
        t1 = SimpleNamespace(platform_post_id="v_inbox~v2.1", post_id=uuid.uuid4(), social_account_id=uuid.uuid4(), post=post)
        t2 = SimpleNamespace(
            platform_post_id="1234567890", post_id=uuid.uuid4(), social_account_id=uuid.uuid4(), post=SimpleNamespace(platform_specific=None, created_at=None)
        )
        t3 = SimpleNamespace(platform_post_id="v_inbox~v2.2", post_id=None, social_account_id=uuid.uuid4(), post=None)
        return [t1, t2, t3]

    @pytest.mark.asyncio
    async def test_live_status(self, monkeypatch):
        db = _DB([_Res(scalar=_account()), _Res(all_items=self._targets())])
        fake = _client(monkeypatch)
        fake.check_publish_status = AsyncMock(
            side_effect=[
                {"data": {"status": "PROCESSING_UPLOAD"}},
                TikTokAPIError(500, "x", "u"),
            ]
        )
        out = await T.tiktok_list_uploads(uuid.uuid4(), 24, True, _user(), db)
        assert len(out) == 3
        assert out[0].status == "PROCESSING_UPLOAD"
        assert out[0].is_pending is True
        assert out[0].post_id is not None
        # numeric display id → complete without polling
        assert out[1].status == "PUBLISH_COMPLETE"
        assert out[1].created_at is None
        # third: publish_id from platform_post_id (no post)
        assert out[2].status == "LOOKUP_FAILED"
        assert out[2].post_id is None

    @pytest.mark.asyncio
    async def test_no_live_status(self, monkeypatch):
        db = _DB([_Res(scalar=_account()), _Res(all_items=self._targets())])
        _client(monkeypatch)
        out = await T.tiktok_list_uploads(uuid.uuid4(), 24, False, _user(), db)
        assert out[0].status is None
        assert out[1].status == "PUBLISH_COMPLETE"
        assert out[0].is_pending is False

    @pytest.mark.asyncio
    async def test_empty(self, monkeypatch):
        db = _DB([_Res(scalar=_account()), _Res(all_items=[])])
        _client(monkeypatch)
        out = await T.tiktok_list_uploads(uuid.uuid4(), 24, True, _user(), db)
        assert out == []
