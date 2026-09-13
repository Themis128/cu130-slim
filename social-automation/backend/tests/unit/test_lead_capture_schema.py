import json
from pathlib import Path

import pytest

from app.models.lead import LeadCompanySize, LeadInterest, LeadSource
from app.services.leads import coerce_company_size, coerce_interest, is_valid_email


def test_lead_enums_have_expected_values():
    assert LeadSource.whatsapp_flow.value == "whatsapp_flow"
    assert LeadSource.facebook_messenger.value == "facebook_messenger"
    assert LeadSource.instagram_dm.value == "instagram_dm"

    assert LeadInterest.cloud.value == "cloud"
    assert LeadInterest.growth.value == "growth"
    assert LeadInterest.audit.value == "audit"

    assert LeadCompanySize.solo.value == "solo"
    assert LeadCompanySize.s_2_5.value == "2-5"
    assert LeadCompanySize.s_200_plus.value == "200+"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("solo", LeadCompanySize.solo),
        ("2-5", LeadCompanySize.s_2_5),
        ("6-20", LeadCompanySize.s_6_20),
        ("21-50", LeadCompanySize.s_21_50),
        ("51-200", LeadCompanySize.s_51_200),
        ("200+", LeadCompanySize.s_200_plus),
        (None, None),
        ("", None),
        ("unknown", None),
    ],
)
def test_coerce_company_size(value, expected):
    assert coerce_company_size(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("cloud", LeadInterest.cloud),
        ("growth", LeadInterest.growth),
        ("audit", LeadInterest.audit),
        ("AUDIT", LeadInterest.audit),
        (None, None),
        ("", None),
        ("other", None),
    ],
)
def test_coerce_interest(value, expected):
    assert coerce_interest(value) == expected


@pytest.mark.parametrize(
    "email, expected",
    [
        ("a@b.co", True),
        ("user.name+tag@example.com", True),
        ("not-an-email", False),
        ("a@b", False),
        ("a @b.co", False),
        ("", False),
        ("@@.", False),
    ],
)
def test_is_valid_email(email, expected):
    assert is_valid_email(email) is expected


def _find_flow_file() -> Path:
    """Resolve the WhatsApp flow JSON path robustly.

    Works both on the host (deep directory tree) and inside the Docker
    container (flat /app layout) by trying multiple candidate paths.
    """
    test_path = Path(__file__).resolve()
    candidates: list[Path] = []

    # Host layout: .../cu130-slim/social-automation/backend/tests/unit/...
    # parents[4] = .../cu130-slim
    try:
        candidates.append(
            test_path.parents[4] / "social-automation" / "flows" / "whatsapp" / "cloudless-lead-capture.json"
        )
    except IndexError:
        pass

    # Container layout: /app/tests/unit/...  (flows copied to /app/flows/)
    candidates.append(
        test_path.parents[2] / "flows" / "whatsapp" / "cloudless-lead-capture.json"
    )

    # Alternative container layout: /app/social-automation/flows/...
    candidates.append(
        test_path.parents[2] / "social-automation" / "flows" / "whatsapp" / "cloudless-lead-capture.json"
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate
    pytest.skip("cloudless-lead-capture.json not found — flow file not copied into container")


def test_cloudless_whatsapp_flow_json_structure():
    path = _find_flow_file()
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["version"] == "5.0"
    assert data["data_api_version"] == "3.0"
    assert isinstance(data.get("routing_model"), dict)

    screens = data.get("screens")
    assert isinstance(screens, list)
    ids = {s.get("id") for s in screens}
    assert ids == {"WELCOME", "CONTACT_DETAILS", "INTEREST", "THANK_YOU"}

    routing = data["routing_model"]
    assert routing["WELCOME"] == ["CONTACT_DETAILS"]
    assert routing["CONTACT_DETAILS"] == ["INTEREST"]
    assert routing["INTEREST"] == ["THANK_YOU"]

    thank_you = next(s for s in screens if s["id"] == "THANK_YOU")
    assert thank_you.get("terminal") is True
    layout = thank_you.get("layout") or {}
    assert layout.get("type") == "SingleColumnLayout"

