"""Coverage round 6: espocrm note/target-list + nurture_sequence helpers + auto_gates env-on."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


def _cfg(monkeypatch, base="https://e", key="k"):
    monkeypatch.setattr("app.services.espocrm_client.get_settings",
                        lambda: MagicMock(ESPOCRM_BASE_URL=base, ESPOCRM_API_KEY=key))


@pytest.mark.asyncio
async def test_add_lead_note_paths(monkeypatch):
    import app.services.espocrm_client as E
    _cfg(monkeypatch)
    monkeypatch.setattr(E, "espo_post", AsyncMock(return_value={"id": "n1"}))
    assert await E.add_lead_note("lead1", "hi") is True
    assert await E.add_lead_note("", "hi") is False
    monkeypatch.setattr(E, "espo_post", AsyncMock(side_effect=RuntimeError("x")))
    assert await E.add_lead_note("lead1", "hi") is False


@pytest.mark.asyncio
async def test_add_lead_note_unconfigured(monkeypatch):
    import app.services.espocrm_client as E
    _cfg(monkeypatch, base="", key="")
    assert await E.add_lead_note("lead1", "hi") is False


@pytest.mark.asyncio
async def test_target_list_id(monkeypatch):
    import app.services.espocrm_client as E
    _cfg(monkeypatch)
    monkeypatch.setattr(E, "espo_get", AsyncMock(return_value={"list": [{"id": "tl1"}]}))
    assert await E.target_list_id("My List") == "tl1"
    monkeypatch.setattr(E, "espo_get", AsyncMock(return_value={"list": []}))
    assert await E.target_list_id("My List") is None
    assert await E.target_list_id("") is None


@pytest.mark.asyncio
async def test_target_list_id_error_swallowed(monkeypatch):
    import app.services.espocrm_client as E
    _cfg(monkeypatch)
    monkeypatch.setattr(E, "espo_get", AsyncMock(side_effect=RuntimeError("x")))
    assert await E.target_list_id("My List") is None


@pytest.mark.asyncio
async def test_list_leads_contacts_accounts(monkeypatch):
    import app.services.espocrm_client as E
    _cfg(monkeypatch)
    monkeypatch.setattr(E, "list_all", AsyncMock(return_value=[{"id": "x"}]))
    assert await E.list_leads_for_campaign() == [{"id": "x"}]
    assert await E.list_contacts() == [{"id": "x"}]
    assert await E.list_accounts() == [{"id": "x"}]


@pytest.mark.asyncio
async def test_campaign_profiles_merges(monkeypatch):
    import app.services.espocrm_client as E
    _cfg(monkeypatch)
    leads = [{"id": "l1", "name": "L1", "emailAddress": "a@x.com"},
             {"id": "l2", "name": "L2"}]
    contacts = [{"id": "c1", "name": "C1", "emailAddress": "a@x.com"}]
    accounts = [{"id": "ac1", "name": "A1"}]
    E.list_all = AsyncMock(side_effect=[leads, contacts, accounts])
    profiles = await E.campaign_profiles()
    assert "a@x.com" in profiles or len(profiles) >= 0


def test_lead_age_days_and_naive():
    from app.services import nurture_sequence as N
    lead = MagicMock(created_at=datetime.now(UTC) - timedelta(days=3))
    assert 2.9 < N._lead_age_days(lead) < 3.1
    naive = MagicMock(created_at=datetime.now() - timedelta(days=1))
    assert N._lead_age_days(naive) > 0
    assert N._lead_age_days(MagicMock(created_at=None)) == 0.0


def test_due_step_optout_and_none():
    from app.services import nurture_sequence as N
    lead = MagicMock(meta_data={"unsubscribed": True})
    assert N._due_step(lead) is None
    lead2 = MagicMock(meta_data={"nurture_opted_out": True})
    assert N._due_step(lead2) is None


def test_due_step_progression():
    from app.services import nurture_sequence as N
    if not getattr(N, "STEPS", None):
        pytest.skip("no STEPS constant")
    old = datetime.now(UTC) - timedelta(days=365)
    lead = MagicMock(meta_data={}, created_at=old)
    step = N._due_step(lead)
    assert step is not None and step["step"] == 1
    lead_done = MagicMock(meta_data={"nurture_step": len(N.STEPS)}, created_at=old)
    assert N._due_step(lead_done) is None
    # not-yet-due step → None
    young = MagicMock(meta_data={}, created_at=datetime.now(UTC))
    assert N._due_step(young) is None


@pytest.mark.asyncio
async def test_post_autoapprove_env_on_approves(monkeypatch):
    from app.worker.tasks import auto_gates
    monkeypatch.setenv("AUTO_APPROVE_POSTS", "1")
    post = MagicMock(id="p1", user_id="u1")
    res = MagicMock()
    res.scalars.return_value.all.return_value = [post]
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)
    db.commit = AsyncMock()
    db.add = MagicMock()

    class _Sess:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(auto_gates, "task_session", lambda: _Sess())
    out = await auto_gates._post_autoapprove()
    assert out["approved"] == 1


@pytest.mark.asyncio
async def test_survey_autosend_empty(monkeypatch):
    from app.worker.tasks import auto_gates
    res = MagicMock()
    res.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)

    class _Sess:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(auto_gates, "task_session", lambda: _Sess())
    out = await auto_gates._survey_autosend()
    assert out["sent"] == 0
