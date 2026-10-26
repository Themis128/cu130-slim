"""media_sources service + API + nextcloud media auto-sync task (#930)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.api import media_sources as api
from app.services import media_sources as ms
from app.services.media_sources import MediaSourceError, RemoteFile

pytestmark = pytest.mark.asyncio

_S = SimpleNamespace(
    NEXTCLOUD_DAV_URL="https://nc.example/remote.php/dav/files/bot/",
    NEXTCLOUD_USERNAME="bot", NEXTCLOUD_APP_PASSWORD="pw",
    GOOGLE_PHOTOS_CLIENT_ID="cid", GOOGLE_PHOTOS_CLIENT_SECRET="sec",
    GOOGLE_PHOTOS_REFRESH_TOKEN="rt", GOOGLE_DRIVE_REFRESH_TOKEN="",
)

_PROPFIND = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
 <d:response><d:href>/remote.php/dav/files/bot/Photos/</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
 </d:response>
 <d:response><d:href>/remote.php/dav/files/bot/Photos/a%20b.jpg</d:href>
  <d:propstat><d:prop><d:getcontenttype>image/jpeg</d:getcontenttype>
  <d:getcontentlength>12</d:getcontentlength><d:resourcetype/></d:prop></d:propstat>
 </d:response>
 <d:response><d:href>/remote.php/dav/files/bot/Photos/Sub/</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
 </d:response>
 <d:response><d:href>/remote.php/dav/files/bot/Photos/noprop</d:href></d:response>
</d:multistatus>"""


@pytest.fixture
def http(monkeypatch):
    """Route every httpx.AsyncClient in the service through a handler."""
    state = {"handler": lambda req: httpx.Response(200)}
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw.pop("auth", None)
        return real(*a, transport=httpx.MockTransport(lambda r: state["handler"](r)), **kw)

    monkeypatch.setattr(ms.httpx, "AsyncClient", factory)
    monkeypatch.setattr(ms, "get_settings", lambda: _S)
    return state


# ── service: nextcloud ────────────────────────────────────────────────────


async def test_nextcloud_list_parses_propfind(http):
    seen = {}

    def h(req):
        seen["m"], seen["url"], seen["depth"] = req.method, str(req.url), req.headers["Depth"]
        return httpx.Response(207, text=_PROPFIND)

    http["handler"] = h
    files = await ms.nextcloud_list("/Photos/")
    assert seen == {"m": "PROPFIND", "url": "https://nc.example/remote.php/dav/files/bot/Photos",
                    "depth": "1"}
    assert [(f.name, f.is_dir, f.size, f.mime_type) for f in files] == [
        ("a b.jpg", False, 12, "image/jpeg"), ("Sub", True, 0, "")]
    assert files[0].id == "Photos/a b.jpg"


async def test_nextcloud_list_error(http):
    http["handler"] = lambda r: httpx.Response(401, text="nope")
    with pytest.raises(MediaSourceError, match="PROPFIND 401"):
        await ms.nextcloud_list()


async def test_nextcloud_download_mime_from_header_and_guess(http):
    http["handler"] = lambda r: httpx.Response(
        200, content=b"x", headers={"content-type": "image/png; q=1"})
    assert await ms.nextcloud_download("P/a.png") == (b"x", "image/png")
    http["handler"] = lambda r: httpx.Response(200, content=b"y")
    assert await ms.nextcloud_download("P/a.jpg") == (b"y", "image/jpeg")
    assert (await ms.nextcloud_download("P/blob"))[1] == "application/octet-stream"


async def test_nextcloud_download_error(http):
    http["handler"] = lambda r: httpx.Response(404)
    with pytest.raises(MediaSourceError, match="GET P/a: 404"):
        await ms.nextcloud_download("P/a")


# ── service: google drive ─────────────────────────────────────────────────


def test_gdrive_connect_url(monkeypatch):
    monkeypatch.setattr(ms, "get_settings", lambda: _S)
    url = ms.gdrive_connect_url("http://cb")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=cid" in url and "drive.readonly" in url and "access_type=offline" in url


