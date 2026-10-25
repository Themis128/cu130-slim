"""Unit tests for media sources (Nextcloud/Google Drive) service, API and sync task."""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.api import media_sources as API
from app.services import media_sources as MS

_REAL_CLIENT = httpx.AsyncClient

_SETTINGS = SimpleNamespace(
    NEXTCLOUD_DAV_URL="https://nc.example/remote.php/dav/files/bot/",
    NEXTCLOUD_USERNAME="bot",
    NEXTCLOUD_APP_PASSWORD="pw",
    GOOGLE_PHOTOS_CLIENT_ID="cid",
    GOOGLE_PHOTOS_CLIENT_SECRET="secret",
    GOOGLE_PHOTOS_REFRESH_TOKEN="",
    GOOGLE_DRIVE_REFRESH_TOKEN="rt",
)


def _use_transport(monkeypatch, handler, settings=_SETTINGS):
    def factory(*a, **kw):
        kw.pop("follow_redirects", None)
        return _REAL_CLIENT(transport=httpx.MockTransport(handler),
                            follow_redirects=True, **kw)

    monkeypatch.setattr(MS.httpx, "AsyncClient", factory)
    monkeypatch.setattr(MS, "get_settings", lambda: settings)


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


# ── service: Nextcloud ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nextcloud_list_parses_propfind(monkeypatch):
    seen = {}

    def handler(req):
        seen["method"] = req.method
        seen["url"] = str(req.url)
        seen["depth"] = req.headers.get("Depth")
        return httpx.Response(207, text=_PROPFIND)

    _use_transport(monkeypatch, handler)
    files = await MS.nextcloud_list("/Photos/")
    assert seen["method"] == "PROPFIND" and seen["depth"] == "1"
    assert seen["url"].endswith("/files/bot/Photos")
    by = {f.name: f for f in files}
    assert set(by) == {"a b.jpg", "Sub"}
    assert by["a b.jpg"].id == "Photos/a b.jpg"
    assert by["a b.jpg"].mime_type == "image/jpeg" and by["a b.jpg"].size == 12
    assert by["Sub"].is_dir is True


@pytest.mark.asyncio
async def test_nextcloud_list_error(monkeypatch):
    _use_transport(monkeypatch, lambda req: httpx.Response(401, text="nope"))
    with pytest.raises(MS.MediaSourceError, match="PROPFIND 401"):
        await MS.nextcloud_list("Photos")


