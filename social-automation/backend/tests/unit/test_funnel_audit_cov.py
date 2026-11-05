"""Coverage for funnel_audit — subprocess audit + slack post arms."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.funnel_audit as FA

pytestmark = pytest.mark.asyncio


def test_run_audit_ok(monkeypatch):
    monkeypatch.setattr(FA.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="stage table\nok", stderr=""))
    monkeypatch.setattr(FA, "_run_audit", lambda: (0, "line1\nsqlalchemy noise\nline2"))
    monkeypatch.setattr(FA, "run_async", lambda c: None)
    out = FA.run_funnel_audit()
    assert out["status"] == "ok" and out["rc"] == 0


def test_run_audit_broken(monkeypatch):
    monkeypatch.setattr(FA, "_run_audit", lambda: (1, "broken stage output"))
    monkeypatch.setattr(FA, "run_async", lambda c: None)
    out = FA.run_funnel_audit()
    assert out["status"] == "broken-stage" and out["rc"] == 1


def test_run_audit_subprocess_shape(monkeypatch):
    called = {}
    def fake_run(args, **kw):
        called.update(kw)
        return SimpleNamespace(returncode=0, stdout="o", stderr="e")
    monkeypatch.setattr(FA.subprocess, "run", fake_run)
    rc, out = FA._run_audit()
    assert rc == 0 and "o" in out and "e" in out and called["timeout"] == 180


async def test_post_arms(monkeypatch):
    import app.services.slack_notifications as SN
    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock())
    monkeypatch.setattr(SN, "post_digest_text_to_slack", AsyncMock())
    await FA._post("alert", alert=True)
    SN.post_alert_to_slack.assert_awaited_once()
    await FA._post("digest", alert=False)
    SN.post_digest_text_to_slack.assert_awaited_once()
    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock(side_effect=Exception("down")))
    await FA._post("failsafe", alert=True)  # exception swallowed
