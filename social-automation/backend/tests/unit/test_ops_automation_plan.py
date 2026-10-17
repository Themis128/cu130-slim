"""GET /ops/automation-plan (#674) — team lookup, date parsing, delegation."""
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.api.ops as OPS
import app.services.strategy_report as SR


class _DB:
    def __init__(self, team):
        self.team = team

    async def scalar(self, stmt):
        return self.team


@pytest.mark.asyncio
async def test_missing_team_404():
    with pytest.raises(HTTPException) as e:
        await OPS.get_automation_plan(uuid.uuid4(), 30, True, None, SimpleNamespace(), _DB(None))
    assert e.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("target,expected", [("2026-10-21", date(2026, 10, 21)), (None, None)])
async def test_builds_report_and_plan(monkeypatch, target, expected):
    team = SimpleNamespace(id=uuid.uuid4(), name="Cloudless")
    calls = {}

    async def fake_build(db, *, team, insight_days, use_llm):
        calls["build"] = (team, insight_days, use_llm)
        return "REPORT"

    def fake_plan(report, *, target_date=None):
        calls["plan"] = (report, target_date)
        return {"actions": [{"platform": "linkedin"}], "date": str(target_date)}

    monkeypatch.setattr(SR, "build_strategy_report", fake_build)
    monkeypatch.setattr(SR, "automation_plan", fake_plan)
    out = await OPS.get_automation_plan(team.id, 14, False, target, SimpleNamespace(), _DB(team))
    assert out["actions"] == [{"platform": "linkedin"}]
    assert calls["build"] == (team, 14, False)
    assert calls["plan"] == ("REPORT", expected)
