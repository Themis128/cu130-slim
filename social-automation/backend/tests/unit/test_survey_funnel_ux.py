"""Tests for the survey-funnel UX: pill rendering, audit-CTA split, nudge,
and the staff responses dashboard."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.api.email_campaigns as api
from app.models.email_campaign import CampaignKind, RecipientStatus
from tests.unit.test_email_campaigns import _DB, _campaign, _recipient, _Res
from tests.unit.test_email_campaigns_api import _body_request

TEAM = uuid.uuid4()
USER = SimpleNamespace(id=uuid.uuid4())


class TestQuestionInputUX:
    def test_choice_renders_pill(self):
        html = api._question_input({"id": "q1", "label": "Pick one", "type": "choice", "options": ["a", "b"]}, 1)
        assert 'class="qp"' in html
        assert 'class="pill"' in html

    def test_rating_renders_segmented(self):
        html = api._question_input({"id": "q2", "label": "Rate", "type": "rating", "min": 1, "max": 5}, 2)
        assert 'class="seg"' in html
        assert 'class="segp"' in html

    def test_num_badge_shows_index(self):
        html = api._question_input({"id": "q3", "label": "Text", "type": "text"}, 3)
        assert ">3</span>" in html

    def test_no_num_omits_badge(self):
        html = api._question_input({"id": "q4", "label": "Text", "type": "text"})
        assert "_QNUM_STYLE" not in html


class TestSurveySubmitAuditCTA:
    @pytest.mark.asyncio
    async def test_audit_yes_shows_booking_cta(self, monkeypatch):
        rec = _recipient(token="t-audit")
        campaign = _campaign(kind=CampaignKind.survey)
        db = _DB([_Res([rec]), _Res([campaign])])

        async def _ok(db, token, ans):
            return (SimpleNamespace(id=uuid.uuid4()), [])

        monkeypatch.setattr(api.svc, "submit_survey", _ok)
        req = _body_request(b'{"audit_interest":"Yes","email":"a@b.c"}', "application/json")
        resp = await api.survey_submit("t-audit", req, db)
        body = resp.body.decode()
        assert "Book the free 30-min audit" in body
        assert "cloudless.gr/contact" in body

    @pytest.mark.asyncio
    async def test_audit_no_shows_readout_copy(self, monkeypatch):
        rec = _recipient(token="t-plain")
        campaign = _campaign(kind=CampaignKind.survey)
        db = _DB([_Res([rec]), _Res([campaign])])

        async def _ok(db, token, ans):
            return (SimpleNamespace(id=uuid.uuid4()), [])

        monkeypatch.setattr(api.svc, "submit_survey", _ok)
        req = _body_request(b'{"audit_interest":"No","email":"a@b.c"}', "application/json")
        resp = await api.survey_submit("t-plain", req, db)
        body = resp.body.decode()
        assert "read-out" in body or "Book the free" not in body


class TestNudge:
    @pytest.mark.asyncio
    async def test_nudge_flips_non_responders(self):
        """Sent recipients with no SurveyResponse go back to pending."""
        answered = _recipient(token="t1", status=RecipientStatus.sent)
        silent = _recipient(token="t2", status=RecipientStatus.sent)
        campaign = _campaign(kind=CampaignKind.survey)
        resp_row = (answered.id,)  # column-select returns Row tuples
        db = _DB([_Res([campaign]), _Res([resp_row]), _Res([answered, silent])])
        out = await api.nudge_campaign(campaign.id, TEAM, db, USER)
        assert out == {"nudged": 1}
        assert silent.status == RecipientStatus.pending
        assert answered.status == RecipientStatus.sent


class TestResponsesDashboard:
    @pytest.mark.asyncio
    async def test_dashboard_renders_aggregates(self):
        campaign = _campaign(kind=CampaignKind.survey, name="Offer validation")
        rec = _recipient(token="t1")
        resp_row = SimpleNamespace(
            answers={"it_support": "1 — one partner", "audit_interest": "Yes"},
            submitted_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        db = _DB([_Res([campaign]), _Res([(resp_row, rec.email)])])
        html = await api.responses_dashboard(campaign.id, TEAM, db, USER)
        body = html.body.decode()
        assert "Offer validation" in body
        assert "1 response" in body
        assert "1 audit" in body or "audit request" in body.lower()
