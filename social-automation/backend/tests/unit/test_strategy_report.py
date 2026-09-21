"""Unit tests for the daily strategy report — pure rendering/parsing only."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.strategy_report import (
    StrategyReport,
    _parse_actions,
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


def _report() -> StrategyReport:
    return StrategyReport(
        generated_at=NOW,
        timezone="Europe/Athens",
        team_name="Test Team",
        insights=INSIGHTS,
        actions=["Post a LinkedIn carousel at 09:00", "Cross-post to Threads"],
        llm_used=True,
    )


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
    assert "<li>Post a LinkedIn carousel" in html
    assert "above median" in html


def test_empty_report_still_renders():
    r = StrategyReport(generated_at=NOW, timezone="Europe/Athens", team_name="T")
    assert "publish consistently" in r.to_text()
    assert "<html>" in r.to_html()
