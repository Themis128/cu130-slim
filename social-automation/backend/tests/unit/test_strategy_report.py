"""Unit tests for the daily strategy report — pure rendering/parsing only."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.strategy_report import (
    BriefMedia,
    BriefPost,
    StrategyReport,
    _brief_media_from_assets,
    _is_image_asset,
    _parse_actions,
    _parse_playbook_item,
    _preview_text,
    _rule_actions,
)

NOW = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)  # 21:00 Europe/Athens — the send time

INSIGHTS = {
    "window_days": 30,
    "platforms": {
        "linkedin": {
            "focus_tier": "primary",
            "confidence": "high",
            "posts": 12,
            "engagement": 340,
            "momentum_7d_engagement_pct": 18.5,
            "benchmark": {"verdict": "above_median"},
            "best_weekday_athens": (1, 4.2),
            "best_hour_athens": (9, 5.1),
        },
        "twitter": {
            "focus_tier": "last",
            "confidence": "low",
            "posts": 4,
            "engagement": 12,
            "momentum_7d_engagement_pct": None,
            "benchmark": None,
            "best_weekday_athens": None,
            "best_hour_athens": None,
        },
    },
    "recommendations": [
        {"type": "channel_focus", "priority": "high", "platform": "linkedin",
         "text": "linkedin is your strongest channel — keep original content here first."},
        {"type": "timing", "priority": "medium", "platform": "linkedin",
         "text": "Your linkedin audience engages most on Tue around 09:00 Athens time."},
        {"type": "deprioritize", "priority": "low", "platform": "twitter",
         "text": "twitter engagement is <40% of linkedin's — cross-post only."},
    ],
}


def _sample_recent() -> dict[str, list[BriefPost]]:
    return {
        "linkedin": [
            BriefPost(
                post_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                content_preview="Ship faster with Cloudless managed hosting — zero friction deploys.",
                published_at=datetime(2026, 9, 20, 10, 30, tzinfo=UTC),
                platform_url="https://www.linkedin.com/feed/update/urn:li:share:1",
                media=[
                    BriefMedia(
                        filename="deploy.png",
                        mime_type="image/png",
                        url="https://cdn.example/api/v1/media/view?path=deploy.png",
                        is_image=True,
                    ),
                    BriefMedia(
                        filename="walkthrough.mp4",
                        mime_type="video/mp4",
                        url="https://cdn.example/api/v1/media/view?path=walkthrough.mp4",
                        is_image=False,
                    ),
                ],
            ),
            BriefPost(
                post_id="11111111-2222-3333-4444-555555555555",
                content_preview="Oops — this one shipped without assets.",
                published_at=datetime(2026, 9, 19, 8, 0, tzinfo=UTC),
                platform_url=None,
                media=[],
                missing_media=True,
            ),
        ],
        "instagram": [
            BriefPost(
                post_id="inst0001-aaaa-bbbb-cccc-dddddddddddd",
                content_preview="Reel teaser for the new status page.",
                published_at=datetime(2026, 9, 18, 15, 0, tzinfo=UTC),
                platform_url="https://www.instagram.com/p/ABC/",
                media=[
                    BriefMedia(
                        filename="reel.mp4",
                        mime_type="video/mp4",
                        url="https://cdn.example/api/v1/media/view?path=reel.mp4",
                        is_image=False,
                    ),
                ],
            ),
        ],
    }


def _report(**overrides) -> StrategyReport:
    base = dict(
        generated_at=NOW,
        timezone="Europe/Athens",
        team_name="Test Team",
        insights=INSIGHTS,
        actions=["Post a LinkedIn carousel at 09:00", "Cross-post to Threads"],
        llm_used=True,
        recent_posts_by_platform=_sample_recent(),
    )
    base.update(overrides)
    return StrategyReport(**base)


def test_parse_actions_numbered_and_bulleted():
    text = (
        "Here are your actions:\n"
        "1. Post a LinkedIn carousel about deployment speed at 09:00\n"
        "2) Reply to every comment on yesterday's post within an hour\n"
        "- Cross-post the carousel to Threads around midday\n"
        "That's all.\n"
    )
    actions = _parse_actions(text)
    assert len(actions) == 3
    assert actions[0].startswith("Post a LinkedIn carousel")
    assert actions[2].startswith("Cross-post")


def test_parse_actions_rejects_short_junk():
    assert _parse_actions("1. ok\n2. - \nDone.") == []


def test_rule_actions_priority_order():
    actions = _rule_actions(INSIGHTS)
    assert actions[0].startswith("linkedin is your strongest")
    assert actions[-1].startswith("twitter engagement")


def test_subject_mentions_date():
    r = _report()
    assert "2026-09-21" in r.subject()


def test_text_render_contains_playbook_and_platforms():
    text = _report().to_text()
    assert "TOMORROW'S PLAYBOOK" in text
    assert "linkedin" in text
    assert "+18.5%" in text
    assert "Tue 09:00" in text


def test_html_render_escapes_and_tables():
    html = _report().to_html()
    assert "Platform pulse" in html
    assert "Tomorrow's playbook" in html
    assert "<td>Post a LinkedIn carousel" in html
    assert "Agent deployment block" in html
    assert "&quot;platform&quot;" in html
    assert "above median" in html


def test_agent_block_parses_destination_platform():
    # "repurpose a LinkedIn post ... on Threads" must land on Threads.
    item = _parse_playbook_item(
        "Repurpose a top LinkedIn post as a short text update on Threads at 17:00 Athens time"
    )
    assert item.platform == "threads"
    assert item.time_athens == "17:00"
    assert item.format == "text"
    assert not item.media_required

    item = _parse_playbook_item("Post a carousel on LinkedIn at 02:00 Athens time")
    assert item.platform == "linkedin"
    assert item.time_athens == "02:00"
    assert item.format == "carousel"
    assert item.media_required


def test_empty_report_still_renders():
    r = StrategyReport(generated_at=NOW, timezone="Europe/Athens", team_name="T")
    assert "publish consistently" in r.to_text()
    assert "<html>" in r.to_html()
    assert "RECENT POSTS & MEDIA" in r.to_text()
    assert "Recent posts &amp; media" in r.to_html()


def test_platform_limits_render_separate_from_sync_gaps():
    import copy
    insights = copy.deepcopy(INSIGHTS)
    insights["platforms"]["twitter"]["platform_limits"] = ["quota_exhausted"]
    insights["platforms"]["linkedin"]["data_warnings"] = [
        "linkedin stats HTTP 500"]

    text = _report(insights=insights).to_text()
    warn_lines = [ln for ln in text.splitlines() if ln.strip().startswith("⚠")]
    limit_lines = [ln for ln in text.splitlines() if "platform limits" in ln]
    assert warn_lines and "linkedin stats HTTP 500" in warn_lines[0]
    assert all("quota_exhausted" not in ln for ln in warn_lines)
    assert len(limit_lines) == 1 and "twitter: quota_exhausted" in limit_lines[0]
    # A platform with only a limit carries no [sync⚠] flag on its row.
    tw_row = next(ln for ln in text.splitlines() if ln.strip().startswith("twitter"))
    assert "[sync⚠]" not in tw_row

    html = _report(insights=insights).to_html()
    assert "platform limits (external, no action)" in html
    assert "quota_exhausted" in html
    assert "sync gaps" in html


def test_preview_text_truncates():
    assert _preview_text("short") == "short"
    long = "x" * 120
    out = _preview_text(long, limit=90)
    assert len(out) == 90
    assert out.endswith("…")
    assert _preview_text("  hello\n\nworld  ") == "hello world"


def test_is_image_asset_helpers():
    assert _is_image_asset("image/png", None) is True
    assert _is_image_asset("video/mp4", "clip.mp4") is False
    assert _is_image_asset(None, "photo.JPEG") is True


def test_brief_media_from_assets_preserves_order_and_prefers_public_url(monkeypatch):
    mid1, mid2, mid3 = uuid4(), uuid4(), uuid4()
    assets = {
        mid1: SimpleNamespace(
            id=mid1,
            filename="a.png",
            mime_type="image/png",
            public_url="https://cdn.example/a.png",
            storage_path="team/a.png",
        ),
        mid2: SimpleNamespace(
            id=mid2,
            filename="b.mp4",
            mime_type="video/mp4",
            public_url="/relative/not-absolute",
            storage_path="team/b.mp4",
        ),
        mid3: SimpleNamespace(
            id=mid3,
            filename="missing.bin",
            mime_type="application/octet-stream",
            public_url=None,
            storage_path=None,
        ),
    }

    def fake_public(path: str, *, force_jpeg: bool = False) -> str | None:
        return f"https://media.test/view?path={path}"

    monkeypatch.setattr(
        "app.services.strategy_report._media_public_url",
        fake_public,
    )
    media = _brief_media_from_assets([mid1, mid2, mid3], assets)  # type: ignore[arg-type]
    assert len(media) == 2
    assert media[0].url == "https://cdn.example/a.png"
    assert media[0].is_image is True
    assert media[1].url == "https://media.test/view?path=team/b.mp4"
    assert media[1].is_image is False
    assert media[1].filename == "b.mp4"


def test_text_render_includes_recent_posts_and_media():
    text = _report().to_text()
    assert "RECENT POSTS & MEDIA (7 days)" in text
    assert "linkedin:" in text
    assert "aaaaaaaa" in text
    assert "Ship faster with Cloudless" in text
    assert "https://www.linkedin.com/feed/update" in text
    assert "[img] https://cdn.example/api/v1/media/view?path=deploy.png" in text
    assert "▶ walkthrough.mp4" in text
    assert "Missing media" in text
    assert "instagram:" in text
    assert "▶ reel.mp4" in text
    # published time in Europe/Athens (UTC+3 in Sep)
    assert "EEST" in text or "03:00" in text or "13:30" in text


def test_html_render_includes_img_thumbnails_and_video_tile():
    html = _report().to_html()
    assert "Recent posts &amp; media (7 days)" in html
    assert "<h4" in html and "linkedin" in html
    assert 'src="https://cdn.example/api/v1/media/view?path=deploy.png"' in html
    assert "▶" in html
    assert "walkthrough.mp4" in html
    assert "Missing media" in html
    assert 'href="https://www.linkedin.com/feed/update/urn:li:share:1"' in html
    # section sits after platform pulse, before playbook
    pulse_i = html.index("Platform pulse")
    recent_i = html.index("Recent posts")
    playbook_i = html.index("Tomorrow's playbook")
    assert pulse_i < recent_i < playbook_i


def test_recent_section_empty_when_no_posts():
    r = _report(recent_posts_by_platform={})
    assert "No published posts in the last 7 days" in r.to_text()
    assert "No published posts in the last 7 days" in r.to_html()
