"""Coverage for linkedin_api — error branches, payload builder, org names."""

from types import SimpleNamespace

import pytest

import app.services.linkedin_api as LA

pytestmark = pytest.mark.asyncio


def test_error_auto_message():
    e = LA.LinkedInAPIError(401, "denied body", "https://li/x")
    assert "401" in str(e) and "denied body" in str(e) and e.status_code == 401
    e2 = LA.LinkedInAPIError(500, "t", "u", message="custom")
    assert str(e2) == "custom"


def test_log_api_error_arms(caplog):
    c = LA.LinkedInAPIClient(access_token="t")
    c._log_api_error("https://li/x", SimpleNamespace(status_code=200, text="ok"))
    c._log_api_error("https://li/x", SimpleNamespace(status_code=500, text="boom"))


def test_ugcpost_v2_payload_with_link():
    c = LA.LinkedInAPIClient(access_token="t")
    p = c._ugcpost_v2_payload("urn:li:person:1", "post text", link_url="https://x.gr")
    assert p["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] == "ARTICLE"


async def test_get_member_orgs_localized_name(monkeypatch):
    elements = {"elements": [{"organization": "urn:li:organization:1"}]}
    detail = {"localizedName": {"en_US": "Org One"}, "vanityName": "org-one"}

    class Resp:
        def __init__(self, payload, status=200):
            self.status_code = status
            self.text = "{}"
            self._p = payload

        def json(self):
            return self._p

    class Client:
        calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            self.calls.append(url)
            if "organizationAcls" in url:
                return Resp(elements)
            return Resp(detail)

    monkeypatch.setattr(LA.httpx, "AsyncClient", lambda **kw: Client())
    c = LA.LinkedInAPIClient(access_token="t")
    out = await c.get_member_organizations()
    assert len(out) == 1 and out[0].name == "Org One" and out[0].vanity_name == "org-one"


async def test_get_member_orgs_dict_name(monkeypatch):
    elements = {"elements": [{"organization": "urn:li:organization:2"}]}
    detail = {"name": {"localized": {"el_GR": "Org Two"}}}

    class Resp:
        status_code = 200
        text = "{}"

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            return Resp(elements if "organizationAcls" in url else detail)

    monkeypatch.setattr(LA.httpx, "AsyncClient", lambda **kw: Client())
    c = LA.LinkedInAPIClient(access_token="t")
    out = await c.get_member_organizations()
    assert out[0].name == "Org Two"
