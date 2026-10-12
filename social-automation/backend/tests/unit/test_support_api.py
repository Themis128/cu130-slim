"""Route-level tests for ``POST /support/report``.

``send_support_report`` is the service boundary — faked. The endpoint's
reply-email fallback, client IP, and user-agent wiring is real.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.support as SU


@pytest.mark.asyncio
async def test_report_issue_uses_body_email_and_client(monkeypatch):
    send = AsyncMock(return_value={"ok": True, "posted": True, "error": None})
    monkeypatch.setattr(SU, "send_support_report", send)
    req = SimpleNamespace(
        client=SimpleNamespace(host="203.0.113.9"),
        headers={"user-agent": "curl/8.0"},
    )
    out = await SU.report_issue(
        data=SU.SupportReportIn(
            message="The queue is stuck on publish",
            category="bug",
            reply_email="ops@cloudless.gr",
        ),
        request=req,
        post_to_slack=True,
        current_user=SimpleNamespace(email="admin@example.com"),
        team=SimpleNamespace(id="t"),
        db=SimpleNamespace(),
    )
    assert out.ok is True and out.posted is True and out.error is None
    kwargs = send.await_args.kwargs
    assert kwargs["reply_email"] == "ops@cloudless.gr"
    assert kwargs["ip"] == "203.0.113.9"
    assert kwargs["user_agent"] == "curl/8.0"


@pytest.mark.asyncio
async def test_report_issue_falls_back_to_user_email_when_client_missing(monkeypatch):
    send = AsyncMock(return_value={"ok": True, "posted": False, "error": "slack down"})
    monkeypatch.setattr(SU, "send_support_report", send)
    req = SimpleNamespace(client=None, headers={})
    out = await SU.report_issue(
        data=SU.SupportReportIn(message="Need a hand with billing"),
        request=req,
        post_to_slack=False,
        current_user=SimpleNamespace(email="admin@example.com"),
        team=SimpleNamespace(id="t"),
        db=SimpleNamespace(),
    )
    assert out.posted is False and out.error == "slack down"
    kwargs = send.await_args.kwargs
    assert kwargs["reply_email"] == "admin@example.com"
    assert kwargs["ip"] is None
    assert kwargs["user_agent"] is None
    assert kwargs["post_to_slack"] is False