@pytest.mark.asyncio
async def test_nextcloud_list_href_without_dav_prefix(monkeypatch):
    xml = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
     <d:response><d:href>plain</d:href><d:propstat><d:prop>
     <d:resourcetype/></d:prop></d:propstat></d:response></d:multistatus>"""
    _use_transport(monkeypatch, lambda req: httpx.Response(200, text=xml))
    files = await MS.nextcloud_list("")
    assert [f.name for f in files] == ["plain"]


@pytest.mark.asyncio
async def test_nextcloud_download_ok_and_mime_fallbacks(monkeypatch):
    def handler(req):
        if req.url.path.endswith("x.png"):
            return httpx.Response(200, content=b"PNG",
                                  headers={"content-type": "image/png; q=1"})
        return httpx.Response(200, content=b"??", headers={"content-type": ""})

    _use_transport(monkeypatch, handler)
    assert await MS.nextcloud_download("Photos/x.png") == (b"PNG", "image/png")
    assert await MS.nextcloud_download("Photos/y.mp4") == (b"??", "video/mp4")
    assert (await MS.nextcloud_download("Photos/noext"))[1] == "application/octet-stream"


@pytest.mark.asyncio
async def test_nextcloud_download_error(monkeypatch):
    _use_transport(monkeypatch, lambda req: httpx.Response(404))
    with pytest.raises(MS.MediaSourceError, match="GET Photos/x: 404"):
        await MS.nextcloud_download("Photos/x")


def test_nc_client_uses_basic_auth(monkeypatch):
    monkeypatch.setattr(MS, "get_settings", lambda: _SETTINGS)
    c = MS._nc_client()
    try:
        assert isinstance(c.auth, httpx.BasicAuth)
    finally:
        asyncio.run(c.aclose())


# ── service: Google Drive ─────────────────────────────────────────────────


def test_gdrive_connect_url(monkeypatch):
    monkeypatch.setattr(MS, "get_settings", lambda: _SETTINGS)
    url = MS.gdrive_connect_url("http://localhost:4180")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "drive.readonly" in url and "access_type=offline" in url
    assert "client_id=cid" in url


@pytest.mark.asyncio
async def test_gdrive_exchange_code(monkeypatch):
    _use_transport(monkeypatch,
                   lambda req: httpx.Response(200, json={"refresh_token": "R"}))
    assert await MS.gdrive_exchange_code("c", "http://x") == "R"


@pytest.mark.asyncio
async def test_gdrive_exchange_code_missing_token(monkeypatch):
    _use_transport(monkeypatch, lambda req: httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(MS.MediaSourceError, match="no refresh_token"):
        await MS.gdrive_exchange_code("c", "http://x")


@pytest.mark.asyncio
async def test_gdrive_token_missing_refresh_token(monkeypatch):
    s = SimpleNamespace(**{**vars(_SETTINGS), "GOOGLE_DRIVE_REFRESH_TOKEN": "",
                           "GOOGLE_PHOTOS_REFRESH_TOKEN": "  "})
    _use_transport(monkeypatch, lambda req: httpx.Response(200), settings=s)
    with pytest.raises(MS.MediaSourceError, match="no Google refresh token"):
        await MS._gdrive_token()


@pytest.mark.asyncio
async def test_gdrive_token_refresh_failed(monkeypatch):
    _use_transport(monkeypatch, lambda req: httpx.Response(400, json={"error": "x"}))
    with pytest.raises(MS.MediaSourceError, match="token refresh failed"):
        await MS._gdrive_token()


def _drive_handler(list_status=200, get_status=200, calls=None):
    def handler(req):
        if calls is not None:
            calls.append(req)
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "AT"})
        if req.url.path == "/drive/v3/files":
            if list_status != 200:
                return httpx.Response(list_status, text="boom")
            return httpx.Response(200, json={"files": [
                {"id": "1", "name": "a.jpg", "mimeType": "image/jpeg", "size": "5"},
                {"id": "2", "name": "Dir",
                 "mimeType": "application/vnd.google-apps.folder"},
            ]})
        if req.url.params.get("alt") == "media":
            return httpx.Response(get_status, content=b"DATA")
        return httpx.Response(200, json={"mimeType": "image/jpeg", "name": "a.jpg"})

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,expect", [
    ({}, "mimeType contains 'image/'"),
    ({"query": "cat"}, "name contains 'cat'"),
    ({"folder_id": "F"}, "'F' in parents"),
])
async def test_gdrive_list(monkeypatch, kwargs, expect):
    calls: list = []
    _use_transport(monkeypatch, _drive_handler(calls=calls))
    files = await MS.gdrive_list(**kwargs)
    assert [(f.id, f.size, f.is_dir) for f in files] == [("1", 5, False), ("2", 0, True)]
    q = calls[-1].url.params["q"]
    assert expect in q and "trashed=false" in q
    assert calls[-1].headers["Authorization"] == "Bearer AT"


@pytest.mark.asyncio
async def test_gdrive_list_error(monkeypatch):
    _use_transport(monkeypatch, _drive_handler(list_status=403))
    with pytest.raises(MS.MediaSourceError, match="drive list 403"):
        await MS.gdrive_list()


@pytest.mark.asyncio
async def test_gdrive_download(monkeypatch):
    _use_transport(monkeypatch, _drive_handler())
    assert await MS.gdrive_download("1") == (b"DATA", "image/jpeg")


@pytest.mark.asyncio
async def test_gdrive_download_error(monkeypatch):
    _use_transport(monkeypatch, _drive_handler(get_status=500))
    with pytest.raises(MS.MediaSourceError, match="drive get 1: 500"):
        await MS.gdrive_download("1")


@pytest.mark.asyncio
async def test_gdrive_download_default_mime(monkeypatch):
    def handler(req):
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "AT"})
        if req.url.params.get("alt") == "media":
            return httpx.Response(200, content=b"D")
        return httpx.Response(200, json={})

    _use_transport(monkeypatch, handler)
    assert await MS.gdrive_download("9") == (b"D", "application/octet-stream")


# ── API ───────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value

    def all(self):
        return self._value


class _DB:
    def __init__(self, *results):
        self._results = list(results)
        self.commits = 0

    async def execute(self, *_a, **_k):
        return _Result(self._results.pop(0))

    async def commit(self):
        self.commits += 1


def _rf(**kw):
    base = {"id": "p/a.jpg", "name": "a.jpg", "mime_type": "image/jpeg",
            "size": 1, "is_dir": False}
    return MS.RemoteFile(**{**base, **kw})


def _patch_save(monkeypatch, target, fail_on=()):
    saved = []

    async def fake_save(db, **kw):
        if kw["original_filename"] in fail_on:
            raise RuntimeError("disk full")
        saved.append(kw)
        return SimpleNamespace(id=uuid.uuid4(), meta_data=None)

    monkeypatch.setattr(target, "save_uploaded_media", fake_save)
    return saved


def test_media_only_filters():
    files = [_rf(), _rf(name="d", is_dir=True, mime_type=""),
             _rf(name="v", mime_type="video/mp4"), _rf(name="t", mime_type="text/plain")]
    assert [f.name for f in API._media_only(files)] == ["a.jpg", "d", "v"]


@pytest.mark.asyncio
async def test_api_nextcloud_files(monkeypatch):
    async def ok(path):
        return [_rf(), _rf(name="t", mime_type="text/plain")]

    monkeypatch.setattr(API.media_sources, "nextcloud_list", ok)
    out = await API.nextcloud_files(path="p", user=SimpleNamespace())
    assert out["path"] == "p" and [f["name"] for f in out["files"]] == ["a.jpg"]


@pytest.mark.asyncio
async def test_api_nextcloud_files_error(monkeypatch):
    async def bad(path):
        raise MS.MediaSourceError("down")

    monkeypatch.setattr(API.media_sources, "nextcloud_list", bad)
    with pytest.raises(HTTPException) as ei:
        await API.nextcloud_files(path="p", user=SimpleNamespace())
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_api_nextcloud_import(monkeypatch):
    async def dl(p):
        return b"x", "image/jpeg"

    monkeypatch.setattr(API.media_sources, "nextcloud_download", dl)
    saved = _patch_save(monkeypatch, API, fail_on={"bad.jpg"})
    db = _DB(SimpleNamespace(team_id="T"))
    user = SimpleNamespace(id="U")
    out = await API.nextcloud_import(
        API.NcImport(paths=["Photos/a.jpg", "Photos/bad.jpg"]), db=db, user=user)
    assert len(out["imported"]) == 1
    assert out["errors"][0]["path"] == "Photos/bad.jpg"
    assert saved[0]["team_id"] == "T" and saved[0]["original_filename"] == "a.jpg"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_api_nextcloud_import_user_team_id_and_no_team(monkeypatch):
    async def dl(p):
        return b"x", "image/jpeg"

    monkeypatch.setattr(API.media_sources, "nextcloud_download", dl)
    saved = _patch_save(monkeypatch, API)
    out = await API.nextcloud_import(
        API.NcImport(paths=["a.jpg"]), db=_DB(),
        user=SimpleNamespace(id="U", team_id="T2"))
    assert len(out["imported"]) == 1 and saved[0]["team_id"] == "T2"

    with pytest.raises(HTTPException) as ei:
        await API.nextcloud_import(API.NcImport(paths=[]), db=_DB(None),
                                   user=SimpleNamespace(id="U"))
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_api_gdrive_connect_url_and_connect(monkeypatch):
    monkeypatch.setattr(API.media_sources, "gdrive_connect_url", lambda r: f"URL:{r}")
    out = await API.gdrive_connect_url(API.GdConnectUrl(), user=SimpleNamespace())
    assert out == {"url": "URL:http://localhost:4180"}

    async def ok(code, redirect):
        return "RT"

    monkeypatch.setattr(API.media_sources, "gdrive_exchange_code", ok)
    out = await API.gdrive_connect(API.GdConnect(code="c"), user=SimpleNamespace())
    assert out["refresh_token"] == "RT"

    async def bad(code, redirect):
        raise MS.MediaSourceError("nope")

    monkeypatch.setattr(API.media_sources, "gdrive_exchange_code", bad)
    with pytest.raises(HTTPException) as ei:
        await API.gdrive_connect(API.GdConnect(code="c"), user=SimpleNamespace())
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_api_gdrive_files(monkeypatch):
    async def ok(query, folder_id):
        return [_rf(id="1")]

    monkeypatch.setattr(API.media_sources, "gdrive_list", ok)
    out = await API.gdrive_files(query="", folder_id=None, user=SimpleNamespace())
    assert out["files"][0]["id"] == "1"

    async def bad(query, folder_id):
        raise MS.MediaSourceError("x")

    monkeypatch.setattr(API.media_sources, "gdrive_list", bad)
    with pytest.raises(HTTPException) as ei:
        await API.gdrive_files(query="", folder_id=None, user=SimpleNamespace())
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_api_gdrive_import(monkeypatch):
    async def lst(page_size):
        return [_rf(id="1", name="one.jpg")]

    async def dl(fid):
        if fid == "bad":
            raise RuntimeError("gone")
        return b"x", "image/jpeg"

    monkeypatch.setattr(API.media_sources, "gdrive_list", lst)
    monkeypatch.setattr(API.media_sources, "gdrive_download", dl)
    saved = _patch_save(monkeypatch, API)
    db = _DB(SimpleNamespace(team_id="T"))
    out = await API.gdrive_import(API.GdImport(file_ids=["1", "2", "bad"]),
                                  db=db, user=SimpleNamespace(id="U"))
    assert len(out["imported"]) == 2
    assert out["errors"] == [{"file_id": "bad", "error": "gone"}]
    assert [s["original_filename"] for s in saved] == ["one.jpg", "2"]


@pytest.mark.asyncio
async def test_api_gdrive_import_list_fails_and_no_team(monkeypatch):
    async def lst(page_size):
        raise RuntimeError("no list")

    async def dl(fid):
        return b"x", "image/jpeg"

    monkeypatch.setattr(API.media_sources, "gdrive_list", lst)
    monkeypatch.setattr(API.media_sources, "gdrive_download", dl)
    saved = _patch_save(monkeypatch, API)
    out = await API.gdrive_import(API.GdImport(file_ids=["7"]), db=_DB(),
                                  user=SimpleNamespace(id="U", team_id="T"))
    assert len(out["imported"]) == 1 and saved[0]["original_filename"] == "7"

    with pytest.raises(HTTPException) as ei:
        await API.gdrive_import(API.GdImport(file_ids=[]), db=_DB(None),
                                user=SimpleNamespace(id="U"))
    assert ei.value.status_code == 403


# ── worker task: sync_nextcloud_media ─────────────────────────────────────


def _patch_task(monkeypatch, *, files=None, list_exc=None, db=None,
                enabled=True, folder="", fail_on=()):
    from contextlib import asynccontextmanager

    from app.worker.tasks import nextcloud_sync as T

    settings = SimpleNamespace(NEXTCLOUD_SYNC_ENABLED=enabled,
                               NEXTCLOUD_SYNC_FOLDER=folder)
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)

    async def lst(folder):
        if list_exc:
            raise list_exc
        return files or []

    async def dl(path):
        return b"x", "image/jpeg"

    monkeypatch.setattr(MS, "nextcloud_list", lst)
    monkeypatch.setattr(MS, "nextcloud_download", dl)
    import app.services.media_storage as storage

    saved = _patch_save(monkeypatch, storage, fail_on=fail_on)

    @asynccontextmanager
    async def fake_session():
        yield db

    monkeypatch.setattr(T, "task_session", fake_session)
    return T, saved


def test_sync_nextcloud_media_task_runs_coroutine(monkeypatch):
    T, _ = _patch_task(monkeypatch, enabled=False)
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    assert T.sync_nextcloud_media.run() == {"skipped": "disabled"}


@pytest.mark.asyncio
async def test_sync_media_list_error(monkeypatch):
    T, _ = _patch_task(monkeypatch, list_exc=MS.MediaSourceError("down"))
    assert await T._sync("X") == {"error": "down"}


@pytest.mark.asyncio
async def test_sync_media_no_user_or_team(monkeypatch):
    T, _ = _patch_task(monkeypatch, db=_DB(None))
    assert await T._sync(None) == {"skipped": "no user"}
    T, _ = _patch_task(monkeypatch, db=_DB(SimpleNamespace(id="U"), None))
    assert await T._sync(None) == {"skipped": "no team"}


@pytest.mark.asyncio
async def test_sync_media_imports_new_files(monkeypatch):
    files = [
        _rf(id="Photos/old.jpg", name="old.jpg"),
        _rf(id="Photos/new.jpg", name="new.jpg"),
        _rf(id="Photos/bad.jpg", name="bad.jpg"),
        _rf(id="Photos/doc.txt", name="doc.txt", mime_type="text/plain"),
        _rf(id="Photos/Dir", name="Dir", is_dir=True, mime_type="image/x"),
    ]
    db = _DB(SimpleNamespace(id="U"), SimpleNamespace(team_id="T"), ["Photos/old.jpg"])
    T, saved = _patch_task(monkeypatch, files=files, db=db, fail_on={"bad.jpg"})
    out = await T._sync(None)
    assert out["folder"] == "Photos"
    assert out["new"] == ["new.jpg"]
    assert out["errors"][0]["path"] == "Photos/bad.jpg"
    assert saved[0]["team_id"] == "T" and saved[0]["user_id"] == "U"
    assert db.commits == 1
