"""Route-level tests for app/api/leads.py — list, manual create, and the
unauthenticated public capture endpoint (honeypot/validation/config/teams)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.leads as LD
from app.models.lead import LeadCompanySize, LeadInterest, LeadSource


class _Res:
    def __init__(self, all_=None):
        self._all = all_ or []

    def scalars(self):
        return self

    def all(self):
        return self._all


class _DB:
    def __init__(self, results=None):
        self.results = list(results or [])

    async def execute(self, stmt):
        return self.results.pop(0) if self.results else _Res([])


def _lead_row(**kw):
    base = dict(id=uuid.uuid4(), team_id=uuid.uuid4(), source=LeadSource.website,
                social_account_id=None, thread_id=None, name="N",
                email="n@x.co", company_size=LeadCompanySize.solo,
                interest=LeadInterest.cloud, notes=None, meta_data={},
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_list_leads_returns_validated_rows():
    rows = [_lead_row(), _lead_row()]
    db = _DB([_Res(rows)])
    out = await LD.list_leads(team_id=uuid.uuid4(), limit=50, offset=0,
                              source=LeadSource.website, db=db,
                              _user=SimpleNamespace())
    assert len(out) == 2 and out[0].source == LeadSource.website


@pytest.mark.asyncio
async def test_create_lead_manual_success(monkeypatch):
    created = _lead_row()
    monkeypatch.setattr(LD, "create_lead", AsyncMock(return_value=created))
    body = LD.LeadCreateRequest(source=LeadSource.website, name="N",
                                email="n@x.co", company_size="solo",
                                interest="cloud")
    out = await LD.create_lead_manual(body=body, team_id=uuid.uuid4(),
                                      db=_DB(), _user=SimpleNamespace())
    assert out.email == "n@x.co"


@pytest.mark.asyncio
async def test_create_lead_manual_bad_enums_400():
    body = LD.LeadCreateRequest(source=LeadSource.website, name="N",
                                email="n@x.co", company_size="hugeco")
    with pytest.raises(HTTPException) as e:
        await LD.create_lead_manual(body=body, team_id=uuid.uuid4(),
                                    db=_DB(), _user=SimpleNamespace())
    assert e.value.status_code == 400 and "company_size" in e.value.detail

    body = LD.LeadCreateRequest(source=LeadSource.website, name="N",
                                email="n@x.co", interest="everything")
    with pytest.raises(HTTPException) as e:
        await LD.create_lead_manual(body=body, team_id=uuid.uuid4(),
                                    db=_DB(), _user=SimpleNamespace())
    assert e.value.status_code == 400 and "interest" in e.value.detail


# ── public capture ──────────────────────────────────────────────────


def _settings(**kw):
    base = {"CLOUDLESS_WEB_ANALYTICS_TEAM_ID": str(uuid.uuid4()),
            "PLAYBOOK_URL": "https://x/playbook.pdf"}
    base.update(kw)
    return SimpleNamespace(**base)


def _req():
    from starlette.requests import Request

    return Request({
        "type": "http", "method": "POST", "path": "/api/v1/leads/public",
        "headers": [], "client": ("1.1.1.1", 1234), "server": ("t", 443),
        "scheme": "https",
    })


@pytest.fixture(autouse=True)
def _unwrap_rate_limiter(monkeypatch):
    """Call the view directly — SlowAPI's limiter is an infra boundary."""
    fn = LD.create_lead_public
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    monkeypatch.setattr(LD, "create_lead_public", fn)


@pytest.fixture
def pub_env(monkeypatch):
    lead = _lead_row()
    monkeypatch.setattr(LD, "create_lead", AsyncMock(return_value=lead))
    monkeypatch.setattr(LD, "deliver_playbook", AsyncMock(return_value="email"))
    monkeypatch.setattr(LD, "playbook_url", lambda s=None: "https://x/pb.pdf")
    return lead


@pytest.mark.asyncio
async def test_public_honeypot_silent_accept(monkeypatch, pub_env):
    monkeypatch.setattr(LD, "get_settings", lambda: _settings())
    body = LD.PublicLeadRequest(email="bot@x.co", website="http://spam")
    out = await LD.create_lead_public(body=body, request=_req(), db=_DB())
    assert out["ok"] is True and out["playbook_delivery"] == "email"
    LD.create_lead.assert_not_awaited()  # honeypot never writes


@pytest.mark.asyncio
async def test_public_bad_email_400(monkeypatch, pub_env):
    monkeypatch.setattr(LD, "get_settings", lambda: _settings())
    with pytest.raises(HTTPException) as e:
        await LD.create_lead_public(
            body=LD.PublicLeadRequest(email="not-an-email"),
            request=_req(), db=_DB())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_public_explicit_no_consent_400(monkeypatch, pub_env):
    monkeypatch.setattr(LD, "get_settings", lambda: _settings())
    with pytest.raises(HTTPException) as e:
        await LD.create_lead_public(
            body=LD.PublicLeadRequest(email="a@b.co", consent=False),
            request=_req(), db=_DB())
    assert e.value.status_code == 400 and "Consent" in e.value.detail


@pytest.mark.asyncio
async def test_public_no_team_configured_503(monkeypatch, pub_env):
    monkeypatch.setattr(LD, "get_settings",
                        lambda: _settings(CLOUDLESS_WEB_ANALYTICS_TEAM_ID=""))
    with pytest.raises(HTTPException) as e:
        await LD.create_lead_public(
            body=LD.PublicLeadRequest(email="a@b.co"),
            request=_req(), db=_DB())
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_public_new_lead_sends_playbook(monkeypatch, pub_env):
    monkeypatch.setattr(LD, "get_settings", lambda: _settings())
    LD.deliver_playbook.return_value = "email"
    db = _DB([_Res([])])  # no prior website lead rows
    out = await LD.create_lead_public(
        body=LD.PublicLeadRequest(email="New@B.co", name="New Person",
                                  site="cloudless.gr", form="playbook"),
        request=_req(), db=db)
    assert out["playbook_delivery"] == "email"
    kwargs = LD.create_lead.await_args.kwargs
    assert kwargs["email"] == "new@b.co"           # lowercased
    assert kwargs["source"] == LeadSource.website
    assert "playbook" in kwargs["notes"]
    assert "via cloudless.gr" in kwargs["notes"]


@pytest.mark.asyncio
async def test_public_already_sent_skips_resend(monkeypatch, pub_env):
    monkeypatch.setattr(LD, "get_settings", lambda: _settings())
    LD.deliver_playbook.return_value = "already_sent"
    prior_meta = [{"playbook_email_queued_at": "2026-01-01T00:00:00"}]
    db = _DB([_Res(prior_meta)])
    out = await LD.create_lead_public(
        body=LD.PublicLeadRequest(email="a@b.co"), request=_req(), db=db)
    assert out["playbook_delivery"] == "already_sent"
    # already_sent=True passed through to deliver_playbook
    assert LD.deliver_playbook.await_args.kwargs["already_sent"] is True


def test_iso_or_none():
    assert LD._iso_or_none(None) is None
    assert LD._iso_or_none("not-a-date") is None
    assert LD._iso_or_none("2026-01-01T00:00:00Z").startswith("2026-01-01")
