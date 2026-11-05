"""Coverage for auto_gates — autonomy dial + autosend/autoapprove."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.auto_gates as AG

pytestmark = pytest.mark.asyncio


class _AsyncCM:
    def __init__(self, v):
        self._v = v

    async def __aenter__(self):
        return self._v

    async def __aexit__(self, *a):
        return False


def _scalars(vals):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: vals),
        first=lambda: vals[0] if vals else None,
    )


def test_autonomy_level(monkeypatch):
    monkeypatch.setenv("AUTONOMY_LEVEL", "3")
    assert AG._autonomy_level() == 3
    monkeypatch.setenv("AUTONOMY_LEVEL", "nope")
    assert AG._autonomy_level() == 0


async def test_engagement_autoapprove_levels(monkeypatch):
    monkeypatch.setenv("AUTONOMY_LEVEL", "1")
    assert (await AG._engagement_autoapprove())["off"] is True

    safe = SimpleNamespace(status="pending_human_gate", action="comment", comment_draft="hi", score=80)
    unsafe = SimpleNamespace(status="pending_human_gate", action="repost", comment_draft=None, score=90)
    low = SimpleNamespace(status="pending_human_gate", action="comment", comment_draft="x", score=10)
    rows = [safe, unsafe, low]
    db = SimpleNamespace(execute=AsyncMock(return_value=_scalars(rows)), commit=AsyncMock())
    monkeypatch.setattr(AG, "task_session", lambda: _AsyncCM(db))
    import app.services.slack_notifications as SN
    monkeypatch.setattr(SN, "notify_engagement_autoapprove", lambda r: None, raising=False)

    monkeypatch.setenv("AUTONOMY_LEVEL", "2")
    out = await AG._engagement_autoapprove()
    assert out["approved"] == 1 and safe.status == "pending" and unsafe.status == "pending_human_gate"

    safe.status, unsafe.status = "pending_human_gate", "pending_human_gate"
    monkeypatch.setenv("AUTONOMY_LEVEL", "3")
    out = await AG._engagement_autoapprove()
    assert out["approved"] == 3


async def test_engagement_notify_exception(monkeypatch):
    monkeypatch.setenv("AUTONOMY_LEVEL", "3")
    r = SimpleNamespace(status="pending_human_gate", action="comment", comment_draft="x", score=99)
    db = SimpleNamespace(execute=AsyncMock(return_value=_scalars([r])), commit=AsyncMock())
    monkeypatch.setattr(AG, "task_session", lambda: _AsyncCM(db))
    import app.services.slack_notifications as SN
    monkeypatch.setattr(SN, "notify_engagement_autoapprove", lambda r: (_ for _ in ()).throw(Exception("down")), raising=False)
    assert (await AG._engagement_autoapprove())["approved"] == 1


def test_wrappers(monkeypatch):
    monkeypatch.setattr(AG, "run_async", lambda c: {"ok": 1})
    assert AG.survey_autosend() == {"ok": 1}
    assert AG.post_autoapprove() == {"ok": 1}
    assert AG.engagement_autoapprove() == {"ok": 1}
