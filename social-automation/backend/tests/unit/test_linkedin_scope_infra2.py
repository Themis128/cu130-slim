"""Unit tests for the LinkedIn scope-infra lanes added 2026-11-15."""

from app.services.linkedin_admin import ROLES, _key
from app.services.linkedin_community import classify_comment
from app.worker.tasks.linkedin_offline_conversions import _lead_response_ref, _sha256


class TestClassifyComment:
    def test_spam_link_wins_over_praise(self):
        assert classify_comment("Great offer!! https://spam.example.com") == "spam"

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
