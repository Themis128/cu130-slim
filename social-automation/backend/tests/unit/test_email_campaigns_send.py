"""Send-path, audience, unsubscribe and funnel edge cases for
app/services/email_campaigns.py plus the Celery send task.

Complements test_email_campaigns.py; reuses its ordered-result DB fake.
External mail (Resend via send_email) and EspoCRM are always mocked.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.services.email_campaigns as S
from app.models.email_campaign import CampaignKind, CampaignStatus, RecipientStatus
from tests.unit.test_email_campaigns import _DB, _campaign, _recipient, _Res


class _CM:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def send_env(monkeypatch):
    """Patch the session factory, mailer, sleep and CRM for send_campaign."""
    state = {"mails": [], "fail_for": set(), "crm": None, "crm_error": False, "configured": False}

    async def fake_send_email(**kw):
        to = kw["to_addrs"][0]
        if to in state["fail_for"]:
            raise RuntimeError(f"resend rejected {to}")
        state["mails"].append(kw)

    async def no_sleep(_s):
        return None

    async def fake_profiles():
        if state["crm_error"]:
            raise RuntimeError("crm down")
        return state["crm"] or {}

    monkeypatch.setattr("app.services.email_digest.send_email", fake_send_email)
    monkeypatch.setattr(S.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(S.espocrm_client, "configured", lambda: state["configured"])
    monkeypatch.setattr(S.espocrm_client, "campaign_profiles", fake_profiles)

    def use_db(db):
        monkeypatch.setattr("app.db.session.async_session_maker", lambda: _CM(db))

    state["use_db"] = use_db
    return state


class TestSendCampaign:
    @pytest.mark.asyncio
    async def test_not_found(self, send_env):
        send_env["use_db"](_DB([_Res([])]))
        out = await S.send_campaign(uuid.uuid4())
        assert out["error"] == "campaign_not_found" and out["sent"] == 0

    @pytest.mark.asyncio
    async def test_full_run_sent_skipped_failed_with_crm(self, send_env):
        camp = _campaign(text_body="Hi {{first_name}} from {{crm_company}}", reply_to="r@x.com")
        ok = _recipient(email="ok@x.com", name=None, token="t-ok")
        sup = _recipient(email="sup@x.com", token="t-sup")
        bad = _recipient(email="bad@x.com", token="t-bad")
        db = _DB(
            [
                _Res([camp]),  # campaign lookup
                _Res([]),  # duplicate detector: no recent campaigns
                _Res([3]),  # _sent_today
                _Res([ok, sup, bad]),  # pending recipients
                _Res([]),  # suppression check for ok
                _Res(["sup@x.com"]),  # suppression check for sup
                _Res([]),  # suppression check for bad
                _Res([0]),  # remaining pending
            ]
        )
        send_env["use_db"](db)
        send_env["configured"] = True
        send_env["crm"] = {"ok@x.com": {"firstName": "Olga", "accountName": "Acme"}}
        send_env["fail_for"] = {"bad@x.com"}

        out = await S.send_campaign(camp.id)

        assert out["sent"] == 1 and out["skipped"] == 1 and out["failed"] == 1
        assert out["budget_left"] == S.DAILY_SEND_BUDGET - 3 - 1
        assert ok.status == RecipientStatus.sent and ok.sent_at is not None
        assert sup.status == RecipientStatus.skipped_suppressed
        assert bad.status == RecipientStatus.failed and "resend rejected" in bad.error
        # one message per recipient, CRM merge tags filled, unsubscribe headers set
        (mail,) = send_env["mails"]
        assert mail["to_addrs"] == ["ok@x.com"]
        assert mail["text_body"].startswith("Hi Olga from Acme")
        assert mail["extra_headers"]["List-Unsubscribe"].endswith("/u/t-ok>")
        assert mail["extra_headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert mail["reply_to"] == "r@x.com"
        # a failure means partial, not sent
        assert camp.status == CampaignStatus.partial and camp.sent_at is not None

    @pytest.mark.asyncio
    async def test_all_sent_marks_sent_and_crm_outage_tolerated(self, send_env):
        camp = _campaign()
        r1 = _recipient(email="a@x.com")
        db = _DB([_Res([camp]), _Res([]), _Res([0]), _Res([r1]), _Res([]), _Res([0])])
        send_env["use_db"](db)
        send_env["configured"] = True
        send_env["crm_error"] = True
        out = await S.send_campaign(camp.id)
        assert out["sent"] == 1 and out["failed"] == 0
        assert camp.status == CampaignStatus.sent

    @pytest.mark.asyncio
    async def test_budget_exhausted_keeps_rows_pending(self, send_env):
        camp = _campaign()
        r1 = _recipient(email="a@x.com")
        db = _DB(
            [
                _Res([camp]),
                _Res([]),
                _Res([S.DAILY_SEND_BUDGET]),  # whole daily budget already used
                _Res([r1]),
                _Res([1]),  # still one pending
            ]
        )
        send_env["use_db"](db)
        out = await S.send_campaign(camp.id)
        assert out == {"sent": 0, "failed": 0, "skipped": 0, "budget_left": 0}
        assert r1.status == RecipientStatus.pending
        assert camp.status == CampaignStatus.sending and camp.sent_at is None
        assert send_env["mails"] == []

    @pytest.mark.asyncio
    async def test_remaining_none_counts_as_zero(self, send_env):
        camp = _campaign()
        db = _DB([_Res([camp]), _Res([]), _Res([None]), _Res([]), _Res([None])])
        send_env["use_db"](db)
        out = await S.send_campaign(camp.id)
        assert out["budget_left"] == S.DAILY_SEND_BUDGET
        assert camp.status == CampaignStatus.sent


class TestDuplicateDetectorPicksBest:
    @pytest.mark.asyncio
    async def test_lower_scoring_later_candidate_ignored(self):
        body = "Hi {{first_name}}, our free 30-minute audit closes Friday — book now."
        best = _campaign(status=CampaignStatus.sent, subject="Audit", text_body=body)
        weak = _campaign(status=CampaignStatus.sent, subject="Other", text_body="Totally unrelated words here.")
        new = _campaign(subject="Audit", text_body=body)
        dup, score = await S.find_duplicate_campaign(_DB([_Res([best, weak])]), new)
        assert dup is best and score >= S.DUP_THRESHOLD


class TestSuppressionHelpers:
    @pytest.mark.asyncio
    async def test_is_suppressed_normalizes_case(self):
        tid = uuid.uuid4()
        assert await S.is_suppressed(_DB([_Res([" A@X.com "])]), tid, "a@x.COM") is True
        assert await S.is_suppressed(_DB([_Res(["b@x.com"])]), tid, "a@x.com") is False

    @pytest.mark.asyncio
    async def test_unsubscribe_by_token(self):
        rec = _recipient(email="Who@X.com")
        db = _DB([_Res([rec]), _Res([])])  # recipient, no existing suppression
        got, sup = await S.unsubscribe_by_token(db, rec.token)
        assert got is rec and rec.status == RecipientStatus.unsubscribed
        assert sup.email == "who@x.com" and sup.source == "unsubscribe_link"
        assert sup.reason == "unsubscribe" and sup.team_id == rec.team_id

    @pytest.mark.asyncio
    async def test_unsubscribe_unknown_token(self):
        assert await S.unsubscribe_by_token(_DB([_Res([])]), "nope") == (None, None)


class TestResolveAudienceSources:
    @pytest.mark.asyncio
    async def test_leads_filters_and_espocrm(self, monkeypatch):
        lead = SimpleNamespace(id=uuid.uuid4(), email="Lead@X.com", name="Lena")
        db = _DB([_Res([]), _Res([lead])])
        monkeypatch.setattr(S.espocrm_client, "configured", lambda: True)

        async def profiles():
            return {
                "crm@x.com": {"firstName": "Cris", "lastName": "Mo"},
                "acct@x.com": {"accountName": "Acme SA"},
                "lead@x.com": {"firstName": "Dup"},  # already added from leads
            }

        monkeypatch.setattr(S.espocrm_client, "campaign_profiles", profiles)
        recs = await S.resolve_audience(
            db,
            _campaign(),
            lead_sources=["website"],
            lead_interests=["seo"],
            include_espocrm=True,
        )
        by = {r.email: r for r in recs}
        assert set(by) == {"lead@x.com", "crm@x.com", "acct@x.com"}
        assert by["lead@x.com"].lead_id == lead.id and by["lead@x.com"].name == "Lena"
        assert by["crm@x.com"].name == "Cris Mo" and by["crm@x.com"].lead_id is None
        assert by["acct@x.com"].name == "Acme SA"
        assert db.added == recs and db.commits == 1

    @pytest.mark.asyncio
    async def test_espocrm_failure_does_not_abort(self, monkeypatch):
        monkeypatch.setattr(S.espocrm_client, "configured", lambda: True)

        async def boom():
            raise RuntimeError("crm down")

        monkeypatch.setattr(S.espocrm_client, "campaign_profiles", boom)
        db = _DB([_Res([])])
        recs = await S.resolve_audience(
            db,
            _campaign(),
            explicit_emails=[{"email": ""}],
            include_espocrm=True,
        )
        assert recs == [] and db.commits == 1

    @pytest.mark.asyncio
    async def test_include_socialauto_leads_without_filters(self):
        lead = SimpleNamespace(id=uuid.uuid4(), email="x@y.com", name="  ")
        recs = await S.resolve_audience(_DB([_Res([]), _Res([lead])]), _campaign(), include_socialauto_leads=True)
        assert [r.email for r in recs] == ["x@y.com"] and recs[0].name is None


class TestFunnelEdges:
    def test_survey_questions_filters_malformed(self):
        c = _campaign(
            meta_data={
                "questions": [
                    {"id": "q1", "label": "One"},
                    {"id": "q2"},
                    "junk",
                    {"label": "no id"},
                ]
            }
        )
        assert S.survey_questions(c) == [{"id": "q1", "label": "One"}]
        assert S.survey_questions(_campaign(meta_data=None)) == []

    def test_rating_not_number_and_custom_range_and_free_choice(self):
        q = [
            {"id": "r", "label": "R", "type": "rating", "min": 0, "max": 10},
            {"id": "c", "label": "C", "type": "choice"},  # no options → free text
            {"id": "t", "label": "T"},
        ]
        clean, errors = S.validate_answers(q, {"r": "abc", "c": " anything ", "t": "  "})
        assert errors == ["r: not a number"]
        assert clean == {"c": "anything"}
        clean, errors = S.validate_answers(q, {"r": 0})
        assert clean == {"r": 0} and errors == []

    @pytest.mark.asyncio
    async def test_submit_validation_errors_returned(self):
        rec = _recipient()
        camp = _campaign(kind=CampaignKind.survey, meta_data={"questions": [{"id": "q", "label": "Q", "required": True}]})
        db = _DB([_Res([rec]), _Res([camp]), _Res([])])
        resp, errors = await S.submit_survey(db, rec.token, None)
        assert resp is None and errors == ["q: required"] and not db.added

    @pytest.mark.asyncio
    async def test_submit_lead_missing_or_without_crm_id(self, monkeypatch):
        async def never(*a, **k):
            raise AssertionError("CRM must not be called")

        monkeypatch.setattr(S.espocrm_client, "add_lead_note", never)
        camp = _campaign(kind=CampaignKind.survey, meta_data={"questions": [{"id": "q", "label": "Q"}]})
        # lead row gone
        rec = _recipient(lead_id=uuid.uuid4())
        db = _DB([_Res([rec]), _Res([camp]), _Res([]), _Res([])])
        resp, errors = await S.submit_survey(db, rec.token, {"q": "hi"})
        assert errors == [] and resp.answers == {"q": "hi"} and db.commits == 1
        # lead exists with prior notes but no CRM link
        lead = SimpleNamespace(id=rec.lead_id, notes="old", meta_data=None)
        db = _DB([_Res([rec]), _Res([camp]), _Res([]), _Res([lead])])
        await S.submit_survey(db, rec.token, {"q": "yo"})
        assert lead.notes.startswith("old\n[survey ") and "q=yo" in lead.notes
        assert lead.meta_data["last_survey_campaign_id"] == str(camp.id)


class TestStatsAndPayload:
    @pytest.mark.asyncio
    async def test_campaign_stats(self):
        db = _DB([_Res([("sent", 2), ("failed", 1)])])
        assert await S.campaign_stats(db, uuid.uuid4()) == {"sent": 2, "failed": 1}

    def test_payload(self):
        sent_at = datetime(2026, 10, 1, tzinfo=UTC)
        c = _campaign(kind=CampaignKind.survey, status=CampaignStatus.sent, sent_at=sent_at, meta_data={"questions": [{"id": "q", "label": "Q"}]})
        p = S.campaign_payload(c)
        assert p["kind"] == "survey" and p["status"] == "sent"
        assert p["sent_at"] == sent_at.isoformat() and p["questions"] == [{"id": "q", "label": "Q"}]
        assert S.campaign_payload(_campaign())["sent_at"] is None


class TestSendTask:
    def test_delegates_to_service(self, monkeypatch):
        from app.worker.tasks import email_campaigns as T

        cid = uuid.uuid4()
        seen = {}

        async def fake_send(campaign_id):
            seen["id"] = campaign_id
            return {"sent": 4}

        monkeypatch.setattr(S, "send_campaign", fake_send)
        assert T.send_campaign.run(str(cid)) == {"sent": 4}
        assert seen["id"] == cid

    def test_failure_retries(self, monkeypatch):
        from app.worker.tasks import email_campaigns as T

        async def boom(campaign_id):
            raise RuntimeError("smtp down")

        class Retry(Exception):
            pass

        captured = {}

        def fake_retry(exc=None, **kw):
            captured["exc"] = exc
            return Retry()

        monkeypatch.setattr(S, "send_campaign", boom)
        monkeypatch.setattr(T.send_campaign, "retry", fake_retry)
        with pytest.raises(Retry):
            T.send_campaign.run(str(uuid.uuid4()))
        assert isinstance(captured["exc"], RuntimeError)