async def test_gdrive_exchange_code(http):
    http["handler"] = lambda r: httpx.Response(200, json={"refresh_token": "R"})
    assert await ms.gdrive_exchange_code("c", "http://cb") == "R"
    http["handler"] = lambda r: httpx.Response(400, json={"error": "invalid_grant"})
    with pytest.raises(MediaSourceError, match="no refresh_token"):
        await ms.gdrive_exchange_code("c", "http://cb")


async def test_gdrive_token_paths(http, monkeypatch):
    http["handler"] = lambda r: httpx.Response(200, json={"access_token": "T"})
    assert await ms._gdrive_token() == "T"
    http["handler"] = lambda r: httpx.Response(400, json={})
    with pytest.raises(MediaSourceError, match="token refresh failed"):
        await ms._gdrive_token()
    empty = SimpleNamespace(**{**_S.__dict__, "GOOGLE_PHOTOS_REFRESH_TOKEN": ""})
    monkeypatch.setattr(ms, "get_settings", lambda: empty)
    with pytest.raises(MediaSourceError, match="no Google refresh token"):
        await ms._gdrive_token()


@pytest.mark.parametrize(("kw", "needle"), [
    ({"folder_id": "F"}, "'F' in parents"),
    ({"query": "cat"}, "name contains 'cat'"),
    ({}, "mimeType contains 'image/'"),
])
async def test_gdrive_list_queries(http, monkeypatch, kw, needle):
    monkeypatch.setattr(ms, "_gdrive_token", AsyncMock(return_value="T"))
    seen = {}

    def h(req):
        seen["q"] = req.url.params["q"]
        seen["auth"] = req.headers["Authorization"]
        return httpx.Response(200, json={"files": [
            {"id": "1", "name": "a.jpg", "mimeType": "image/jpeg", "size": "5"},
            {"id": "2", "name": "dir", "mimeType": "application/vnd.google-apps.folder"},
        ]})

    http["handler"] = h
    files = await ms.gdrive_list(**kw)
    assert needle in seen["q"] and seen["auth"] == "Bearer T"
    assert [(f.id, f.size, f.is_dir) for f in files] == [("1", 5, False), ("2", 0, True)]


async def test_gdrive_list_error(http, monkeypatch):
    monkeypatch.setattr(ms, "_gdrive_token", AsyncMock(return_value="T"))
    http["handler"] = lambda r: httpx.Response(403, text="denied")
    with pytest.raises(MediaSourceError, match="drive list 403"):
        await ms.gdrive_list()


async def test_gdrive_download(http, monkeypatch):
    monkeypatch.setattr(ms, "_gdrive_token", AsyncMock(return_value="T"))

    def h(req):
        if req.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"bytes")
        return httpx.Response(200, json={"mimeType": "video/mp4", "name": "v"})

    http["handler"] = h
    assert await ms.gdrive_download("X") == (b"bytes", "video/mp4")

    def h2(req):
        if req.url.params.get("alt") == "media":
            return httpx.Response(500)
        return httpx.Response(200, json={})

    http["handler"] = h2
    with pytest.raises(MediaSourceError, match="drive get X: 500"):
        await ms.gdrive_download("X")


# ── API ───────────────────────────────────────────────────────────────────

_IMG = RemoteFile(id="P/a.jpg", name="a.jpg", mime_type="image/jpeg", size=1, is_dir=False)
_DOC = RemoteFile(id="P/a.pdf", name="a.pdf", mime_type="application/pdf", size=1, is_dir=False)
_DIR = RemoteFile(id="P/S", name="S", mime_type="", size=0, is_dir=True)


def _db(team=None):
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.first.return_value = team
    db.execute = AsyncMock(return_value=res)
    db.commit = AsyncMock()
    return db


def _user(team_id=None):
    u = SimpleNamespace(id="u1")
    if team_id:
        u.team_id = team_id
    return u


async def test_api_nextcloud_files(monkeypatch):
    monkeypatch.setattr(ms, "nextcloud_list", AsyncMock(return_value=[_IMG, _DOC, _DIR]))
    out = await api.nextcloud_files("P", user=_user())
    assert [f["name"] for f in out["files"]] == ["a.jpg", "S"]
    monkeypatch.setattr(ms, "nextcloud_list", AsyncMock(side_effect=MediaSourceError("x")))
    with pytest.raises(HTTPException) as e:
        await api.nextcloud_files("P", user=_user())
    assert e.value.status_code == 502


