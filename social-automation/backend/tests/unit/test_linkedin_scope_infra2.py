"""Unit tests for the LinkedIn scope-infra lanes added 2026-11-15."""

import pytest

from app.services.linkedin_admin import ROLES, _key
from app.services.linkedin_community import classify_comment
from app.worker.tasks.linkedin_offline_conversions import (
    _already_sent,
    _espo_won_leads,
    _lead_response_ref,
    _sha256,
)


class TestClassifyComment:
    def test_single_link_alone_is_not_spam(self):
        # A bare URL must not auto-delete — customers reference their own site.
        assert classify_comment("Great offer!! check https://customer.example.com") != "spam"

    def test_linked_question_stays_lead(self):
        assert classify_comment("Can you quote a migration for https://customer.example?") == "lead"

    def test_corroborated_links_are_spam(self):
        assert classify_comment("promo https://a.example https://b.example") == "spam"

    def test_link_shortener_is_spam(self):
        assert classify_comment("deal at t.me/whatever now") == "spam"

    def test_spam_crypto(self):
        assert classify_comment("DM me for crypto investment tips") == "spam"

    def test_lead_pricing_question(self):
        assert classify_comment("What is your pricing for SMBs?") == "lead"

    def test_lead_email(self):
        assert classify_comment("Reach me at boss@company.com") == "lead"

    def test_how_question_is_question_not_lead(self):
        assert classify_comment("How does the audit work?") == "question"

    def test_plain_question(self):
        assert classify_comment("When will you cover Kubernetes?") == "question"

    def test_praise(self):
        assert classify_comment("Great work on the migration story") == "praise"

    def test_other(self):
        assert classify_comment("Following this space") == "other"

    def test_empty(self):
        assert classify_comment("") == "other"


class TestAclKey:
    def test_key_shape(self):
        k = _key("urn:li:organization:108614163", "ADMINISTRATOR", "urn:li:person:abc")
        assert k.startswith("(organization:")
        assert "role:ADMINISTRATOR" in k
        assert "roleAssignee:" in k

    def test_roles_cover_documented_set(self):
        for r in ("ADMINISTRATOR", "ANALYST", "CURATOR", "CONTENT_ADMINISTRATOR", "LEAD_GEN_FORMS_MANAGER"):
            assert r in ROLES


class TestOfflineConversionHelpers:
    def test_sha256_stable(self):
        assert _sha256("A@B.com") == _sha256("a@b.com ")
        assert len(_sha256("x@y.z")) == 64

    def test_lead_response_ref_extracts(self):
        assert _lead_response_ref("LinkedIn lead-gen response urn:li:leadGenFormResponse:123 via form") == "urn:li:leadGenFormResponse:123"

    def test_lead_response_ref_missing(self):
        assert _lead_response_ref("some other description") is None
        assert _lead_response_ref("") is None


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_a, **_k):
        return _Scalars(self._rows)


class TestAlreadySent:
    @pytest.mark.asyncio
    async def test_accepted_conversion_dedups(self):
        db = _FakeDB([{"result": {"ok": True, "status": 200}}])
        assert await _already_sent(db, 1, "lead1:lead_won") is True

    @pytest.mark.asyncio
    async def test_accepted_by_status_dedups(self):
        db = _FakeDB([{"result": {"status": 201}}])
        assert await _already_sent(db, 1, "lead1:lead_won") is True

    @pytest.mark.asyncio
    async def test_failed_attempt_retries(self):
        db = _FakeDB([{"result": {"ok": False, "status": 503, "error": "boom"}}])
        assert await _already_sent(db, 1, "lead1:lead_won") is False

    @pytest.mark.asyncio
    async def test_no_rows_not_sent(self):
        assert await _already_sent(_FakeDB([]), 1, "x:lead_won") is False


class TestEspoWonLeadsFilter:
    @pytest.mark.asyncio
    async def test_source_filter_uses_equals_type(self, monkeypatch):
        captured = {}

        async def fake_list_all(entity, **kw):
            captured.update(kw)
            return []

        monkeypatch.setattr("app.services.espocrm_client.list_all", fake_list_all)
        await _espo_won_leads()
        source_clause = next(c for c in captured["where"] if c["attribute"] == "source")
        assert source_clause["type"] == "equals"
