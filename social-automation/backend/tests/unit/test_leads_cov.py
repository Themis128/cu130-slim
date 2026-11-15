"""Coverage for leads public endpoints — validation + audit/survey branches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.leads as L

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _unwrap_rate_limiter(monkeypatch):
    """Call the views directly — SlowAPI's limiter is an infra boundary and
    requires a real starlette Request these tests don't construct."""
    for name in ("create_audit_public", "create_survey_public"):
        fn = getattr(L, name)
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__
        monkeypatch.setattr(L, name, fn)


def _audit_body(**kw):
    d = {"email": "a@b.com", "website": "", "url": "https://x.gr", "consent": True, "name": None}
    d.update(kw)
    return SimpleNamespace(**d)


def _survey_body(**kw):
    d = {
        "email": "a@b.com",
        "website": "",
        "phone": "",
        "consent": True,
        "answers": {},
        "company_size": None,
        "interest": None,
        "site": None,
        "form": None,
        "page_path": None,
        "locale": None,
        "platform": None,
        "name": None,
    }
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
    body = _survey_body(
        phone="+30697123456",
        answers={"q": "a"},
        company_size="1-10",
        interest="audit",
        site="example",
        form="f1",
        page_path="/x",
        locale="en",
        platform="linkedin",
    )
    out = await L.create_survey_public(body, SimpleNamespace(), db)
    assert out["survey_delivery"] == "email"


# ── residual gaps ───────────────────────────────────────────────────────


def test_public_lead_meta_full_attribution():
    body = L.PublicLeadRequest(
        email="a@b.com",
        site="cloudless",
        form="playbook",
        page_path="/en/audit",
        locale="en-GR",
        consent=True,
        consent_at="2026-11-20T10:00:00Z",
    )
    meta = L.public_lead_meta(body)
    assert meta["site"] == "cloudless" and meta["form_field"] if "form_field" in meta else meta["form"] == "playbook"
    assert meta["path"] == "/en/audit" and meta["locale"] == "en-GR"
    assert meta["consent"] is True and meta["consent_at"].startswith("2026-11-20")


async def test_list_leads_without_source_filter():
    from app.models.lead import LeadSource  # noqa

    class _Res:
        def scalars(self):
            return self

        def all(self):
            return []

    class _DB:
        async def execute(self, stmt):
            return _Res()

    out = await L.list_leads(team_id=__import__("uuid").uuid4(), limit=10, offset=0, source=None, db=_DB(), _user=SimpleNamespace())
    assert out == []


async def test_audit_success_creates_lead_and_row(monkeypatch):
    import uuid as _uuid

    import app.services.site_audit as sa
    import app.services.url_safety as us

    monkeypatch.setattr(L, "get_settings", lambda: _settings())
    monkeypatch.setattr(us, "validate_public_http_url", lambda u: u)
    monkeypatch.setattr(
        sa,
        "run_site_audit",
        AsyncMock(
            return_value={
                "score": 82,
                "url": "https://x.gr",
                "findings": [{"k": "v"}],
                "recommended_services": ["seo"],
                "action_plan": {"step": 1},
            }
        ),
    )
    lead = SimpleNamespace(id=_uuid.uuid4())
    monkeypatch.setattr(L, "create_lead", AsyncMock(return_value=lead))

    class _DB:
        def __init__(self):
            self.rows = []

        def add(self, obj):
            self.rows.append(obj)

        async def flush(self):
            pass

        async def commit(self):
            pass

    db = _DB()
    out = await L.create_audit_public(_audit_body(), SimpleNamespace(), db)
    assert out["ok"] is True and out["score"] == 82
    assert len(db.rows) == 1  # AgencyAudit row


async def test_survey_minimal_body_and_no_team(monkeypatch):
    import app.services.espocrm_client as esp

    monkeypatch.setattr(esp, "upsert_lead", AsyncMock())
    monkeypatch.setattr(L, "get_settings", lambda: _settings())
    monkeypatch.setattr(L, "playbook_url", lambda s: None)
    monkeypatch.setattr(L, "create_lead", AsyncMock())

    class _DB:
        async def commit(self):
            pass

    # all optional fields absent -> every `if` False arc
    out = await L.create_survey_public(_survey_body(), SimpleNamespace(), _DB())
    assert out["ok"] is True

    # no team configured -> 503
    monkeypatch.setattr(L, "get_settings", lambda: SimpleNamespace(CLOUDLESS_WEB_ANALYTICS_TEAM_ID=""))
    with pytest.raises(HTTPException) as e:
        await L.create_survey_public(_survey_body(), SimpleNamespace(), _DB())
    assert e.value.status_code == 503
