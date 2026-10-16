"""Coverage for app/services/email_campaigns.py — funnel + suppression logic."""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.services.email_campaigns as S
from app.models.email_campaign import CampaignKind, CampaignStatus, RecipientStatus


def _campaign(**kw):
    d = dict(
        id=uuid.uuid4(), team_id=uuid.uuid4(), name="Camp", kind=CampaignKind.marketing,
        subject="S", text_body="Hi {{first_name}}", html_body=None,
        from_name="Cloudless", reply_to=None, status=CampaignStatus.draft,
        sent_at=None, espocrm_target_list_id=None, survey_url=None,
        meta_data={}, created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _recipient(**kw):
    d = dict(
        id=uuid.uuid4(), campaign_id=uuid.uuid4(), team_id=uuid.uuid4(),
        lead_id=None, email="client@example.com", name="Client",
        status=RecipientStatus.pending, error=None, token="tok123",
        sent_at=None, created_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


class _Res:
    def __init__(self, items):
        self._items = list(items)

    def scalars(self):
        return SimpleNamespace(
            all=lambda: self._items,
            first=lambda: self._items[0] if self._items else None,
        )

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalar(self):
        return self._items[0] if self._items else 0

    def all(self):
        return self._items


class _DB:
    """Ordered-result fake — execute() pops queued result sets."""

    def __init__(self, results=None):
        self._q = list(results or [])
        self.added = []
        self.commits = 0

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Res([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class TestMergeTags:
    def test_render_known_and_unknown(self):
        out = S.render_template("Hi {{first_name}} — {{missing}}", {"first_name": "Ann"})
        assert out == "Hi Ann — {{missing}}"

    def test_unsubscribe_url_shape(self):
        url = S.unsubscribe_url("abc123")
        assert url.endswith("/api/v1/email-campaigns/unsubscribe/abc123/confirm")
        assert S.unsubscribe_url("abc123", for_post=True).endswith("/post")

    def test_survey_url_per_recipient_token(self):
        c = _campaign(kind=CampaignKind.survey)
        r = _recipient(token="secrettoken")
        url = S.survey_url_for(c, r)
        assert url.endswith(f"/api/v1/email-campaigns/surveys/{r.token}")

    def test_survey_url_external_override(self):
        c = _campaign(kind=CampaignKind.survey, survey_url="https://cloud.cloudless.gr/forms/x")
        assert S.survey_url_for(c, _recipient()) == "https://cloud.cloudless.gr/forms/x"

    def test_build_bodies_injects_survey_and_unsub(self):
        c = _campaign(kind=CampaignKind.survey, text_body="Hello {{first_name}}")
        r = _recipient(name="Themis Baltzakis", token="tk")
        text, html = S.build_bodies(c, r, "https://u/x")
        assert "Themis" in text and "Take the survey:" in text and "Unsubscribe:" in text
        assert html is None


class TestAudience:
    @pytest.mark.asyncio
    async def test_dedupe_synthetic_and_suppression(self):
        db = _DB([_Res(["old@example.com"])])  # first execute = suppression emails
        campaign = _campaign()
        recs = await S.resolve_audience(
            db, campaign,
            explicit_emails=[
                {"email": "new@example.com", "name": "N"},
                {"email": "NEW@example.com"},  # dup, case-insensitive
                {"email": "psid.abc@messenger.local"},  # synthetic — dropped
                {"email": "old@example.com"},  # suppressed — queued skipped
                {"email": "not-an-email"},  # invalid shape — dropped
            ],
        )
        by_email = {r.email: r for r in recs}
        assert set(by_email) == {"new@example.com", "old@example.com"}
        assert by_email["new@example.com"].status == RecipientStatus.pending
        assert by_email["old@example.com"].status == RecipientStatus.skipped_suppressed
        assert all(r.token for r in recs)

    @pytest.mark.asyncio
    async def test_explicit_emails_link_existing_leads(self):
        lead_id = uuid.uuid4()
        db = _DB([
            _Res([]),  # suppression list
            _Res([(lead_id, "known@example.com")]),  # lead lookup by email
        ])
        recs = await S.resolve_audience(
            db, _campaign(),
            explicit_emails=[
                {"email": "known@example.com", "name": "K"},
                {"email": "stranger@example.com"},
            ],
        )
        by_email = {r.email: r for r in recs}
        assert by_email["known@example.com"].lead_id == lead_id
        assert by_email["stranger@example.com"].lead_id is None


class TestSurveyValidation:
    Q = [
        {"id": "sat", "label": "Satisfaction", "type": "rating", "required": True},
        {"id": "pick", "label": "Pick", "type": "choice", "options": ["a", "b"]},
        {"id": "notes", "label": "Notes", "type": "text"},
    ]

    def test_clean_answers(self):
        clean, errors = S.validate_answers(
            self.Q, {"sat": "5", "pick": "a", "notes": " nice ", "junk": "x"}
        )
        assert errors == []
        assert clean == {"sat": 5, "pick": "a", "notes": "nice"}  # junk dropped

    def test_required_and_range(self):
        clean, errors = S.validate_answers(self.Q, {"sat": "9"})
        assert "sat: rating out of range 1-5" in errors
        clean, errors = S.validate_answers(self.Q, {"pick": "c"})
        assert "sat: required" in errors and "pick: not a valid option" in errors


class TestSubmitSurvey:
    @pytest.mark.asyncio
    async def test_submit_stores_and_updates_lead(self, monkeypatch):
        rec = _recipient(lead_id=uuid.uuid4())
        campaign = _campaign(
            kind=CampaignKind.survey,
            meta_data={"questions": [{"id": "sat", "label": "S", "type": "rating", "required": True}]},
        )
        lead = SimpleNamespace(
            id=rec.lead_id, notes=None, meta_data={"espocrm_lead_id": "crm123"},
        )
        db = _DB([_Res([rec]), _Res([campaign]), _Res([]), _Res([lead])])

        noted = []
        async def fake_note(crm_id, text, entity="Lead"):
            noted.append((crm_id, text))
            return True
        monkeypatch.setattr(S.espocrm_client, "add_lead_note", fake_note)

        resp, errors = await S.submit_survey(db, rec.token, {"sat": "4"})
        assert errors == [] and resp is not None
        assert resp.answers == {"sat": 4} and resp.espocrm_noted is True
        assert noted and noted[0][0] == "crm123"
        assert "survey" in (lead.notes or "")
        assert lead.meta_data["last_survey_campaign_id"] == str(campaign.id)

    @pytest.mark.asyncio
    async def test_idempotent_resubmit(self):
        rec = _recipient()
        campaign = _campaign(kind=CampaignKind.survey, meta_data={"questions": []})
        existing = SimpleNamespace(id=uuid.uuid4(), answers={"sat": 3})
        db = _DB([_Res([rec]), _Res([campaign]), _Res([existing])])
        resp, errors = await S.submit_survey(db, rec.token, {"sat": "1"})
        assert resp is existing and errors == [] and not db.added

    @pytest.mark.asyncio
    async def test_rejects_bad_token_and_non_survey(self):
        db = _DB([_Res([])])
        resp, errors = await S.submit_survey(db, "nope", {})
        assert resp is None and errors == ["invalid token"]

        rec = _recipient()
        campaign = _campaign(kind=CampaignKind.marketing)
        db = _DB([_Res([rec]), _Res([campaign])])
        resp, errors = await S.submit_survey(db, rec.token, {})
        assert resp is None and errors == ["not a survey campaign"]


class TestSuppression:
    @pytest.mark.asyncio
    async def test_add_suppression_idempotent(self):
        db = _DB([_Res([])])  # no existing row
        row = await S.add_suppression(db, uuid.uuid4(), "A@B.COM", reason="manual")
        assert row.email == "a@b.com"
        db2 = _DB([_Res([row])])
        again = await S.add_suppression(db2, uuid.uuid4(), "a@b.com")
        assert again is row
