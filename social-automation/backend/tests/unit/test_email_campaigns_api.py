"""Route-level tests for app/api/email_campaigns.py.

Handlers are called directly with an ordered-result DB fake (same pattern as
test_service_feed.py). EspoCRM, Celery and the service layer's mail path are
mocked — nothing leaves the process.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.api.email_campaigns as api
from app.models.email_campaign import CampaignKind, CampaignStatus, RecipientStatus
from tests.unit.test_email_campaigns import _DB, _campaign, _recipient, _Res

TEAM = uuid.uuid4()
USER = SimpleNamespace(id=uuid.uuid4())


class _FlushDB(_DB):
    """Commit fills column defaults the way a real flush/refresh would."""

    async def commit(self):
        await super().commit()
        for obj in self.added:
            if getattr(obj, "status", None) is None:
                obj.status = CampaignStatus.draft
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(UTC)


def _body_request(body: bytes, ctype: str) -> Request:
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "headers": [(b"content-type", ctype.encode())]},
        receive,
    )


class TestCampaignCrud:
    @pytest.mark.asyncio
    async def test_list_attaches_stats(self):
        c1, c2 = _campaign(name="A"), _campaign(name="B")
        db = _DB([_Res([c1, c2]), _Res([("sent", 3)]), _Res([])])
        out = await api.list_campaigns(TEAM, 50, db, USER)
        assert [p["name"] for p in out] == ["A", "B"]
        assert out[0]["stats"] == {"sent": 3} and out[1]["stats"] == {}

    @pytest.mark.asyncio
    async def test_create_renders_branded_html_when_missing(self):
        db = _FlushDB()
        body = api.CampaignCreate(
            name="  Launch ",
            kind=CampaignKind.survey,
            subject=" Hello ",
            text_body="Body text",
            from_name="  ",
            questions=[api.SurveyQuestion(id="q1", label="How?", type="rating")],
        )
        out = await api.create_campaign(body, TEAM, db, USER)
        (row,) = db.added
        assert row.name == "Launch" and row.subject == "Hello" and row.from_name == "Cloudless"
        assert row.team_id == TEAM and "DOCTYPE" in row.html_body
        assert row.meta_data["questions"][0]["id"] == "q1"
        assert out["kind"] == "survey" and out["status"] == "draft"
        assert out["questions"][0]["label"] == "How?"
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_create_keeps_explicit_html(self):
        db = _FlushDB()
        body = api.CampaignCreate(name="N", subject="S", text_body="t", html_body="<p>mine</p>")
        await api.create_campaign(body, TEAM, db, USER)
        assert db.added[0].html_body == "<p>mine</p>"

    @pytest.mark.asyncio
    async def test_get_found_and_missing(self):
        c = _campaign(text_body="T", html_body="<b>H</b>")
        out = await api.get_campaign(c.id, TEAM, _DB([_Res([c]), _Res([("pending", 2)])]), USER)
        assert out["text_body"] == "T" and out["html_body"] == "<b>H</b>"
        assert out["stats"] == {"pending": 2}
        with pytest.raises(HTTPException) as e:
            await api.get_campaign(uuid.uuid4(), TEAM, _DB([_Res([])]), USER)
        assert e.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_paths(self):
        draft = _campaign(status=CampaignStatus.draft)
        assert await api.delete_campaign(draft.id, TEAM, _DB([_Res([draft])]), USER) == {"deleted": True}
        sent = _campaign(status=CampaignStatus.sent)
        with pytest.raises(HTTPException) as e:
            await api.delete_campaign(sent.id, TEAM, _DB([_Res([sent])]), USER)
        assert e.value.status_code == 409
        with pytest.raises(HTTPException) as e:
            await api.delete_campaign(uuid.uuid4(), TEAM, _DB([_Res([])]), USER)
        assert e.value.status_code == 404


class TestAudienceAndLists:
    @pytest.mark.asyncio
    async def test_add_audience_counts(self):
        c = _campaign()
        db = _DB(
            [
                _Res([c]),  # campaign
                _Res(["blocked@x.com"]),  # suppression list
                _Res([]),  # lead lookup for explicit emails
                _Res([("pending", 1), ("skipped_suppressed", 1)]),  # stats
            ]
        )
        spec = api.AudienceSpec(emails=[{"email": "ok@x.com"}, {"email": "blocked@x.com"}])
        out = await api.add_audience(c.id, spec, TEAM, db, USER)
        assert out["queued"] == 1 and out["skipped_suppressed"] == 1
        assert out["stats"] == {"pending": 1, "skipped_suppressed": 1}

    @pytest.mark.asyncio
    async def test_add_audience_404(self):
        with pytest.raises(HTTPException) as e:
            await api.add_audience(uuid.uuid4(), api.AudienceSpec(), TEAM, _DB([_Res([])]), USER)
        assert e.value.status_code == 404

    @pytest.mark.asyncio
    async def test_recipients(self):
        c = _campaign()
        when = datetime(2026, 10, 1, tzinfo=UTC)
        r1 = _recipient(status=RecipientStatus.sent, sent_at=when)
        r2 = _recipient(email="b@x.com", status=RecipientStatus.failed, error="bounced")
        out = await api.list_recipients(c.id, TEAM, 200, _DB([_Res([c]), _Res([r1, r2])]), USER)
        assert out[0] == {"email": r1.email, "name": r1.name, "status": "sent", "sent_at": when.isoformat(), "error": None}
        assert out[1]["sent_at"] is None and out[1]["error"] == "bounced"
        with pytest.raises(HTTPException):
            await api.list_recipients(c.id, TEAM, 200, _DB([_Res([])]), USER)

    @pytest.mark.asyncio
    async def test_responses(self):
        c = _campaign(kind=CampaignKind.survey)
        lid = uuid.uuid4()
        when = datetime(2026, 10, 2, tzinfo=UTC)
        r1 = SimpleNamespace(lead_id=lid, answers={"q": 1}, espocrm_noted=True, submitted_at=when)
        r2 = SimpleNamespace(lead_id=None, answers={}, espocrm_noted=False, submitted_at=when)
        out = await api.list_responses(c.id, TEAM, _DB([_Res([c]), _Res([(r1, "a@x.com"), (r2, None)])]), USER)
        assert out[0] == {"email": "a@x.com", "lead_id": str(lid), "answers": {"q": 1}, "espocrm_noted": True, "submitted_at": when.isoformat()}
        assert out[1]["lead_id"] is None and out[1]["email"] is None
        with pytest.raises(HTTPException):
            await api.list_responses(c.id, TEAM, _DB([_Res([])]), USER)


class TestSendEndpoint:
    @pytest.mark.asyncio
    async def test_404_and_no_pending(self):
        with pytest.raises(HTTPException) as e:
            await api.send_campaign(uuid.uuid4(), TEAM, _DB([_Res([])]), USER)
        assert e.value.status_code == 404
        c = _campaign()
        with pytest.raises(HTTPException) as e:
            await api.send_campaign(c.id, TEAM, _DB([_Res([c]), _Res([])]), USER)
        assert e.value.status_code == 400

    @pytest.mark.asyncio
    async def test_duplicate_refused(self):
        body = "Hi {{first_name}}, our free 30-minute audit closes Friday — book your slot now."
        prev = _campaign(name="Old promo", status=CampaignStatus.sent, subject="Audit", text_body=body)
        c = _campaign(subject="Audit", text_body=body)
        db = _DB([_Res([c]), _Res([_recipient()]), _Res([prev])])
        with pytest.raises(HTTPException) as e:
            await api.send_campaign(c.id, TEAM, db, USER)
        assert e.value.status_code == 409 and "Old promo" in e.value.detail

    @pytest.mark.asyncio
    async def test_enqueues_worker_task(self, monkeypatch):
        from app.worker.celery_app import celery_app

        sent = []
        monkeypatch.setattr(celery_app, "send_task", lambda name, args: sent.append((name, args)))
        c = _campaign()
        db = _DB([_Res([c]), _Res([_recipient(), _recipient()]), _Res([])])
        out = await api.send_campaign(c.id, TEAM, db, USER)
        assert out == {"queued": True, "pending": 2}
        assert sent == [("app.worker.tasks.email_campaigns.send_campaign", [str(c.id)])]


class TestSuppressionsAndCrm:
    @pytest.mark.asyncio
    async def test_list_and_add_suppression(self):
        when = datetime(2026, 10, 3, tzinfo=UTC)
        row = SimpleNamespace(email="a@x.com", reason="manual", source="api", created_at=when)
        out = await api.list_suppressions(TEAM, _DB([_Res([row])]), USER)
        assert out == [{"email": "a@x.com", "reason": "manual", "source": "api", "created_at": when.isoformat()}]
        db = _DB([_Res([])])
        res = await api.add_suppression(api.SuppressionAdd(email="B@X.com"), TEAM, db, USER)
        assert res == {"email": "b@x.com", "suppressed": True}
        assert db.added[0].source == "api" and db.added[0].reason == "manual"

    @pytest.mark.asyncio
    async def test_crm_unconfigured_503(self, monkeypatch):
        monkeypatch.setattr(api.espocrm_client, "configured", lambda: False)
        for call in (api.crm_summary(TEAM, USER), api.crm_clients(TEAM, 10, USER)):
            with pytest.raises(HTTPException) as e:
                await call
            assert e.value.status_code == 503

    @pytest.mark.asyncio
    async def test_crm_summary_and_clients(self, monkeypatch):
        monkeypatch.setattr(api.espocrm_client, "configured", lambda: True)

        async def counts():
            return {"lead": 4}

        async def contacts():
            return [
                {"id": "c1", "firstName": "Ann", "lastName": "Lee", "emailAddress": "a@x.com", "accountName": "Acme", "title": "CTO"},
                {"id": "c2", "accountName": "Solo Ltd", "emailAddress": "s@x.com"},
                {"id": "c3"},
            ]

        async def leads():
            return [{"id": "l1", "firstName": "Lou", "emailAddress": "l@x.com", "status": "New", "source": "Web"}]

        monkeypatch.setattr(api.espocrm_client, "lead_counts", counts)
        monkeypatch.setattr(api.espocrm_client, "list_contacts", contacts)
        monkeypatch.setattr(api.espocrm_client, "list_leads_for_campaign", leads)
        assert await api.crm_summary(TEAM, USER) == {"configured": True, "counts": {"lead": 4}}
        out = await api.crm_clients(TEAM, 2, USER)
        assert [c["name"] for c in out["contacts"]] == ["Ann Lee", "Solo Ltd"]  # limit applied
        assert out["contacts"][0]["title"] == "CTO"
        assert out["leads"] == [{"id": "l1", "name": "Lou", "email": "l@x.com", "status": "New", "source": "Web"}]


class TestPublicUnsubscribe:
    @pytest.mark.asyncio
    async def test_confirm_states(self):
        resp = await api.unsubscribe_confirm("bad", _DB([_Res([])]))
        assert resp.status_code == 404 and b"Link expired" in resp.body
        done = _recipient(status=RecipientStatus.unsubscribed)
        resp = await api.unsubscribe_confirm("t", _DB([_Res([done])]))
        assert resp.status_code == 200 and b"Already unsubscribed" in resp.body
        rec = _recipient(token="tok9")
        resp = await api.short_unsubscribe_confirm("tok9", _DB([_Res([rec])]))
        assert b'action="/u/tok9"' in resp.body and rec.email.encode() in resp.body
        # GET never mutates
        assert rec.status == RecipientStatus.pending

    @pytest.mark.asyncio
    async def test_post_unsubscribes(self):
        resp = await api.unsubscribe_post("bad", _DB([_Res([])]))
        assert resp.status_code == 404
        rec = _recipient()
        db = _DB([_Res([rec]), _Res([])])
        resp = await api.short_unsubscribe_post(rec.token, db)
        assert resp.status_code == 200 and b"Unsubscribed" in resp.body
        assert rec.status == RecipientStatus.unsubscribed
        assert db.added[0].email == rec.email


class TestPublicSurvey:
    Q = [
        {"id": "pick", "label": "Pick one", "type": "choice", "options": ["a", "b"], "required": True},
        {"id": "rate", "label": "Rate", "type": "rating", "min": 1, "max": 3},
        {"id": "note", "label": "Notes"},
    ]

    def test_question_inputs(self):
        choice = api._question_input(self.Q[0])
        assert choice.count('type="radio"') == 2 and " required" in choice
        rating = api._question_input(self.Q[1])
        assert [f'value="{n}"' in rating for n in (1, 2, 3)] == [True] * 3 and " required" not in rating
        text = api._question_input(self.Q[2])
        assert "<textarea" in text and 'name="note"' in text

    @pytest.mark.asyncio
    async def test_form_states(self):
        resp = await api.survey_form("bad", _DB([_Res([])]))
        assert resp.status_code == 404
        rec = _recipient()
        resp = await api.survey_form(rec.token, _DB([_Res([rec]), _Res([uuid.uuid4()])]))
        assert b"Already submitted" in resp.body
        # campaign gone → unavailable
        resp = await api.survey_form(rec.token, _DB([_Res([rec]), _Res([]), _Res([])]))
        assert resp.status_code == 404 and b"Unavailable" in resp.body
        camp = _campaign(kind=CampaignKind.survey, name="Feedback", subject="Two questions", meta_data={"questions": self.Q})
        resp = await api.short_survey_form(rec.token, _DB([_Res([rec]), _Res([]), _Res([camp])]))
        assert resp.status_code == 200
        assert b"Feedback" in resp.body and b"Two questions" in resp.body
        assert f'action="/s/{rec.token}"'.encode() in resp.body

    @pytest.mark.asyncio
    async def test_submit_json_and_form(self, monkeypatch):
        calls = []

        async def fake_submit(db, token, answers):
            calls.append((token, answers))
            if token == "bad":
                return None, ["invalid token"]
            if token == "invalid":
                return None, ["pick: required"]
            return SimpleNamespace(id=1), []

        monkeypatch.setattr(api.svc, "submit_survey", fake_submit)
        req = _body_request(json.dumps({"answers": {"pick": "a"}}).encode(), "application/json")
        resp = await api.survey_submit("ok", req, _DB())
        assert resp.status_code == 200 and b"Thank you" in resp.body
        assert calls[-1] == ("ok", {"pick": "a"})

        req = _body_request(json.dumps({"pick": "b"}).encode(), "application/json")
        await api.survey_submit("ok", req, _DB())
        assert calls[-1][1] == {"pick": "b"}  # bare dict accepted

        req = _body_request(b"[1,2]", "application/json")
        await api.survey_submit("ok", req, _DB())
        assert calls[-1][1] == {}

        req = _body_request(b"pick=a&note=hello", "application/x-www-form-urlencoded")
        resp = await api.short_survey_submit("invalid", req, _DB())
        assert resp.status_code == 422 and b"pick: required" in resp.body
        assert calls[-1][1] == {"pick": "a", "note": "hello"}

        req = _body_request(b"", "application/x-www-form-urlencoded")
        resp = await api.survey_submit("bad", req, _DB())
        assert resp.status_code == 404
