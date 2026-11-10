"""workforce-audit Celery task wrapper — runs the roster audit + Slack push."""

from __future__ import annotations

import asyncio


def test_run_workforce_audit_wrapper(monkeypatch):
    import app.worker.tasks.workforce_audit as W
    import scripts.workforce_audit as WA

    async def fake_audit():
        return {
            "workers": [{"status": "ok"}, {"status": "dark"}, {"status": "escalation"}, {"status": "ungoverned"}, {"status": "idle"}, {"status": "unverified"}],
            "autonomy_proposals": [{"worker": "x"}],
        }

    pushed = []

    async def fake_push(report):
        pushed.append(report)

    monkeypatch.setattr(WA, "audit", fake_audit)
    monkeypatch.setattr(WA, "push_slack", fake_push)
    monkeypatch.setattr(W, "run_async", lambda coro: asyncio.run(coro))

    out = W.run_workforce_audit()
    assert out == {"workers": 6, "ok": 1, "idle": 1, "dark": 1, "escalation": 1, "ungoverned": 1, "unverified": 1, "attention": 3, "proposals": 1}
    assert len(pushed) == 1


def test_run_workforce_audit_notify_off_and_slack_failure(monkeypatch):
    import app.worker.tasks.workforce_audit as W
    import scripts.workforce_audit as WA

    async def fake_audit():
        return {"workers": [], "autonomy_proposals": []}

    async def boom(report):
        raise RuntimeError("slack down")

    monkeypatch.setattr(WA, "audit", fake_audit)
    monkeypatch.setattr(WA, "push_slack", boom)
    monkeypatch.setattr(W, "run_async", lambda coro: asyncio.run(coro))

    out = W.run_workforce_audit(notify=False)
    assert out["workers"] == 0 and out["attention"] == 0
