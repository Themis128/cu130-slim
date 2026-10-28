"""Tests for app/services/support_report.py — support Slack diagnostics."""
from __future__ import annotations

import uuid
from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.support_report as sr
from app.models.content import PostStatus


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _DB:
    def __init__(self, results):
        self._q = deque(results)

    async def execute(self, q):
        return self._q.popleft()


def _user():
    return SimpleNamespace(id=uuid.uuid4(), name="Themis")


def _team(**kw):
    base = dict(
        id=uuid.uuid4(), name="Cloudless", plan_tier="pro",
        subscription_status="active", paddle_customer_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_build_report_no_team():
    text = await sr.build_support_report_text(
        _user(), None, _DB([]),
        message="help!", category="bug", reply_email="u@x.io",
        ip="1.2.3.4", user_agent="agent/1",
    )
    assert "*Support request*" in text
    assert "*Category:* bug" in text
    assert "Themis <u@x.io>" in text
    assert "*Team:* —" in text
    assert "*Connected accounts (0)*" in text
    assert "*Recent posts (7d): 0*" in text
    assert "agent/1" in text and "1.2.3.4" in text


@pytest.mark.asyncio
async def test_build_report_with_team_accounts_and_failed_posts():
    acc = SimpleNamespace(
        platform="linkedin", display_name="LI", username="li",
        account_id="a1", status="active",
        token_expires_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    acc2 = SimpleNamespace(
        platform="tiktok", display_name=None, username=None,
        account_id="tk1", status=None, token_expires_at=None,
    )
    ok_post = SimpleNamespace(
        status=PostStatus.PUBLISHED, content_text="all good", id=uuid.uuid4(),
        platform_specific=None,
    )
    fail_post = SimpleNamespace(
        status=PostStatus.FAILED, content_text="boom", id=uuid.uuid4(),
        platform_specific={"error": "token expired"},
    )
    fail_no_err = SimpleNamespace(
        status=PostStatus.FAILED, content_text="boom2", id=uuid.uuid4(),
        platform_specific="not-a-dict",
    )
    team = _team(paddle_customer_id="ctm_123")
    db = _DB([_Res([acc, acc2]), _Res([ok_post, fail_post, fail_no_err])])
    text = await sr.build_support_report_text(
        _user(), team, db,
        message="m", category="billing", reply_email="r@x.io",
    )
    assert "*Paddle customer:* ctm_123" in text
    assert "*Connected accounts (2)*" in text
    assert "linkedin — *LI* — active — expires 2026-01-01 00:00" in text
    assert "tiktok — *tk1* — unknown — expires —" in text
    assert "*Recent posts (7d): 3* · failed: *2*" in text
    assert "token expired" in text
    assert "no error captured" in text
    assert "all good" in text


@pytest.mark.asyncio
async def test_build_report_omits_paddle_line_when_missing():
    team = _team()
    db = _DB([_Res([]), _Res([])])
    text = await sr.build_support_report_text(
        _user(), team, db, message="m", category="q", reply_email="r@x",
    )
    assert "Paddle customer" not in text


@pytest.mark.asyncio
async def test_send_report_no_slack(monkeypatch):
    team = _team()
    db = _DB([_Res([]), _Res([])])
    out = await sr.send_support_report(
        _user(), team, db,
        message="m", category="q", reply_email="r@x", post_to_slack=False,
    )
    assert out == {"ok": True, "posted": False, "error": None, "text": out["text"]}


@pytest.mark.asyncio
async def test_send_report_slack_ok_and_fail(monkeypatch):
    team = _team()
    db = _DB([_Res([]), _Res([]), _Res([]), _Res([])])
    monkeypatch.setattr(
        sr, "post_support_report_to_slack",
        AsyncMock(side_effect=[(True, None), (False, "webhook 500")]),
    )
    out = await sr.send_support_report(
        _user(), team, db, message="m", category="q", reply_email="r@x",
    )
    assert out["posted"] is True and out["error"] is None
    out2 = await sr.send_support_report(
        _user(), team, db, message="m", category="q", reply_email="r@x",
    )
    assert out2["posted"] is False and out2["error"] == "webhook 500"
    assert out2["ok"] is True