async def test_api_nextcloud_import(monkeypatch):
    monkeypatch.setattr(ms, "nextcloud_download", AsyncMock(
        side_effect=[(b"d", "image/jpeg"), MediaSourceError("gone")]))
    save = AsyncMock(return_value=SimpleNamespace(id="A1"))
    monkeypatch.setattr(api, "save_uploaded_media", save)
    db = _db(team=SimpleNamespace(team_id="T1"))
    out = await api.nextcloud_import(api.NcImport(paths=["P/a.jpg", "P/b.jpg"]), db=db, user=_user())
    assert out["imported"] == ["A1"] and out["errors"][0]["path"] == "P/b.jpg"
    assert save.await_args.kwargs["team_id"] == "T1"
    assert save.await_args.kwargs["original_filename"] == "a.jpg"
    db.commit.assert_awaited_once()


async def test_api_imports_without_team_400():
    for fn, body in ((api.nextcloud_import, api.NcImport(paths=["a"])),
                     (api.gdrive_import, api.GdImport(file_ids=["a"]))):
        with pytest.raises(HTTPException) as e:
            await fn(body, db=_db(team=None), user=_user())
        assert e.value.status_code == 400


async def test_api_gdrive_connect(monkeypatch):
    monkeypatch.setattr(ms, "get_settings", lambda: _S)
    assert "accounts.google.com" in (await api.gdrive_connect_url(api.GdConnectUrl(), user=_user()))["url"]
    monkeypatch.setattr(ms, "gdrive_exchange_code", AsyncMock(return_value="R"))
    assert (await api.gdrive_connect(api.GdConnect(code="c"), user=_user()))["refresh_token"] == "R"
    monkeypatch.setattr(ms, "gdrive_exchange_code", AsyncMock(side_effect=MediaSourceError("bad")))
    with pytest.raises(HTTPException) as e:
        await api.gdrive_connect(api.GdConnect(code="c"), user=_user())
    assert e.value.status_code == 400


async def test_api_gdrive_files(monkeypatch):
    monkeypatch.setattr(ms, "gdrive_list", AsyncMock(return_value=[_IMG]))
    assert (await api.gdrive_files(user=_user()))["files"][0]["id"] == "P/a.jpg"
    monkeypatch.setattr(ms, "gdrive_list", AsyncMock(side_effect=MediaSourceError("x")))
    with pytest.raises(HTTPException) as e:
        await api.gdrive_files(user=_user())
    assert e.value.status_code == 502


async def test_api_gdrive_import(monkeypatch):
    monkeypatch.setattr(ms, "gdrive_list", AsyncMock(return_value=[
        RemoteFile(id="F1", name="pic.jpg", mime_type="image/jpeg", size=1, is_dir=False)]))
    monkeypatch.setattr(ms, "gdrive_download", AsyncMock(
        side_effect=[(b"d", "image/jpeg"), (b"e", "image/png"), MediaSourceError("x")]))
    save = AsyncMock(return_value=SimpleNamespace(id="A"))
    monkeypatch.setattr(api, "save_uploaded_media", save)
    db = _db()
    out = await api.gdrive_import(api.GdImport(file_ids=["F1", "F2", "F3"]), db=db,
                                  user=_user(team_id="T9"))
    assert out["imported"] == ["A", "A"] and out["errors"][0]["file_id"] == "F3"
    names = [c.kwargs["original_filename"] for c in save.await_args_list]
    assert names == ["pic.jpg", "F2"]
    assert save.await_args.kwargs["team_id"] == "T9"
    db.execute.assert_not_awaited()


