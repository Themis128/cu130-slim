"""Coverage for linkedin_messenger._capture_contact."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.linkedin_messenger as LM

pytestmark = pytest.mark.asyncio


class _AsyncCM:
    def __init__(self, v):
        self._v = v

    async def __aenter__(self):
        return self._v

    async def __aexit__(self, *a):
        return False


def _acct():
    return SimpleNamespace(team_id="t1", id="a1")


async def test_no_contact_returns_none():
    assert await LM._capture_contact(_acct(), "Bob", "hi there") is None


async def test_email_capture_full_path(monkeypatch):
    import app.db.session as dbs
    import app.models.lead as ml
    import app.services.espocrm_client as esp
    import app.services.leads as leads

    monkeypatch.setattr(ml, "LeadSource", SimpleNamespace(dm="dm"))
    made = []
    monkeypatch.setattr(dbs, "async_session_maker", lambda: _AsyncCM(SimpleNamespace()))
    monkeypatch.setattr(leads, "create_lead", AsyncMock(side_effect=lambda *a, **k: made.append(k)))
    monkeypatch.setattr(esp, "upsert_lead", AsyncMock())

    out = await LM._capture_contact(_acct(), "Jane Doe", "reach me at jane@x.com")
    assert "Jane" in out and "jane@x.com" in out
    assert made and made[0]["email"] == "jane@x.com"
    esp.upsert_lead.assert_awaited_once()


async def test_phone_only_capture(monkeypatch):
    import app.models.lead as ml
    import app.services.espocrm_client as esp
    monkeypatch.setattr(ml, "LeadSource", SimpleNamespace(dm="dm"))
    monkeypatch.setattr(esp, "upsert_lead", AsyncMock())
    out = await LM._capture_contact(_acct(), "Bob", "call +30 697 123 4567")
    assert "Bob" in out and "number" in out


async def test_espo_failure_nonfatal(monkeypatch):
    import app.db.session as dbs
    import app.models.lead as ml
    import app.services.espocrm_client as esp
    import app.services.leads as leads

    monkeypatch.setattr(ml, "LeadSource", SimpleNamespace(dm="dm"))
    monkeypatch.setattr(dbs, "async_session_maker", lambda: _AsyncCM(SimpleNamespace()))
    monkeypatch.setattr(leads, "create_lead", AsyncMock())
    monkeypatch.setattr(esp, "upsert_lead", AsyncMock(side_effect=Exception("down")))
    out = await LM._capture_contact(_acct(), "Ana", "ana@x.com")
    assert "ana@x.com" in out


async def test_outer_exception_returns_none(monkeypatch):
    import app.db.session as dbs
    import app.services.leads as leads

    monkeypatch.setattr(dbs, "async_session_maker", lambda: _AsyncCM(SimpleNamespace()))
    monkeypatch.setattr(leads, "create_lead", AsyncMock(side_effect=Exception("db down")))
    assert await LM._capture_contact(_acct(), "Bob", "b@x.com") is None
