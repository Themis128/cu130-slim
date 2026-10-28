"""Coverage tails: notebooks, log_intel, newsletter_subs, datalake_insights,
auto_gates, nurture — the last uncovered task-module lines."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _db(scalars_all=None, first=None, all_rows=None):
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = scalars_all or []
    res.scalars.return_value.first.return_value = first
    res.first.return_value = first
    res.all.return_value = all_rows or []
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


# ---------- notebooks ----------


@pytest.mark.asyncio
async def test_weekly_analytics_sends(monkeypatch):
    from app.worker.tasks import notebooks as M

    sent = []
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock(side_effect=lambda m: sent.append(m)))
    db = _db(all_rows=[("published", 3)], first=(4.5, 2))
    monkeypatch.setattr("app.db.session.async_session_maker", lambda: db)
    out = await M._weekly_analytics()
    assert out["audits"]["count"] == 2 and sent


def test_weekly_analytics_disabled(monkeypatch):
    from app.worker.tasks import notebooks as M

    monkeypatch.setattr("app.core.config.get_settings", lambda: MagicMock(AGENCY_ENABLED=False))
    assert M.run_weekly_analytics() is None


def test_weekly_analytics_error(monkeypatch):
    from app.worker.tasks import notebooks as M

    monkeypatch.setattr("app.core.config.get_settings", lambda: MagicMock(AGENCY_ENABLED=True))
    monkeypatch.setattr(M, "run_async", MagicMock(side_effect=RuntimeError("x")))
    assert M.run_weekly_analytics() is None


# ---------- log_intel ----------


def test_log_intel_digest_with_lines(monkeypatch):
    from app.worker.tasks import log_intel as M

    posted = []
    monkeypatch.setattr(M, "triage_logs", AsyncMock(return_value={"lines": 5, "summary": "s"}))
    monkeypatch.setattr(M, "post_alert_to_slack", AsyncMock(side_effect=lambda m: posted.append(m)))
    out = M.digest()
    assert out["lines"] == 5 and posted


def test_log_intel_digest_empty(monkeypatch):
    from app.worker.tasks import log_intel as M

    monkeypatch.setattr(M, "triage_logs", AsyncMock(return_value={"lines": 0, "summary": ""}))
    posted = []
    monkeypatch.setattr(M, "post_alert_to_slack", AsyncMock(side_effect=lambda m: posted.append(m)))
    assert M.digest()["lines"] == 0 and not posted


# ---------- newsletter_subs ----------


@pytest.mark.asyncio
async def test_newsletter_run(monkeypatch):
    from app.worker.tasks import newsletter_subs as M

    monkeypatch.setattr("app.core.config.get_settings", lambda: MagicMock(LINKEDIN_NEWSLETTER_SERIES_URN="urn:x"))
    monkeypatch.setattr("app.services.newsletter_subscribers.sync_newsletter_subscribers", AsyncMock(return_value={"synced": 4}))
    assert (await M._run())["synced"] == 4


def test_newsletter_wrapper_error(monkeypatch):
    from app.worker.tasks import newsletter_subs as M

    monkeypatch.setattr(M, "run_async", MagicMock(side_effect=RuntimeError("x")))
    assert M.run_newsletter_subscriber_sync() is None


# ---------- datalake_insights ----------


def test_datalake_sweep(monkeypatch):
    import app.services.datalake_insights as rdi
    from app.worker.tasks import datalake_insights as M

    monkeypatch.setattr(rdi, "apply_signals", AsyncMock(return_value={"actions": [1, 2]}))
    monkeypatch.setattr(rdi, "render_slack_lines", MagicMock(return_value=["hdr", "line"]))
    posted = []
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock(side_effect=lambda m: posted.append(m)))
    db = _db(scalars_all=[uuid.uuid4()])
    monkeypatch.setattr(M, "task_session", lambda: db)
    out = M.datalake_insights_sweep()
    assert out["teams"] == 1 and posted


# ---------- auto_gates ----------


@pytest.mark.asyncio
async def test_survey_autosend_sends(monkeypatch):
    from app.worker.tasks import auto_gates as M

    camp = MagicMock(meta_data={"auto_send": True}, id=1)
    pend = MagicMock()
    res1 = MagicMock()
    res1.scalars.return_value.all.return_value = [camp]
    res2 = MagicMock()
    res2.first.return_value = pend
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[res1, res2])
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(M, "task_session", lambda: db)
    send = AsyncMock()
    import app.services.email_campaigns as ec

    monkeypatch.setattr(ec, "send_campaign", send)
    out = await M._survey_autosend()
    assert out["sent"] == 1
    send.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_survey_autosend_skips_unflagged(monkeypatch):
    from app.worker.tasks import auto_gates as M

    camp = MagicMock(meta_data={}, id=1)
    res1 = MagicMock()
    res1.scalars.return_value.all.return_value = [camp]
    db = MagicMock()
    db.execute = AsyncMock(return_value=res1)
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(M, "task_session", lambda: db)
    assert (await M._survey_autosend())["sent"] == 0


@pytest.mark.asyncio
async def test_post_autoapprove_off(monkeypatch):
    from app.worker.tasks import auto_gates as M

    monkeypatch.delenv("AUTO_APPROVE_POSTS", raising=False)
    assert (await M._post_autoapprove())["off"] is True


@pytest.mark.asyncio
async def test_post_autoapprove_approves(monkeypatch):
    from app.worker.tasks import auto_gates as M

    monkeypatch.setenv("AUTO_APPROVE_POSTS", "true")
    post = MagicMock(id=5, user_id=7)
    res = MagicMock()
    res.scalars.return_value.all.return_value = [post]
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(M, "task_session", lambda: db)
    out = await M._post_autoapprove()
    assert out["approved"] == 1


def test_auto_gates_wrappers(monkeypatch):
    from app.worker.tasks import auto_gates as M

    monkeypatch.setattr(M, "run_async", lambda c: (c.close(), {"x": 1})[1])
    assert M.survey_autosend() == {"x": 1}
    assert M.post_autoapprove() == {"x": 1}


# ---------- nurture ----------


@pytest.mark.asyncio
async def test_nurture_no_team(monkeypatch):
    from app.worker.tasks import nurture as M

    monkeypatch.setattr("app.core.config.get_settings", lambda: MagicMock(CLOUDLESS_WEB_ANALYTICS_TEAM_ID=""))
    assert (await M._run())["skipped"].startswith("no public-lead")


@pytest.mark.asyncio
async def test_nurture_sweeps(monkeypatch):
    from app.worker.tasks import nurture as M

    tid = str(uuid.uuid4())
    monkeypatch.setattr("app.core.config.get_settings", lambda: MagicMock(CLOUDLESS_WEB_ANALYTICS_TEAM_ID=tid))
    db = _db()
    monkeypatch.setattr("app.db.session.async_session_maker", lambda: db)
    monkeypatch.setattr("app.services.nurture_sequence.nurture_sweep", AsyncMock(return_value={"advanced": 2}))
    assert (await M._run())["advanced"] == 2


def test_nurture_wrapper_error(monkeypatch):
    from app.worker.tasks import nurture as M

    monkeypatch.setattr(M, "run_async", MagicMock(side_effect=RuntimeError("x")))
    assert M.run_nurture_sweep() is None
