"""Coverage for leads public endpoints — validation + audit/survey branches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.leads as L

pytestmark = pytest.mark.asyncio


def _audit_body(**kw):
    d = {"email": "a@b.com", "website": "", "url": "https://x.gr", "consent": True}
    d.update(kw)
    return SimpleNamespace(**d)


def _survey_body(**kw):
    d = {"email": "a@b.com", "website": "", "phone": "", "name": "T", "consent": True,
         "answers": {}, "company_size": None, "interest": None, "site": None,
         "form": None, "page_path": None, "locale": None, "platform": None,
         "name": None}
    d.update(kw)
    return SimpleNamespace(**d)


def _settings():
    return SimpleNamespace(CLOUDLESS_WEB_ANALYTICS_TEAM_ID="00000000-0000-0000-0000-000000000001")


async def test_audit_honeypot():
    out = await L.create_audit_public(_audit_body(website="spam"), SimpleNamespace())
    assert out == {"ok": True, "score": None, "issues": None}


async def test_audit_validation_branches(monkeypatch):
    monkeypatch.setattr(L, "get_settings", lambda: _settings())
    for body, code in ((_audit_body(email="bad"), 400), (_audit_body(consent=False), 400)):
        with pytest.raises(HTTPException) as e:
            await L.create_audit_public(body, SimpleNamespace())
        assert e.value.status_code == code
    monkeypatch.setattr(L, "get_settings", lambda: SimpleNamespace(CLOUDLESS_WEB_ANALYTICS_TEAM_ID=""))
    with pytest.raises(HTTPException) as e:
        await L.create_audit_public(_audit_body(), SimpleNamespace())
    assert e.value.status_code == 503


async def test_audit_url_errors(monkeypatch):
    import app.services.site_audit as sa
    import app.services.url_safety as us

    monkeypatch.setattr(L, "get_settings", lambda: _settings())
    monkeypatch.setattr(us, "validate_public_http_url", lambda u: (_ for _ in ()).throw(sa.UnsafeUrlError("bad url")))
    with pytest.raises(HTTPException) as e:
        await L.create_audit_public(_audit_body(), SimpleNamespace())
    assert e.value.status_code == 400

    monkeypatch.setattr(us, "validate_public_http_url", lambda u: u)
    monkeypatch.setattr(sa, "run_site_audit", AsyncMock(side_effect=sa.UnsafeUrlError("x")))
    with pytest.raises(HTTPException) as e:
        await L.create_audit_public(_audit_body(), SimpleNamespace())
    assert e.value.status_code == 400

    monkeypatch.setattr(sa, "run_site_audit", AsyncMock(side_effect=RuntimeError("unreachable")))
    with pytest.raises(HTTPException) as e:
        await L.create_audit_public(_audit_body(), SimpleNamespace())
    assert e.value.status_code == 422


async def test_survey_honeypot_and_validation(monkeypatch):
    monkeypatch.setattr(L, "get_settings", lambda: _settings())
    monkeypatch.setattr(L, "playbook_url", lambda s: None)
    out = await L.create_survey_public(_survey_body(website="spam"), SimpleNamespace())
    assert out["survey_delivery"] == "none"
    for body, code in ((_survey_body(email="bad"), 400), (_survey_body(phone="abc"), 400), (_survey_body(phone="+30697123456", consent=False), 400)):
        with pytest.raises(HTTPException) as e:
            await L.create_survey_public(body, SimpleNamespace())
        assert e.value.status_code == code


async def test_survey_full_meta_and_espo_fail(monkeypatch):
    import app.services.espocrm_client as esp
    monkeypatch.setattr(esp, "upsert_lead", AsyncMock(side_effect=Exception("down")))
    monkeypatch.setattr(L, "get_settings", lambda: _settings())
    monkeypatch.setattr(L, "playbook_url", lambda s: "https://pb")
    monkeypatch.setattr(L, "create_lead", AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())
    body = _survey_body(phone="+30697123456", answers={"q": "a"}, company_size="1-10",
                        interest="audit", site="example", form="f1", page_path="/x",
                        locale="en", platform="linkedin")
    out = await L.create_survey_public(body, SimpleNamespace(), db)
    assert out["survey_delivery"] == "email"
