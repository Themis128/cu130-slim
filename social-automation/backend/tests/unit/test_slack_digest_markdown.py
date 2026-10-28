from datetime import UTC, datetime

from app.services.slack_digest import DigestReport


def test_digest_markdown_is_friendly_and_scannable():
    report = DigestReport(
        generated_at=datetime.now(UTC),
        timezone="Europe/Athens",
        team_name="Cloudless",
        days=1,
        overview={"total_posts": 3, "published_posts": 1, "scheduled_posts": 1, "draft_posts": 1, "failed_posts": 0, "connected_accounts": 1},
        impressions_24h=10,
        engagement_24h=2,
        issues=[],
    )

    md = report.to_slack_markdown()

    assert "Clear skies. Zero friction." in md
    assert "*At a glance*" in md
    assert "*All clear.*" in md

