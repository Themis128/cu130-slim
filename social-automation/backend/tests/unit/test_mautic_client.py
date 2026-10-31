"""Coverage for app/services/mautic_client.py — Mautic funnel client.

All HTTP is mocked with respx; no real Mautic is contacted.
"""

from types import SimpleNamespace

import httpx
import pytest
import respx

import app.services.mautic_client as M

BASE = "http://mautic.test:80"
HOST = "mautic.cloudless.gr"


@pytest.fixture
def cfg(monkeypatch):
    def _set(base=BASE, user="admin@test", pw="pw"):
        monkeypatch.setattr(
            M,
            "get_settings",
            lambda: SimpleNamespace(
                MAUTIC_BASE_URL=base, MAUTIC_USERNAME=user, MAUTIC_PASSWORD=pw,
                MAUTIC_PUBLIC_HOST="",
            ),
        )

    _set()
    return _set


class TestConfig:
    def test_cfg_strips(self, cfg):
        cfg(base="  http://mautic.test:80/  ", user=" u ", pw=" p ")
        base, user, pw, host = M._cfg()
        assert (base, user, pw) == ("http://mautic.test:80", "u", "p")
        assert host == HOST  # default canonical host
        assert M.configured() is True

    def test_not_configured_when_missing(self, cfg):
        cfg(base="", pw="p")
        assert M.configured() is False

    def test_headers_send_canonical_pair(self, cfg):
        assert M._headers() == {"Host": HOST, "X-Forwarded-Proto": "https"}


@pytest.mark.asyncio
class TestHttp:
    async def test_get_decodes_json_and_sends_headers(self, cfg, respx_mock):
        route = respx_mock.get(f"{BASE}/api/contacts").mock(
            return_value=httpx.Response(200, json={"total": "3", "contacts": {}})
        )
        data = await M.mautic_get("/contacts", params={"limit": 1})
        assert data["total"] == "3"
        sent = route.calls[0].request
        assert sent.headers["host"] == HOST
        assert sent.headers["x-forwarded-proto"] == "https"

    async def test_get_returns_status_body_on_non_json(self, cfg, respx_mock):
        respx_mock.get(f"{BASE}/api/x").mock(
            return_value=httpx.Response(500, text="<html>oops</html>")
        )
        data = await M.mautic_get("/x")
        assert data["_status"] == 500

    async def test_post_is_form_encoded(self, cfg, respx_mock):
        route = respx_mock.post(f"{BASE}/api/segments/new").mock(
            return_value=httpx.Response(201, json={"list": {"id": 1}})
        )
        await M.mautic_post("/segments/new", {"name": "S", "tags": ["a", "b"]})
        body = route.calls[0].request.content.decode()
        assert "name=S" in body and "tags=a%2Cb" in body  # list flattened

    async def test_raises_when_unconfigured(self, cfg):
        cfg(base="")
        with pytest.raises(RuntimeError, match="not configured"):
            await M.mautic_get("/contacts")


@pytest.mark.asyncio
class TestContacts:
    async def test_upsert_posts_contact_fields(self, cfg, respx_mock):
        route = respx_mock.post(f"{BASE}/api/contacts/new").mock(
            return_value=httpx.Response(200, json={"contact": {"id": 7}})
        )
        r = await M.upsert_contact("a@b.c", {"firstname": "A"})
        assert r["contact"]["id"] == 7
        body = route.calls[0].request.content.decode()
        assert "email=a%40b.c" in body and "firstname=A" in body

    async def test_list_contacts_search_syntax(self, cfg, respx_mock):
        route = respx_mock.get(f"{BASE}/api/contacts").mock(
            return_value=httpx.Response(200, json={"contacts": {"1": {"id": 1}}})
        )
        hits = await M.list_contacts(email_contains="themis")
        assert hits == [{"id": 1}]
        assert "email%3Athemis" in str(route.calls[0].request.url)


@pytest.mark.asyncio
class TestSegments:
    async def test_add_to_segment_route(self, cfg, respx_mock):
        route = respx_mock.post(f"{BASE}/api/segments/9/contact/7/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        await M.add_to_segment(7, 9)
        assert route.called

    async def test_create_segment_idempotent_on_alias_conflict(self, cfg, respx_mock):
        respx_mock.post(f"{BASE}/api/segments/new").mock(
            return_value=httpx.Response(
                400, json={"errors": [{"message": "alias in use"}]}
            )
        )
        respx_mock.get(f"{BASE}/api/segments").mock(
            return_value=httpx.Response(
                200, json={"lists": {"1": {"id": 1, "alias": "crm-sync"}}}
            )
        )
        r = await M.create_segment("CRM Sync", "crm-sync")
        assert r["id"] == 1

    async def test_create_segment_returns_new(self, cfg, respx_mock):
        respx_mock.post(f"{BASE}/api/segments/new").mock(
            return_value=httpx.Response(201, json={"list": {"id": 5}})
        )
        r = await M.create_segment("New", "new-alias")
        assert r["list"]["id"] == 5
