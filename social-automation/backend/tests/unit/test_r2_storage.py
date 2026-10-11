"""Tests for app/services/r2_storage.py — Cloudflare R2 REST client."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.services.r2_storage as r2


def _settings(**kw):
    base = dict(
        R2_BUCKET_NAME="bucket",
        CLOUDFLARE_ACCOUNT_ID="acct",
        R2_API_BASE="https://api.cloudflare.com/client/v4",
        R2_PUBLIC_URL="https://pub.example.com",
        CLOUDFLARE_API_TOKEN="tok",
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    monkeypatch.setattr(r2, "settings", _settings())


class _Resp:
    def __init__(self, code=200, data=None, content=b"data", text=""):
        self.status_code = code
        self._data = data or {}
        self.content = content
        self.text = text

    def json(self):
        return self._data


class _HTTP:
    def __init__(self, resp=None, exc=None):
        self.resp = resp or _Resp()
        self.exc = exc
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def put(self, url, **kw):
        self.calls.append(("PUT", url, kw))
        return self._ret()

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._ret()

    async def delete(self, url, **kw):
        self.calls.append(("DELETE", url, kw))
        return self._ret()

    async def head(self, url, **kw):
        self.calls.append(("HEAD", url, kw))
        return self._ret()

    def _ret(self):
        if self.exc:
            raise self.exc
        return self.resp


def _wire(monkeypatch, http):
    monkeypatch.setattr(r2.httpx, "AsyncClient", lambda **kw: http)


# ── key validation ───────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "/abs", "a/../b", "a/../../x", "bad key", "a?b"])
def test_validate_key_rejects(bad):
    with pytest.raises(HTTPException) as e:
        r2._validate_key(bad)
    assert e.value.status_code == 400


def test_validate_key_accepts():
    assert r2._validate_key("a/b-c_d.e") == "a/b-c_d.e"


# ── url builders ─────────────────────────────────────────────────────


def test_r2_object_url(monkeypatch):
    url = r2._r2_object_url("a/b c.png")
    assert url.endswith("/accounts/acct/r2/buckets/bucket/objects/a/b%20c.png")
    assert r2._r2_object_url("k", bucket="other").endswith("/buckets/other/objects/k")

    monkeypatch.setattr(r2, "settings", _settings(R2_BUCKET_NAME=""))
    assert r2._r2_object_url("k") is None
    monkeypatch.setattr(r2, "settings", _settings(CLOUDFLARE_ACCOUNT_ID=""))
    assert r2._r2_object_url("k") is None


def test_r2_public_url(monkeypatch):
    assert r2._r2_public_url("k") == "https://pub.example.com/k"
    monkeypatch.setattr(r2, "settings", _settings(R2_PUBLIC_URL=""))
    assert r2._r2_public_url("k") is None
    # trailing slash not doubled
    monkeypatch.setattr(r2, "settings", _settings(R2_PUBLIC_URL="https://x.io/"))
    assert r2._r2_public_url("k") == "https://x.io/k"


# ── upload ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_ok(monkeypatch):
    http = _HTTP(_Resp(200, {"result": {"etag": "e1"}}))
    _wire(monkeypatch, http)
    out = await r2.upload_object("k", b"data", content_type="image/png")
    assert out == {"key": "k", "etag": "e1", "size": 4,
                   "public_url": "https://pub.example.com/k"}
    assert http.calls[0][2]["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_upload_other_bucket_no_public_url(monkeypatch):
    _wire(monkeypatch, _HTTP(_Resp(200, {"result": {}})))
    out = await r2.upload_object("k", b"x", bucket="analytics")
    assert out["public_url"] is None


@pytest.mark.asyncio
async def test_upload_errors(monkeypatch):
    # not configured
    monkeypatch.setattr(r2, "settings", _settings(R2_BUCKET_NAME=""))
    with pytest.raises(HTTPException) as e:
        await r2.upload_object("k", b"x")
    assert e.value.status_code == 500

    # no token
    monkeypatch.setattr(r2, "settings", _settings(CLOUDFLARE_API_TOKEN=""))
    with pytest.raises(HTTPException) as e:
        await r2.upload_object("k", b"x")
    assert "CLOUDFLARE_API_TOKEN" in e.value.detail

    # upstream failure
    monkeypatch.setattr(r2, "settings", _settings())
    _wire(monkeypatch, _HTTP(_Resp(500, text="oops")))
    with pytest.raises(HTTPException) as e:
        await r2.upload_object("k", b"x")
    assert e.value.status_code == 502


# ── get ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_object(monkeypatch):
    _wire(monkeypatch, _HTTP(_Resp(200, content=b"bytes")))
    assert await r2.get_object("k") == b"bytes"

    _wire(monkeypatch, _HTTP(_Resp(404)))
    with pytest.raises(HTTPException) as e:
        await r2.get_object("k")
    assert e.value.status_code == 404

    _wire(monkeypatch, _HTTP(_Resp(503, text="down")))
    with pytest.raises(HTTPException) as e:
        await r2.get_object("k")
    assert e.value.status_code == 502

    monkeypatch.setattr(r2, "settings", _settings(R2_BUCKET_NAME=""))
    with pytest.raises(HTTPException):
        await r2.get_object("k")


# ── delete ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_object(monkeypatch):
    _wire(monkeypatch, _HTTP(_Resp(200)))
    assert await r2.delete_object("k") is True
    _wire(monkeypatch, _HTTP(_Resp(404)))
    assert await r2.delete_object("k") is True
    _wire(monkeypatch, _HTTP(_Resp(500, text="x")))
    with pytest.raises(HTTPException) as e:
        await r2.delete_object("k")
    assert e.value.status_code == 502

    monkeypatch.setattr(r2, "settings", _settings(R2_BUCKET_NAME=""))
    assert await r2.delete_object("k") is False

    monkeypatch.setattr(r2, "settings", _settings(CLOUDFLARE_API_TOKEN=""))
    with pytest.raises(HTTPException) as e:
        await r2.delete_object("k")
    assert "CLOUDFLARE_API_TOKEN" in e.value.detail


# ── exists ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_object_exists(monkeypatch):
    _wire(monkeypatch, _HTTP(_Resp(200)))
    assert await r2.object_exists("k") is True
    _wire(monkeypatch, _HTTP(_Resp(404)))
    assert await r2.object_exists("k") is False
    monkeypatch.setattr(r2, "settings", _settings(R2_BUCKET_NAME=""))
    assert await r2.object_exists("k") is False
