"""Remaining branch arms in app/api/media.py.

- PATCH endpoints with omitted fields leave them untouched.
- Search/list without filters add no WHERE clauses beyond the team
  scope; checked on the compiled SQL.
- Remote view falls through empty MinIO/R2 payloads.
- Deleting a local asset whose file is already gone still removes the row.
- A pending video task has neither asset_id nor error.

Reuses the fakes from test_media_api_router.py; storage backends and
Celery are faked."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import media
from app.api.media import (
    BulkDeleteRequest,
    MediaAssetUpdateRequest,
    MediaCollectionUpdateRequest,
    bulk_delete_media,
    delete_media,
    generate_video_status,
    list_media,
    search_media,
    update_collection,
    update_media,
    view_media,
)
from tests.unit.test_media_api_router import _DB, _asset, _collection, _png_bytes, _team, _user

pytestmark = pytest.mark.asyncio


class _SQLDB(_DB):
    """_DB that records the compiled SQL of every executed statement."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.sql: list[str] = []

    async def execute(self, stmt):
        self.sql.append(" ".join(str(stmt.compile()).lower().split()))
        return await super().execute(stmt)


async def test_update_media_empty_body_changes_nothing(monkeypatch):
    a = _asset(filename="keep.png")
    a.alt_text, a.tags = "alt", ["t"]
    correct = AsyncMock()
    monkeypatch.setattr(media, "correct_text", correct)
    monkeypatch.setattr(media, "correct_tags", correct)
    db = _DB([a])
    await update_media(a.id, MediaAssetUpdateRequest(), a.team_id, _user(), db)
    assert (a.filename, a.alt_text, a.tags, a.is_favorite, a.is_archived) == ("keep.png", "alt", ["t"], False, False)
    correct.assert_not_awaited()
    assert db.committed == 1


async def test_update_collection_cover_only(monkeypatch):
    c = _collection()
    name, desc = c.name, c.description
    cover = uuid.uuid4()
    monkeypatch.setattr(media, "correct_text", AsyncMock())
    db = _DB([c, []])
    await update_collection(c.id, MediaCollectionUpdateRequest(cover_asset_id=cover), c.team_id, _user(), db)
    assert c.cover_asset_id == cover and c.name == name and c.description == desc
    media.correct_text.assert_not_awaited()


async def test_update_collection_name_only_keeps_cover(monkeypatch):
    c = _collection()
    old_cover = c.cover_asset_id
    monkeypatch.setattr(media, "correct_text", AsyncMock(return_value=""))
    db = _DB([c, []])
    await update_collection(c.id, MediaCollectionUpdateRequest(name="Raw"), c.team_id, _user(), db)
    assert c.name == "Raw"  # empty correction falls back to the raw name
    assert c.cover_asset_id == old_cover


async def test_search_without_filters_scopes_by_team_only():
    team = _team()
    db = _SQLDB(results=[[], []], team=team)
    out = await search_media(team.id, None, None, None, None, None, None, None, None, 1, 20, _user(), db)
    assert out.total == 0
    where = db.sql[0].split(" where ", 1)[1]
    assert "team_id" in where
    for col in ("like", "mime_type", "source", "collection_id", "is_favorite", "is_archived", "tags"):
        assert col not in where, col


async def test_list_unknown_type_adds_no_type_filter():
    team = _team()
    db = _SQLDB(results=[[]], team=team, scalar_value=0)
    out = await list_media(team.id, 1, 20, "audio", None, None, None, None, _user(), db)
    assert out.total == 0
    where = db.sql[-1].split(" where ", 1)[1]
    assert "mime_type" not in where and "source" not in where


async def test_view_skips_empty_minio_then_empty_r2(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(media.minio_storage, "minio_enabled", lambda: True)
    monkeypatch.setattr(media.minio_storage, "get_object", AsyncMock(return_value=b""))
    r2 = AsyncMock(return_value=b"")
    monkeypatch.setattr(media.r2_storage, "get_object", r2)
    with pytest.raises(HTTPException) as ei:
        await view_media(path="remote/pic.png", format=None)
    assert ei.value.status_code == 404
    r2.assert_awaited_once_with("remote/pic.png")

    r2.return_value = _png_bytes(mode="RGB")
    out = await view_media(path="remote/pic.png", format=None)
    assert out.media_type == "image/png"


async def test_delete_local_asset_with_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "UPLOAD_DIR", str(tmp_path))
    a = _asset(storage_backend="local", storage_path="never-written.png")
    db = _DB([a])
    await delete_media(a.id, a.team_id, _user(), db)
    assert db.deleted == [a]

    team = _team()
    b = _asset(team_id=team.id, storage_backend="local", storage_path="also-missing.png")
    db2 = _DB(results=[[b]], team=team)
    out = await bulk_delete_media(BulkDeleteRequest(ids=[b.id]), team.id, _user(), db2)
    assert out["deleted"] == 1 and db2.deleted == [b]


async def test_video_status_pending(monkeypatch):
    res = SimpleNamespace(status="PENDING", successful=lambda: False, failed=lambda: False, result=None)
    monkeypatch.setattr("celery.result.AsyncResult", lambda *a, **k: res)
    out = await generate_video_status("t1", uuid.uuid4(), _user())
    assert out.status == "PENDING" and out.asset_id is None and out.error is None
