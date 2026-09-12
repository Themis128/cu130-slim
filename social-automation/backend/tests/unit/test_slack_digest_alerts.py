from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services.slack_digest import DigestIssue, DigestReport, post_digest_to_slack


def _report_with_issues() -> DigestReport:
    return DigestReport(
        generated_at=datetime.now(UTC),
        timezone="Europe/Athens",
        team_name="Cloudless",
        days=1,
        overview={},
        issues=[
            DigestIssue(severity="error", title="Post failed", detail="boom"),
            DigestIssue(severity="warning", title="Analytics warning", detail="slow"),
        ],
    )


def _report_no_issues() -> DigestReport:
    return DigestReport(
        generated_at=datetime.now(UTC),
        timezone="Europe/Athens",
        team_name="Cloudless",
        days=1,
        overview={},
        issues=[],
    )


@pytest.mark.asyncio
async def test_digest_posts_alert_when_issues_present():
    report = _report_with_issues()
    with patch(
        "app.services.slack_digest.post_digest_text_to_slack",
        new=AsyncMock(return_value=(True, None, "123.456")),
    ), patch(
        "app.services.slack_digest.post_alert_to_slack",
        new=AsyncMock(),
    ) as post_alert:
        out = await post_digest_to_slack(report)

    assert out.posted_to_slack is True
    post_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_digest_does_not_post_alert_when_no_issues():
    report = _report_no_issues()
    with patch(
        "app.services.slack_digest.post_digest_text_to_slack",
        new=AsyncMock(return_value=(True, None, "123.456")),
    ), patch(
        "app.services.slack_digest.post_alert_to_slack",
        new=AsyncMock(),
    ) as post_alert:
        out = await post_digest_to_slack(report)

    assert out.posted_to_slack is True
    post_alert.assert_not_awaited()

