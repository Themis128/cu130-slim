"""Coverage for email_campaigns — responses dashboard + nudge 404."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.email_campaigns as EC

pytestmark = pytest.mark.asyncio


def _res(first=None, rows=None):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: first, all=lambda: rows or []),
        all=lambda: rows or [],
    )


async def test_responses_dashboard_404():
    db = SimpleNamespace(execute=AsyncMock(return_value=_res(first=None)))
    with pytest.raises(HTTPException) as e:
        await EC.responses_dashboard("c1", "t1", db, SimpleNamespace())
    assert e.value.status_code == 404


async def test_responses_dashboard_full(monkeypatch):
    camp = SimpleNamespace(id="c1", team_id="t1")
    qs = [
        {"id": "q1", "label": "Pick", "type": "choice", "options": ["a", "b"]},
        {"id": "q2", "label": "Free text", "type": "text"},
    ]
    monkeypatch.setattr(EC.svc, "survey_questions", lambda c: qs)
    r1 = SimpleNamespace(answers={"q1": "a", "q2": "ans1", "audit_interest": "Yes"})
    r2 = SimpleNamespace(answers={"q1": "a", "q2": "ans2"})
    rows = [(r1, "e@x.com"), (r2, None)]
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_res(first=camp), _res(rows=rows)]))
    out = await EC.responses_dashboard("c1", "t1", db, SimpleNamespace())
    html = out.body.decode() if hasattr(out, "body") else str(out)
    assert "Pick" in html and "ans1" in html and "audit" in html.lower()


async def test_nudge_campaign_404():
    db = SimpleNamespace(execute=AsyncMock(return_value=_res(first=None)))
    with pytest.raises(HTTPException) as e:
        await EC.nudge_campaign("c1", "t1", db, SimpleNamespace())
    assert e.value.status_code == 404
