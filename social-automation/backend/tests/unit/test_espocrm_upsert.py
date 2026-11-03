"""espocrm_client: espo_patch + upsert_lead (email-dedup create/update)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import respx

import app.services.espocrm_client as E

BASE = "http://crm.test:30700"


@pytest.fixture
def cfg(monkeypatch):
    def _set(base=BASE, key="k-123"):
        monkeypatch.setattr(E, "get_settings", lambda: SimpleNamespace(ESPOCRM_BASE_URL=base, ESPOCRM_API_KEY=key))

    _set()
    return _set


class TestEspoPatch:
    @pytest.mark.asyncio
    async def test_unconfigured_raises(self, cfg):
        cfg(key="")
        with pytest.raises(RuntimeError, match="not configured"):
            await E.espo_patch("/Lead/1", {})

    @pytest.mark.asyncio
    @respx.mock
    async def test_patch_dict_list_nonjson_and_error(self, cfg):
        route = respx.patch(f"{BASE}/api/v1/Lead/1")
        route.mock(return_value=httpx.Response(200, json={"id": "1"}))
        assert await E.espo_patch("/Lead/1", {"status": "In Process"}) == {"id": "1"}
        req = route.calls.last.request
        assert req.headers["X-Api-Key"] == "k-123"
        assert json.loads(req.content) == {"status": "In Process"}

        route.mock(return_value=httpx.Response(200, json=[1]))
        assert await E.espo_patch("/Lead/1", {}) == {"data": [1]}
        route.mock(return_value=httpx.Response(200, text="ok"))
        assert await E.espo_patch("/Lead/1", {}) == {}
        route.mock(return_value=httpx.Response(409, text="conflict"))
        with pytest.raises(RuntimeError, match="409: conflict"):
            await E.espo_patch("/Lead/1", {})


@pytest.mark.asyncio
async def test_link_target_list_missing_list(cfg, monkeypatch):
    async def no_list(name):
        return None

    monkeypatch.setattr(E, "target_list_id", no_list)
    assert await E.link_lead_to_target_list("L1", "Survey") is False


class _Fake:
    def __init__(self, found, rel=None, rel_error=False, created=None):
        self.found, self.rel, self.rel_error = found, rel, rel_error
        self.created = created or {"id": "NEW"}
        self.gets, self.posts, self.patches = [], [], []

    async def get(self, path, params=None):
        self.gets.append((path, params))
        if path.endswith("/targetLists"):
            if self.rel_error:
                raise RuntimeError("rel down")
            return self.rel or {}
        return self.found

    async def post(self, path, payload):
        self.posts.append((path, payload))
        return self.created

    async def patch(self, path, payload):
        self.patches.append((path, payload))
        return {}


def _install(monkeypatch, fake):
    monkeypatch.setattr(E, "espo_get", fake.get)
    monkeypatch.setattr(E, "espo_post", fake.post)
    monkeypatch.setattr(E, "espo_patch", fake.patch)


@pytest.mark.asyncio
async def test_upsert_skips_without_email_or_config(cfg):
    assert await E.upsert_lead({"emailAddress": "  "}) is None
    cfg(key="")
    assert await E.upsert_lead({"emailAddress": "a@b.c"}) is None


@pytest.mark.asyncio
async def test_upsert_creates_new_lead(cfg, monkeypatch):
    fake = _Fake({"list": []})
    _install(monkeypatch, fake)
    out = await E.upsert_lead({"emailAddress": " A@B.C ", "firstName": "Ann", "lastName": "", "phoneNumber": None, "targetListsIds": ["TL1"], "x": []})
    assert out == "NEW"
    path, body = fake.posts[0]
    assert path == "/Lead"
    assert body == {"emailAddress": "a@b.c", "firstName": "Ann", "targetListsIds": ["TL1"], "status": "New"}
    search = json.loads(fake.gets[0][1]["searchParams"])
    assert search["where"][0]["value"] == "a@b.c"


@pytest.mark.asyncio
async def test_upsert_existing_without_id(cfg, monkeypatch):
    fake = _Fake({"list": [{"status": "New"}]})
    _install(monkeypatch, fake)
    assert await E.upsert_lead({"emailAddress": "a@b.c"}) is None
    assert fake.patches == []


@pytest.mark.asyncio
async def test_upsert_existing_merges_fields_desc_and_lists(cfg, monkeypatch):
    existing = {"id": "L1", "status": "Converted", "firstName": "Old", "lastName": None, "description": "first touch"}
    fake = _Fake({"list": [existing]}, rel={"list": [{"id": "TL0"}, {"name": "no-id"}]})
    _install(monkeypatch, fake)
    out = await E.upsert_lead(
        {
            "emailAddress": "a@b.c",
            "firstName": "",
            "lastName": "New",
            "utmSource": "li",
            "utmMedium": "",
            "description": "second touch",
            "targetListsIds": ["TL1", "TL0"],
            "status": "New",
        }
    )
    assert out == "L1"
    path, upd = fake.patches[0]
    assert path == "/Lead/L1"
    assert upd["firstName"] == "Old" and upd["lastName"] == "New"
    assert upd["utmSource"] == "li" and "utmMedium" not in upd
    assert upd["description"] == "first touch\nsecond touch"
    assert upd["targetListsIds"] == ["TL0", "TL1"]
    assert "status" not in upd  # never resets status


@pytest.mark.asyncio
async def test_upsert_existing_desc_rules_and_rel_failure(cfg, monkeypatch):
    # empty old description → new one used as-is; relation read fails → lists untouched
    fake = _Fake({"list": [{"id": "L2", "description": ""}]}, rel_error=True)
    _install(monkeypatch, fake)
    assert await E.upsert_lead({"emailAddress": "a@b.c", "description": "hello", "targetListsIds": ["TL1"]}) == "L2"
    upd = fake.patches[0][1]
    assert upd["description"] == "hello" and "targetListsIds" not in upd
    assert "firstName" not in upd and "lastName" not in upd

    # description already contained → not duplicated; no new lists → no relation read
    fake = _Fake({"list": [{"id": "L3", "description": "already hello there"}]})
    _install(monkeypatch, fake)
    assert await E.upsert_lead({"emailAddress": "a@b.c", "description": "hello"}) == "L3"
    assert "description" not in fake.patches[0][1]
    assert all(not p.endswith("/targetLists") for p, _ in fake.gets)


@pytest.mark.asyncio
async def test_upsert_failure_returns_none(cfg, monkeypatch):
    async def boom(path, params=None):
        raise RuntimeError("down")

    monkeypatch.setattr(E, "espo_get", boom)
    assert await E.upsert_lead({"emailAddress": "a@b.c"}) is None
