"""Coverage for app/services/espocrm_client.py — EspoCRM read/write client.

All HTTP is mocked with respx; no real CRM is contacted.
"""
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
        monkeypatch.setattr(
            E, "get_settings",
            lambda: SimpleNamespace(ESPOCRM_BASE_URL=base, ESPOCRM_API_KEY=key),
        )
    _set()
    return _set


class TestConfig:
    def test_cfg_strips_and_trims_slash(self, cfg):
        cfg(base="  http://crm.test:30700/ ", key=" abc ")
        assert E._cfg() == ("http://crm.test:30700", "abc")
        assert E.configured() is True

    def test_not_configured_when_missing(self, cfg):
        cfg(base=None, key="k")
        assert E.configured() is False
        cfg(base=BASE, key="")
        assert E.configured() is False


class TestEspoGet:
    @pytest.mark.asyncio
    async def test_unconfigured_raises(self, cfg):
        cfg(key="")
        with pytest.raises(RuntimeError, match="not configured"):
            await E.espo_get("/Lead")

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_api_key_and_returns_dict(self, cfg):
        route = respx.get(f"{BASE}/api/v1/Lead").mock(
            return_value=httpx.Response(200, json={"total": 3, "list": []})
        )
        out = await E.espo_get("/Lead", params={"a": "1"})
        assert out == {"total": 3, "list": []}
        req = route.calls.last.request
        assert req.headers["X-Api-Key"] == "k-123"
        assert req.url.params["a"] == "1"

    @pytest.mark.asyncio
    @respx.mock
    async def test_wraps_non_dict_body(self, cfg):
        respx.get(f"{BASE}/api/v1/X").mock(return_value=httpx.Response(200, json=[1, 2]))
        assert await E.espo_get("/X") == {"data": [1, 2]}

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_raises_with_status(self, cfg):
        respx.get(f"{BASE}/api/v1/X").mock(return_value=httpx.Response(403, text="denied"))
        with pytest.raises(RuntimeError, match="403: denied"):
            await E.espo_get("/X")

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_json_raises(self, cfg):
        respx.get(f"{BASE}/api/v1/X").mock(return_value=httpx.Response(200, text="<html>"))
        with pytest.raises(RuntimeError, match="non-JSON"):
            await E.espo_get("/X")


class TestListAll:
    @pytest.mark.asyncio
    async def test_paginates_until_short_page(self, monkeypatch):
        monkeypatch.setattr(E, "_PAGE", 2)
        calls = []
        pages = [[{"id": 1}, {"id": 2}], [{"id": 3}]]

        async def fake_get(path, params=None):
            calls.append((path, json.loads(params["searchParams"])))
            return {"list": pages.pop(0)}

        monkeypatch.setattr(E, "espo_get", fake_get)
        out = await E.list_all("Lead", select=["id"], where=[{"type": "isNotNull"}])
        assert [i["id"] for i in out] == [1, 2, 3]
        assert calls[0][0] == "/Lead"
        assert calls[0][1]["offset"] == 0 and calls[1][1]["offset"] == 2
        assert calls[0][1]["select"] == ["id"]
        assert calls[0][1]["where"] == [{"type": "isNotNull"}]

    @pytest.mark.asyncio
    async def test_respects_max_items_and_omits_optional(self, monkeypatch):
        monkeypatch.setattr(E, "_PAGE", 2)
        seen = []

        async def fake_get(path, params=None):
            seen.append(json.loads(params["searchParams"]))
            return {"list": [{"id": len(seen) * 10}, {"id": len(seen) * 10 + 1}]}

        monkeypatch.setattr(E, "espo_get", fake_get)
        out = await E.list_all("Account", max_items=3)
        assert len(out) == 3
        assert "select" not in seen[0] and "where" not in seen[0]
        assert seen[1]["maxSize"] == 1  # only the remaining item is requested

    @pytest.mark.asyncio
    async def test_empty_list_key(self, monkeypatch):
        async def fake_get(path, params=None):
            return {}

        monkeypatch.setattr(E, "espo_get", fake_get)
        assert await E.list_all("Lead") == []


