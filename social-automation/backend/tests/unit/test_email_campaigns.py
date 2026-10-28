"""Coverage for app/services/email_campaigns.py — funnel + suppression logic."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.services.email_campaigns as S
from app.models.email_campaign import CampaignKind, CampaignStatus, RecipientStatus


def _campaign(**kw):
    d = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        name="Camp",
        kind=CampaignKind.marketing,
        subject="S",
        text_body="Hi {{first_name}}",
        html_body=None,
        from_name="Cloudless",
        reply_to=None,
        status=CampaignStatus.draft,
        sent_at=None,
        espocrm_target_list_id=None,
        survey_url=None,
        meta_data={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _recipient(**kw):
    d = dict(
        id=uuid.uuid4(),
        campaign_id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        lead_id=None,
        email="client@example.com",
        name="Client",
        status=RecipientStatus.pending,
        error=None,
        token="tok123",
        sent_at=None,
        created_at=datetime.now(UTC),
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

    async def delete(self, obj):
        self.deleted = getattr(self, "deleted", []) + [obj]

    async def commit(self):
        self.commits += 1


class TestMergeTags:
    def test_render_known_and_unknown(self):
        out = S.render_template("Hi {{first_name}} — {{missing}}", {"first_name": "Ann"})
        assert out == "Hi Ann — {{missing}}"

    def test_unsubscribe_url_shape(self):
        url = S.unsubscribe_url("abc123")
        assert url.endswith("/u/abc123")

    def test_survey_url_per_recipient_token(self):
        c = _campaign(kind=CampaignKind.survey)
        r = _recipient(token="secrettoken")
        url = S.survey_url_for(c, r)
        assert url.endswith(f"/s/{r.token}")

    def test_survey_url_external_override(self):
        c = _campaign(kind=CampaignKind.survey, survey_url="https://cloud.cloudless.gr/forms/x")
        assert S.survey_url_for(c, _recipient()) == "https://cloud.cloudless.gr/forms/x"

    def test_build_bodies_injects_survey_and_unsub(self):
        c = _campaign(kind=CampaignKind.survey, text_body="Hello {{first_name}}")
        r = _recipient(name="Themis Baltzakis", token="tk")
        text, html = S.build_bodies(c, r, "https://u/x")
        assert "Themis" in text and "Take the survey:" in text and "Unsubscribe:" in text
        # text-only campaigns now send the branded HTML shell (not raw text)
        assert html is not None and "DOCTYPE" in html and "Themis" in html
        assert 'href="https://u/x"' in html  # unsubscribe link appended

    def test_build_bodies_personalizes_from_crm(self):
        c = _campaign(text_body="{{first_name}} at {{crm_company}} ({{crm_industry}}) — {{crm_entity}}")
        r = _recipient(name=None)
        text, html = S.build_bodies(
            c,
            r,
            "https://u/x",
            crm={
                "id": "abc",
                "firstName": "Maria",
                "lastName": "Galani",
                "accountName": "Galani Psych",
                "industry": "Healthcare",
                "phoneNumber": "+30 210 555",
                "doNotCall": False,
                "entity": "Contact",
            },
        )
        # CRM names backfill a missing recipient name
        assert text.startswith("Maria")
        assert "Galani Psych" in text and "Healthcare" in text and "Contact" in text
        assert "{{" not in text  # every authored tag resolved
        assert html is not None and "Maria" in html

    def test_build_bodies_crm_unknown_tag_stays_literal(self):
        c = _campaign(text_body="Hi {{first_name}} — {{crm_no_such_field}}")
        text, _ = S.build_bodies(c, _recipient(name="Ana X"), "https://u/x")
        assert "Ana" in text and "{{crm_no_such_field}}" in text

    def test_crm_ctx_flattens_and_aliases(self):
        ctx = S._crm_ctx(
            {
                "id": "x",
                "accountName": "Acme",
                "phoneNumber": "123",
                "addressCity": "Athens",
                "assignedUserName": "Themis",
                "doNotCall": True,
                "meta": {"nested": 1},
                "empty": "  ",
            }
        )
        assert ctx["crm_account_name"] == "Acme"
        assert ctx["crm_company"] == "Acme"  # alias
        assert ctx["crm_phone"] == "123" and ctx["crm_city"] == "Athens"
        assert ctx["crm_owner"] == "Themis"
        assert "crm_id" not in ctx and "crm_do_not_call" not in ctx
        assert "crm_meta" not in ctx and "crm_empty" not in ctx
        assert S._crm_ctx(None) == {}


class TestAudience:
    @pytest.mark.asyncio
    async def test_dedupe_synthetic_and_suppression(self):
        db = _DB([_Res(["old@example.com"])])  # first execute = suppression emails
        campaign = _campaign()
        recs = await S.resolve_audience(
            db,
            campaign,
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
        db = _DB(
            [
                _Res([]),  # suppression list
                _Res([(lead_id, "known@example.com")]),  # lead lookup by email
            ]
        )
        recs = await S.resolve_audience(
            db,
            _campaign(),
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
        clean, errors = S.validate_answers(self.Q, {"sat": "5", "pick": "a", "notes": " nice ", "junk": "x"})
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
            id=rec.lead_id,
            notes=None,
            meta_data={"espocrm_lead_id": "crm123"},
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


class TestDuplicateCampaignGate:
    """Every sent campaign must be unique — same detector as post publishing."""

    @pytest.mark.asyncio
    async def test_matches_sent_campaign(self):
        sent = _campaign(
            name="Launch promo",
            status=CampaignStatus.sent,
            subject="Cloudless launch offer",
            text_body="Hi {{first_name}}, our free 30-minute audit closes Friday — book your slot now.",
        )
        new = _campaign(
            subject="Cloudless launch offer",
            text_body="Hi {{first_name}}, our free 30-minute audit closes Friday — book your slot now.",
        )
        db = _DB([_Res([sent])])
        dup, score = await S.find_duplicate_campaign(db, new)
        assert dup is sent and score >= S.DUP_THRESHOLD

    @pytest.mark.asyncio
    async def test_unique_campaign_passes(self):
        sent = _campaign(
            status=CampaignStatus.sent,
            subject="Black Friday hosting deal",
            text_body="Migrate to Cloudflare and cut server costs — details inside.",
        )
        new = _campaign(
            subject="Quick onboarding survey",
            text_body="How was your first week? Two questions, one minute.",
        )
        db = _DB([_Res([sent])])
        dup, score = await S.find_duplicate_campaign(db, new)
        assert dup is None and score < S.DUP_THRESHOLD

    @pytest.mark.asyncio
    async def test_shared_signoff_does_not_match(self):
        # Identical footer/sign-off must not push two different emails over the line.
        footer = "\n\n— Themis, Cloudless\nUnsubscribe link at the bottom of every email."
        sent = _campaign(
            status=CampaignStatus.sent,
            subject="Audit slots open",
            text_body=(
                "Your infrastructure audit slots for November are open. In thirty "
                "minutes we map every server you run, flag what Cloudflare Workers "
                "and Pages can absorb, and hand you a written migration plan with "
                "real cost deltas — the same review we run on our own stack."
            )
            + footer,
        )
        new = _campaign(
            subject="Survey: rate onboarding",
            text_body=(
                "Two questions about your first week with cloudless.gr — how did "
                "the migration feel, and what should we document better? It takes "
                "under a minute and feeds straight into our CRM."
            )
            + footer,
        )
        db = _DB([_Res([sent])])
        dup, score = await S.find_duplicate_campaign(db, new)
        assert dup is None, f"false positive at {score}"

    @pytest.mark.asyncio
    async def test_send_refuses_duplicate(self, monkeypatch):
        sent_prev = _campaign(
            name="Old",
            status=CampaignStatus.sent,
            subject="Launch offer",
            text_body="Hi {{first_name}}, grab the deal.",
        )
        camp = _campaign(subject="Launch offer", text_body="Hi {{first_name}}, grab the deal.")
        db = _DB([_Res([camp]), _Res([sent_prev])])

        class _CM:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr("app.db.session.async_session_maker", lambda: _CM())
        result = await S.send_campaign(uuid.UUID(str(camp.id)) if not isinstance(camp.id, uuid.UUID) else camp.id)
        assert result["error"] == "duplicate_campaign"
        assert result["duplicate_of"] == str(sent_prev.id)
        assert camp.status == CampaignStatus.draft  # never flipped to sending


class TestDeleteDraftCampaign:
    @pytest.mark.asyncio
    async def test_deletes_draft(self):
        c = _campaign(status=CampaignStatus.draft)
        db = _DB([_Res([c])])
        out = await S.delete_draft_campaign(db, c.id, c.team_id)
        assert out is c
        assert db.deleted == [c]
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_refuses_sent_campaign(self):
        c = _campaign(status=CampaignStatus.sent)
        db = _DB([_Res([c])])
        with pytest.raises(ValueError, match="not draft"):
            await S.delete_draft_campaign(db, c.id, c.team_id)
        assert not getattr(db, "deleted", [])
        assert db.commits == 0

    @pytest.mark.asyncio
    async def test_missing_returns_none(self):
        db = _DB([_Res([])])
        assert await S.delete_draft_campaign(db, uuid.uuid4(), uuid.uuid4()) is None


class TestBrandedRenderer:
    def test_text_to_elegant_html(self):
        from app.services.email_brand import render_branded_html

        txt = (
            "Hi {{first_name}},\n\n"
            "A plain paragraph.\n\n"
            "1. First thing. Detail here.\n"
            "2. Second thing. More detail.\n\n"
            "> Wise words\n— Carl Rogers\n\n"
            "P.S. If the timing is wrong, that is fine."
        )
        h = render_branded_html(
            txt,
            subject="Subj",
            from_name="Cloudless",
            preheader="preview",
            hero_url="https://example.com/h.png",
        )
        assert "{{first_name}}" in h  # merge fields survive for send-time render
        assert 'src="https://example.com/h.png"' in h
        assert "display:none" in h  # preheader hidden text
        assert "01" in h and "02" in h  # numbered ghost cards
        assert "Carl Rogers" in h  # quote attribution kept
        assert "First thing." in h and "Detail here" in h
        assert "<!DOCTYPE html>" in h and 'lang="en"' in h

    def test_cta_line_and_markdown_link_become_buttons(self):
        from app.services.email_brand import render_branded_html

        h1 = render_branded_html("Body.\n\nCTA: Book a review | https://x.test/go")
        assert "border-radius:999px" in h1 and 'href="https://x.test/go"' in h1
        assert "Book a review" in h1

        h2 = render_branded_html("Body.\n\n[See plans](https://x.test/plans)")
        assert "border-radius:999px" in h2 and 'href="https://x.test/plans"' in h2

    def test_html_escaping(self):
        from app.services.email_brand import render_branded_html

        h = render_branded_html('<script>alert("x")</script>')
        assert "<script>" not in h and "&lt;script&gt;" in h

    def test_send_path_wraps_text_only_campaign(self):
        # campaigns without html_body get branded HTML at send, not raw text
        c = _campaign(text_body="Hello {{first_name}}")
        _text, html = S.build_bodies(c, _recipient(), "https://u/x")
        assert html is not None and "cloudless<span" in html


def test_parse_cta_line_valid():
    from app.services.email_brand import _parse_cta_line

    assert _parse_cta_line("CTA: Book a call | https://cloudless.gr/contact") == (
        "Book a call",
        "https://cloudless.gr/contact",
    )


def test_parse_cta_line_rejects_bad_url():
    from app.services.email_brand import _parse_cta_line

    assert _parse_cta_line("CTA: Book | notaurl") is None
    assert _parse_cta_line("CTA: Book | https://x y") is None  # space in url
    assert _parse_cta_line("CTA:  | https://ok.gr") is None  # empty label
