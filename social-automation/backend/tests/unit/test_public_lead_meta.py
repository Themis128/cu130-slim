"""Attribution meta for POST /api/v1/leads/public (cloudless.gr forwarder)."""
from __future__ import annotations

from app.api.leads import PublicLeadRequest, public_lead_meta


def test_legacy_in_app_form_keeps_historical_meta():
    meta = public_lead_meta(PublicLeadRequest(email="a@b.co"))
    assert meta == {"form": "newsletter", "path": "public_landing"}


def test_cloudless_forwarder_attribution_is_recorded():
    body = PublicLeadRequest(
        email="a@b.co",
        site="cloudless.gr",
        form="playbook",
        page_path="/el/playbook",
        locale="el",
        consent=True,
        consent_at="2026-09-30T18:00:00.000Z",
    )
    meta = public_lead_meta(body)
    assert meta["site"] == "cloudless.gr"
    assert meta["form"] == "playbook"
    assert meta["path"] == "/el/playbook"
    assert meta["locale"] == "el"
    assert meta["consent"] is True
    assert meta["consent_at"].startswith("2026-09-30T18:00:00")


def test_unsafe_attribution_values_are_dropped():
    body = PublicLeadRequest(
        email="a@b.co",
        site="<script>",
        form="play book",
        page_path="https://evil.example/x",
        locale="english",
        consent=True,
        consent_at="not-a-date",
    )
    meta = public_lead_meta(body)
    assert "site" not in meta
    assert meta["form"] == "newsletter"
    assert meta["path"] == "public_landing"
    assert "locale" not in meta
    assert meta["consent"] is True
    assert meta["consent_at"]  # server-side timestamp fallback


def test_consent_flag_absent_or_false_not_recorded_as_consent():
    assert "consent" not in public_lead_meta(PublicLeadRequest(email="a@b.co"))
    assert "consent" not in public_lead_meta(PublicLeadRequest(email="a@b.co", consent=False))