class TestEntityLists:
    @pytest.mark.asyncio
    async def test_wrappers_pass_entity_and_filters(self, monkeypatch):
        captured = {}

        async def fake_list_all(entity, **kw):
            captured[entity] = kw
            return [{"entity": entity}]

        monkeypatch.setattr(E, "list_all", fake_list_all)
        assert await E.list_leads_for_campaign() == [{"entity": "Lead"}]
        assert await E.list_contacts() == [{"entity": "Contact"}]
        assert await E.list_accounts() == [{"entity": "Account"}]
        lead_where = captured["Lead"]["where"]
        assert {"type": "notEquals", "attribute": "status", "value": "Dead"} in lead_where
        assert {"type": "notEquals", "attribute": "status", "value": "Converted"} in lead_where
        assert "emailAddress" in captured["Contact"]["select"]
        assert "where" not in captured["Account"]

    @pytest.mark.asyncio
    async def test_campaign_profiles_contact_wins_lead_fills_gaps(self, monkeypatch):
        async def leads():
            return [
                {"emailAddress": " Ann@X.com ", "firstName": "Ann", "industry": "Retail", "title": "CEO"},
                {"emailAddress": "", "firstName": "NoMail"},
                {"emailAddress": "lead-only@x.com", "firstName": "Lee"},
            ]

        async def contacts():
            return [
                {"emailAddress": "ann@x.com", "firstName": "Annie", "industry": "", "title": None},
                {"emailAddress": None, "firstName": "Ghost"},
                {"emailAddress": "c@x.com", "firstName": "Cee"},
            ]

        monkeypatch.setattr(E, "list_leads_for_campaign", leads)
        monkeypatch.setattr(E, "list_contacts", contacts)
        prof = await E.campaign_profiles()
        assert set(prof) == {"ann@x.com", "lead-only@x.com", "c@x.com"}
        ann = prof["ann@x.com"]
        assert ann["entity"] == "Contact" and ann["firstName"] == "Annie"
        # empty contact fields don't clobber richer lead values
        assert ann["industry"] == "Retail" and ann["title"] == "CEO"
        assert prof["lead-only@x.com"]["entity"] == "Lead"
        assert prof["c@x.com"]["entity"] == "Contact"


class TestEspoPost:
    @pytest.mark.asyncio
    async def test_unconfigured_raises(self, cfg):
        cfg(base="")
        with pytest.raises(RuntimeError, match="not configured"):
            await E.espo_post("/Note", {})

    @pytest.mark.asyncio
    @respx.mock
    async def test_posts_json(self, cfg):
        route = respx.post(f"{BASE}/api/v1/Note").mock(
            return_value=httpx.Response(200, json={"id": "n1"})
        )
        assert await E.espo_post("/Note", {"post": "hi"}) == {"id": "n1"}
        req = route.calls.last.request
        assert json.loads(req.content) == {"post": "hi"}
        assert req.headers["X-Api-Key"] == "k-123"

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_dict_and_non_json(self, cfg):
        respx.post(f"{BASE}/api/v1/A").mock(return_value=httpx.Response(200, json=["x"]))
        respx.post(f"{BASE}/api/v1/B").mock(return_value=httpx.Response(204, text=""))
        assert await E.espo_post("/A", {}) == {"data": ["x"]}
        assert await E.espo_post("/B", {}) == {}

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_status_raises(self, cfg):
        respx.post(f"{BASE}/api/v1/Note").mock(return_value=httpx.Response(500, text="boom"))
        with pytest.raises(RuntimeError, match="500: boom"):
            await E.espo_post("/Note", {})


class TestAddLeadNote:
    @pytest.mark.asyncio
    async def test_skips_without_id_or_config(self, cfg):
        assert await E.add_lead_note("", "x") is False
        cfg(key="")
        assert await E.add_lead_note("L1", "x") is False

    @pytest.mark.asyncio
    async def test_posts_truncated_note(self, cfg, monkeypatch):
        sent = {}

        async def fake_post(path, payload):
            sent["path"], sent["payload"] = path, payload
            return {}

        monkeypatch.setattr(E, "espo_post", fake_post)
        assert await E.add_lead_note("L1", "y" * 20000, entity="Contact") is True
        assert sent["path"] == "/Note"
        p = sent["payload"]
        assert p["type"] == "Post" and p["parentType"] == "Contact" and p["parentId"] == "L1"
        assert len(p["post"]) == 10000

    @pytest.mark.asyncio
    async def test_failure_returns_false(self, cfg, monkeypatch):
        async def boom(path, payload):
            raise RuntimeError("down")

        monkeypatch.setattr(E, "espo_post", boom)
        assert await E.add_lead_note("L1", "x") is False


class TestLeadCounts:
    @pytest.mark.asyncio
    async def test_counts_and_failure_marker(self, monkeypatch):
        async def fake_get(path, params=None):
            if path == "/Opportunity":
                raise RuntimeError("403")
            return {"total": {"/Lead": 5, "/Contact": 2, "/Account": 1}[path]}

        monkeypatch.setattr(E, "espo_get", fake_get)
        assert await E.lead_counts() == {"lead": 5, "contact": 2, "account": 1, "opportunity": -1}