async def test_api_gdrive_import_listing_failure_falls_back_to_id(monkeypatch):
    monkeypatch.setattr(ms, "gdrive_list", AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(ms, "gdrive_download", AsyncMock(return_value=(b"d", "image/jpeg")))
    save = AsyncMock(return_value=SimpleNamespace(id="A"))
    monkeypatch.setattr(api, "save_uploaded_media", save)
    out = await api.gdrive_import(api.GdImport(file_ids=["F1"]), db=_db(), user=_user(team_id="T"))
    assert out["imported"] == ["A"] and save.await_args.kwargs["original_filename"] == "F1"


# ── worker: nextcloud media auto-sync ─────────────────────────────────────


class _Ctx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def _sync_db(results):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=results)
    db.commit = AsyncMock()
    return db


def _first(v):
    r = MagicMock()
    r.scalars.return_value.first.return_value = v
    return r


def _all(v):
    r = MagicMock()
    r.scalars.return_value.all.return_value = v
    return r


@pytest.fixture
def nc_task(monkeypatch):
    from app.core import config
    from app.db import session as session_mod
    from app.services import media_storage
    from app.worker.tasks import nextcloud_sync as T

    cfg = SimpleNamespace(NEXTCLOUD_SYNC_ENABLED=True, NEXTCLOUD_SYNC_FOLDER="")
    monkeypatch.setattr(config, "get_settings", lambda: cfg)
    state = SimpleNamespace(cfg=cfg, db=None, session_mod=session_mod, T=T)
    monkeypatch.setattr(session_mod, "async_session_maker", lambda: _Ctx(state.db))
    state.save = AsyncMock(return_value=SimpleNamespace(meta_data=None))
    monkeypatch.setattr(media_storage, "save_uploaded_media", state.save)
    return state


async def test_nc_media_sync_disabled(nc_task):
    nc_task.cfg.NEXTCLOUD_SYNC_ENABLED = False
    assert await nc_task.T._sync(None) == {"skipped": "disabled"}


async def test_nc_media_sync_list_error(nc_task, monkeypatch):
    monkeypatch.setattr(ms, "nextcloud_list", AsyncMock(side_effect=MediaSourceError("boom")))
    assert await nc_task.T._sync("X") == {"error": "boom"}


@pytest.mark.parametrize(("user", "team", "why"), [
    (None, None, "no user"), (SimpleNamespace(id="u"), None, "no team")])
async def test_nc_media_sync_missing_owner(nc_task, monkeypatch, user, team, why):
    monkeypatch.setattr(ms, "nextcloud_list", AsyncMock(return_value=[]))
    nc_task.db = _sync_db([_first(user), _first(team)])
    assert await nc_task.T._sync(None) == {"skipped": why}


async def test_nc_media_sync_imports_new_only(nc_task, monkeypatch):
    new = RemoteFile(id="Photos/n.jpg", name="n.jpg", mime_type="image/jpeg", size=1, is_dir=False)
    old = RemoteFile(id="Photos/o.jpg", name="o.jpg", mime_type="image/jpeg", size=1, is_dir=False)
    bad = RemoteFile(id="Photos/b.mp4", name="b.mp4", mime_type="video/mp4", size=1, is_dir=False)
    monkeypatch.setattr(ms, "nextcloud_list", AsyncMock(return_value=[new, old, bad, _DOC, _DIR]))
    monkeypatch.setattr(ms, "nextcloud_download", AsyncMock(
        side_effect=[(b"d", "image/jpeg"), MediaSourceError("x")]))
    nc_task.db = _sync_db([_first(SimpleNamespace(id="u")),
                           _first(SimpleNamespace(team_id="t")), _all(["Photos/o.jpg"])])
    out = await nc_task.T._sync(None)
    assert out["folder"] == "Photos" and out["new"] == ["n.jpg"] and out["skipped"] == 1
    assert out["errors"][0]["path"] == "Photos/b.mp4"
    assert nc_task.save.await_args.kwargs["team_id"] == "t"
    nc_task.db.commit.assert_awaited_once()


def test_nc_media_sync_task_wrapper(monkeypatch):
    from app.worker.tasks import nextcloud_sync as T

    async def fake(folder):
        return {"folder": folder}

    monkeypatch.setattr(T, "_sync", fake)
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    assert T.sync_nextcloud_media.run("Pics") == {"folder": "Pics"}
