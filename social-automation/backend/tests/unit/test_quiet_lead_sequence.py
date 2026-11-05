"""Coverage: quiet-lead lane — enrollment age, due steps, sweep routing."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import nurture_sequence as M


def _lead(meta=None, email="a@x.co", name="Ana Maria", created_days=30):
    lead = MagicMock()
    lead.meta_data = meta or {}
    lead.email = email
    lead.name = name
    lead.created_at = datetime.now(UTC) - timedelta(days=created_days)
    return lead


def _db(leads):
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = leads
    db.execute = AsyncMock(return_value=res)
    db.commit = AsyncMock()
    return db


def _enrolled(days_ago, **extra):
    return {
        "quiet_lead": True,
        "quiet_lead_at": (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
        **extra,
    }


def test_quiet_age_measured_from_enrollment_not_created():
    # A 30-day-old lead enrolled 2h ago must be ~0 days into the sequence.
    lead = _lead(_enrolled(days_ago=0.1))
    assert M._quiet_age_days(lead) < 0.5
    assert M._lead_age_days(lead) > 29


def test_due_quiet_step_day1():
    lead = _lead(_enrolled(days_ago=1.2))
    step = M._due_quiet_step(lead)
    assert step["step"] == 1 and step["key"] == "nudge"


def test_quiet_steps_are_sequential():
    # 8 days in but only touch 1 sent → touch 2 (day 3) is next, not 3.
    lead = _lead(_enrolled(days_ago=8, quiet_step=1))
    step = M._due_quiet_step(lead)
    assert step["step"] == 2 and step["key"] == "pain"
    lead.meta_data["quiet_step"] = 2
    step = M._due_quiet_step(lead)
    assert step["step"] == 3 and step["key"] == "clean_break"
    lead.meta_data["quiet_step"] = 3
    assert M._due_quiet_step(lead) is None


def test_quiet_opt_out_and_bad_timestamp():
    assert M._due_quiet_step(_lead(_enrolled(5, unsubscribed=True))) is None
    assert M._due_quiet_step(_lead(_enrolled(5, nurture_opted_out=True))) is None
    assert M._due_quiet_step(_lead({"quiet_lead": True, "quiet_lead_at": "junk"})) is None
    assert M._due_quiet_step(_lead({"quiet_lead": True})) is None


@pytest.mark.asyncio
async def test_sweep_routes_quiet_lane_and_stamps(monkeypatch):
    lead = _lead(_enrolled(days_ago=2), name="Nikos Papas")
    send = AsyncMock()
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", send)
    out = await M.nurture_sweep(_db([lead]), MagicMock())
    assert out == {"scanned": 1, "sent": 1, "skipped": 0, "rolled": 0}
    assert lead.meta_data["quiet_step"] == 1
    assert "nurture_step" not in lead.meta_data
    body = send.call_args.kwargs["text_body"]
    assert "Nikos" in body


@pytest.mark.asyncio
async def test_sweep_quiet_company_defaults_to_your_team(monkeypatch):
    lead = _lead(_enrolled(days_ago=4, quiet_step=1))
    send = AsyncMock()
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", send)
    out = await M.nurture_sweep(_db([lead]), MagicMock())
    assert out["sent"] == 1
    assert "your team" in send.call_args.kwargs["text_body"]


@pytest.mark.asyncio
async def test_sweep_quiet_uses_company_placeholder(monkeypatch):
    lead = _lead(_enrolled(days_ago=4, quiet_step=1, company="Acme SA"))
    send = AsyncMock()
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", send)
    out = await M.nurture_sweep(_db([lead]), MagicMock())
    assert out["sent"] == 1
    assert "Acme SA" in send.call_args.kwargs["text_body"]


@pytest.mark.asyncio
async def test_sweep_rolls_finished_ladder_into_quiet_lane(monkeypatch):
    # A lead that consumed all nurture steps without booking auto-enrolls
    # into the quiet lane (touch 1 due 24h after rollover).
    lead = _lead({"form": "newsletter", "nurture_step": len(M.STEPS)})
    send = AsyncMock()
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", send)
    out = await M.nurture_sweep(_db([lead]), MagicMock())
    assert out["rolled"] == 1
    assert lead.meta_data["quiet_lead"] is True
    assert "quiet_lead_at" in lead.meta_data
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_no_rollover_when_opted_out_or_mid_ladder(monkeypatch):
    opted = _lead({"form": "newsletter", "nurture_step": 99, "unsubscribed": True})
    mid = _lead({"form": "newsletter", "nurture_step": 1})
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", AsyncMock())
    out = await M.nurture_sweep(_db([opted, mid]), MagicMock())
    assert out["rolled"] == 0
    assert "quiet_lead" not in (opted.meta_data or {})
    assert "quiet_lead" not in (mid.meta_data or {})


@pytest.mark.asyncio
async def test_sweep_audit_request_leads_auto_enter_quiet_lane(monkeypatch):
    # form=audit-request (the /audit booking page) → quiet lane from
    # creation; at age 2d touch 1 (day-1 nudge) sends immediately.
    lead = _lead({"form": "audit-request"}, created_days=2)
    send = AsyncMock()
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", send)
    out = await M.nurture_sweep(_db([lead]), MagicMock())
    assert out["rolled"] == 1 and out["sent"] == 1
    assert lead.meta_data["quiet_step"] == 1


@pytest.mark.asyncio
async def test_sweep_audit_request_opted_out_not_enrolled(monkeypatch):
    lead = _lead({"form": "audit-request", "quiet_opted_out": True}, created_days=2)
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", AsyncMock())
    out = await M.nurture_sweep(_db([lead]), MagicMock())
    assert out["rolled"] == 0
    assert "quiet_lead" not in (lead.meta_data or {})


@pytest.mark.asyncio
async def test_sweep_quiet_lane_supersedes_nurture_forms(monkeypatch):
    # A newsletter-form lead that is also quiet-enrolled takes the quiet lane.
    lead = _lead(_enrolled(days_ago=10, form="newsletter"))
    send = AsyncMock()
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", send)
    await M.nurture_sweep(_db([lead]), MagicMock())
    assert lead.meta_data["quiet_step"] == 1
    assert "nurture_step" not in lead.meta_data


@pytest.mark.asyncio
async def test_shadow_call_skipped_when_env_unset(monkeypatch):
    settings = MagicMock()
    settings.LEAD_SCHEDULER_URL = ""
    settings.LEAD_SCHEDULER_TOKEN = ""
    monkeypatch.setattr(M, "get_settings", lambda: settings)
    with patch("httpx.AsyncClient") as client:
        await M._cf_shadow_call("LEAD_SCHEDULER_URL", "LEAD_SCHEDULER_TOKEN", "/x", {})
    client.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_call_posts_when_env_set(monkeypatch):
    settings = MagicMock()
    settings.CF_U = "https://w.example.com"
    settings.CF_T = "tok"
    monkeypatch.setattr(M, "get_settings", lambda: settings)
    post = AsyncMock()
    client = AsyncMock()
    client.post = post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **kw: client)
    await M._cf_shadow_call("CF_U", "CF_T", "/do/x", {"a": 1})
    post.assert_awaited_once_with(
        "https://w.example.com/do/x",
        json={"a": 1},
        headers={"Authorization": "Bearer tok"},
    )


@pytest.mark.asyncio
async def test_sweep_enroll_fires_scheduler_shadow(monkeypatch):
    lead = _lead({"form": "audit-request"}, created_days=2)
    shadow = AsyncMock()
    monkeypatch.setattr(M, "_cf_shadow_call", shadow)
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", AsyncMock())
    await M.nurture_sweep(_db([lead]), MagicMock())
    paths = [c.args[2] for c in shadow.await_args_list]
    assert any(p.endswith("/enroll") for p in paths)


@pytest.mark.asyncio
async def test_sweep_send_emits_funnel_event(monkeypatch):
    lead = _lead(_enrolled(days_ago=2))
    shadow = AsyncMock()
    monkeypatch.setattr(M, "_cf_shadow_call", shadow)
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", AsyncMock())
    await M.nurture_sweep(_db([lead]), MagicMock())
    event_calls = [c for c in shadow.await_args_list if c.args[2] == "/event"]
    assert event_calls and event_calls[0].args[3]["type"] == "quiet.sent"
