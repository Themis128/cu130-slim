"""Datalake export of email campaigns — PII-minimized shape."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models.email_campaign import CampaignKind, CampaignStatus, RecipientStatus
from app.worker.tasks import datalake_export as de
from tests.unit.test_email_campaigns import _DB, _Res


@pytest.mark.asyncio
async def test_export_email_campaigns_hashes_recipients_and_respondents():
    when = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    camp = SimpleNamespace(
        id=uuid.uuid4(), team_id=uuid.uuid4(), name="Survey", kind=CampaignKind.survey,
        status=CampaignStatus.sent, survey_url=None, sent_at=when, created_at=when,
    )
    draft = SimpleNamespace(
        id=uuid.uuid4(), team_id=camp.team_id, name="Draft", kind=CampaignKind.marketing,
        status=CampaignStatus.draft, survey_url="https://forms.example/x", sent_at=None, created_at=when,
    )
    lid = uuid.uuid4()
    r1 = SimpleNamespace(email="Ann@Example.com", lead_id=lid, status=RecipientStatus.sent, sent_at=when)
    r2 = SimpleNamespace(email="bob@example.com", lead_id=None, status=RecipientStatus.pending, sent_at=None)
    resp1 = SimpleNamespace(lead_id=lid, answers={"q": 5}, espocrm_noted=True, submitted_at=when)
    resp2 = SimpleNamespace(lead_id=None, answers={}, espocrm_noted=False, submitted_at=when)
    db = _DB([
        _Res([camp, draft]),
        _Res([r1, r2]), _Res([(resp1, "ann@example.com"), (resp2, None)]),
        _Res([]), _Res([]),
    ])

    out = await de._export_email_campaigns(db)

    assert [c["name"] for c in out] == ["Survey", "Draft"]
    survey = out[0]
    assert survey["campaign_id"] == str(camp.id) and survey["team_id"] == str(camp.team_id)
    assert survey["survey_url"] is False and out[1]["survey_url"] is True
    assert survey["sent_at"] == when.isoformat() and out[1]["sent_at"] is None
    assert survey["recipients"][0] == {
        "email_hash": de._email_hash("ann@example.com"), "lead_id": str(lid),
        "status": str(RecipientStatus.sent), "sent_at": when.isoformat(),
    }
    assert survey["recipients"][1]["lead_id"] is None and survey["recipients"][1]["sent_at"] is None
    assert survey["responses"][0]["email_hash"] == de._email_hash("Ann@Example.com")
    assert survey["responses"][0]["answers"] == {"q": 5} and survey["responses"][0]["espocrm_noted"] is True
    assert survey["responses"][1]["email_hash"] is None and survey["responses"][1]["lead_id"] is None
    assert out[1]["recipients"] == [] and out[1]["responses"] == []
    # no raw addresses anywhere in the lake payload
    assert "@" not in json.dumps(out)
