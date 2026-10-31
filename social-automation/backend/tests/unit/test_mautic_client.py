"""Unit tests for app/services/mautic_client.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import mautic_client as mc


def _settings(**over):
    base = dict(MAUTIC_BASE_URL=" http://mautic:80/ ", MAUTIC_USERNAME="admin", MAUTIC_PASSWORD="pw", MAUTIC_PUBLIC_HOST="")
    base.update(over)
    return SimpleNamespace(**base)


class _Resp:
    def __init__(self, payload=None, status=200, text="", bad_json=False):
        self._p, self.status_code, self.text, self._bad = payload, status, text, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._p


class _Client:
    calls: list = []
    resp: _Resp = _Resp({})
    init: dict = {}

    def __init__(self, **kw):
        _Client.init = kw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        _Client.calls.append(("GET", url, params))
        return _Client.resp

    async def post(self, url, data=None):
        _Client.calls.append(("POST", url, data))
        return _Client.resp


@pytest.fixture
def http(monkeypatch):
    _Client.calls, _Client.resp, _Client.init = [], _Resp({}), {}
    monkeypatch.setattr(mc.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(mc, "get_settings", lambda: _settings())
    return _Client


def test_cfg_and_headers(monkeypatch):
    monkeypatch.setattr(mc, "get_settings", lambda: _settings())
    assert mc._cfg() == ("http://mautic:80", "admin", "pw", "mautic.cloudless.gr")
    assert mc._headers() == {"Host": "mautic.cloudless.gr", "X-Forwarded-Proto": "https"}
    assert mc.configured() is True
    monkeypatch.setattr(mc, "get_settings", lambda: _settings(MAUTIC_PUBLIC_HOST="m.example", MAUTIC_PASSWORD=None))
    assert mc._headers()["Host"] == "m.example"
    assert mc.configured() is False


@pytest.mark.asyncio
async def test_get_post_unconfigured(monkeypatch):
    monkeypatch.setattr(mc, "get_settings", lambda: _settings(MAUTIC_BASE_URL=""))
    with pytest.raises(RuntimeError):
        await mc.mautic_get("/contacts")
    with pytest.raises(RuntimeError):
        await mc.mautic_post("/contacts/new", {})


@pytest.mark.asyncio
async def test_get_json_and_non_json(http):
    http.resp = _Resp({"total": 3})
    assert await mc.mautic_get("/contacts", {"limit": 1}) == {"total": 3}
    assert http.calls[-1] == ("GET", "http://mautic:80/api/contacts", {"limit": 1})
    assert http.init["auth"] == ("admin", "pw")
    assert http.init["headers"]["Host"] == "mautic.cloudless.gr"
    http.resp = _Resp(status=502, text="x" * 1000, bad_json=True)
    out = await mc.mautic_get("/contacts")
    assert out["_status"] == 502 and len(out["_body"]) == 400


@pytest.mark.asyncio
async def test_post_flattens_lists_and_non_json(http):
    http.resp = _Resp({"contact": {"id": 1}})
    assert await mc.mautic_post("/contacts/new", {"tags": ["a", 2], "email": "e"}) == {"contact": {"id": 1}}
    assert http.calls[-1][2] == {"tags": "a,2", "email": "e"}
    http.resp = _Resp(status=500, text="boom", bad_json=True)
    assert await mc.mautic_post("/x", {}) == {"_status": 500, "_body": "boom"}


@pytest.mark.asyncio
async def test_upsert_contact(monkeypatch):
    post = AsyncMock(return_value={"ok": 1})
    monkeypatch.setattr(mc, "mautic_post", post)
    assert await mc.upsert_contact("a@b.c", {"firstname": "A"}) == {"ok": 1}
    path, body = post.await_args.args
    assert path == "/contacts/new"
    assert body == {"firstname": "A", "email": "a@b.c", "overwriteWithBlank": False}
    await mc.upsert_contact("x@y.z")
    assert post.await_args.args[1]["email"] == "x@y.z"


@pytest.mark.asyncio
async def test_list_contacts_shapes(monkeypatch):
    get = AsyncMock(return_value={"contacts": {"1": {"id": 1}, "2": {"id": 2}}})
    monkeypatch.setattr(mc, "mautic_get", get)
    assert await mc.list_contacts("acme") == [{"id": 1}, {"id": 2}]
    assert get.await_args.kwargs["params"]["search"] == "email:acme"
    get.return_value = {"contacts": [{"id": 3}]}
    assert await mc.list_contacts() == [{"id": 3}]
    assert "search" not in get.await_args.kwargs["params"]
    get.return_value = {}
    assert await mc.list_contacts() == []


@pytest.mark.asyncio
async def test_add_to_segment(monkeypatch):
    post = AsyncMock(return_value={"success": 1})
    monkeypatch.setattr(mc, "mautic_post", post)
    assert await mc.add_to_segment(7, 3) == {"success": 1}
    assert post.await_args.args == ("/segments/3/contact/7/add", {})


@pytest.mark.asyncio
async def test_list_segments_shapes(monkeypatch):
    get = AsyncMock(return_value={"lists": {"1": {"alias": "a"}}})
    monkeypatch.setattr(mc, "mautic_get", get)
    assert await mc.list_segments() == [{"alias": "a"}]
    get.return_value = {"segments": [{"alias": "b"}]}
    assert await mc.list_segments() == [{"alias": "b"}]
    get.return_value = {}
    assert await mc.list_segments() == []


@pytest.mark.asyncio
async def test_create_segment_new_and_existing(monkeypatch):
    post = AsyncMock(return_value={"list": {"id": 5}})
    monkeypatch.setattr(mc, "mautic_post", post)
    assert await mc.create_segment("Hot", "hot") == {"list": {"id": 5}}

    post.return_value = {"errors": [{"code": 400}]}
    monkeypatch.setattr(mc, "list_segments", AsyncMock(return_value=[{"alias": "warm", "id": 1}, {"alias": "hot", "id": 9}]))
    assert await mc.create_segment("Hot", "hot") == {"list": {"alias": "hot", "id": 9}, "id": 9}

    monkeypatch.setattr(mc, "list_segments", AsyncMock(return_value=[]))
    assert await mc.create_segment("Hot", "hot") == {"errors": [{"code": 400}]}


@pytest.mark.asyncio
async def test_funnel_state(monkeypatch):
    monkeypatch.setattr(mc, "get_settings", lambda: _settings())
    monkeypatch.setattr(mc, "mautic_get", AsyncMock(return_value={"total": 12}))
    monkeypatch.setattr(mc, "list_segments", AsyncMock(return_value=[{"name": f"s{i}"} for i in range(25)]))
    out = await mc.funnel_state()
    assert out["configured"] is True and out["contacts"] == 12 and len(out["segments"]) == 20

    monkeypatch.setattr(mc, "mautic_get", AsyncMock(return_value={"contacts": [{"id": 1}]}))
    monkeypatch.setattr(mc, "list_segments", AsyncMock(return_value=[]))
    out = await mc.funnel_state()
    assert out["contacts"] == 1 and out["segments"] == []
