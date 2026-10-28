"""Structured-reply handling in the strategy report (#676).

Providers return the strategist reply as think-residue text, JSON arrays of
strings/objects, or a structured {"actions": ...}/{"content": ...} payload.
These tests pin each shape to the actions the automation plan receives.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import app.services.inference as INF
import app.services.strategy_report as SR
from app.services.slack_digest import DigestReport

A1 = "Post a LinkedIn carousel at 09:00 Athens about audit wins."
A2 = "Share an Instagram image at 12:30 Athens with a client quote."


def _digest():
    return DigestReport(
        generated_at=datetime(2026, 10, 17, 18, 0, tzinfo=UTC), timezone="Europe/Athens",
        team_name="T", days=1, overview={}, impressions_24h=10, engagement_24h=3,
    )


async def _run(monkeypatch, resp):
    monkeypatch.setattr(INF, "call_inference", AsyncMock(return_value=resp))
    return await SR._llm_actions(object(), "tid", {}, _digest())


class TestParseActions:
    def test_think_residue_dropped(self):
        text = "<think>plan: maybe 1. Ignore this draft line here</think>\n1. " + A1
        assert SR._parse_actions(text) == [A1]

    def test_json_array_mixed_entries(self):
        text = json.dumps([
            A1,
            12345678901234567890,                       # numeric → str, long enough
            {"action": A2},
            {"text": "Reply to every LinkedIn comment within the hour."},
            {"note": "short", "detail": "Repurpose the top LinkedIn post as a TikTok video."},
            {"x": 1},                                    # no string field → dropped
            ["nested"],                                  # unsupported → ignored
            "too short",
        ])
        assert SR._parse_actions(text) == [
            A1,
            "12345678901234567890",
            A2,
            "Reply to every LinkedIn comment within the hour.",
            "Repurpose the top LinkedIn post as a TikTok video.",
        ]

    def test_invalid_json_falls_back_to_lines(self):
        text = "[not json\n1. " + A1
        assert SR._parse_actions(text) == [A1]

    def test_json_object_not_list_falls_back(self):
        assert SR._parse_actions('["a"]') == []  # list but nothing long enough

    def test_python_repr_list_single_quoted_dicts(self):
        # Production failure (2026-10-19/24/27 briefs): the model answered
        # with a Python-repr list of dicts — single quotes — which
        # json.loads rejects. literal_eval must recover the actions.
        text = (
            "[{'platform': 'Threads', 'time': '09:00', 'format': 'Image', "
            "'reason': 'Media posts beat text on Threads with a better rate.'},"
            " {'platform': 'LinkedIn', 'time': '10:00', 'format': 'Carousel', "
            "'reason': 'Carousels drive the strongest LinkedIn engagement.'}]"
        )
        out = SR._parse_actions(text)
        assert out == [
            "Threads - 09:00 - Image - Media posts beat text on Threads with a better rate.",
            "LinkedIn - 10:00 - Carousel - Carousels drive the strongest LinkedIn engagement.",
        ]

    def test_actions_object_in_text(self):
        # {"actions": [...]} returned as bare text must still parse.
        text = json.dumps({"actions": [A1, A2]})
        assert SR._parse_actions(text) == [A1, A2]

    def test_python_repr_list_recomposed_items_feed_playbook_parser(self):
        # The composed "Platform - HH:MM - Format - reason" line must give
        # _parse_playbook_item a routable platform/time/format.
        text = (
            "[{'platform': 'TikTok', 'time': '20:00', 'format': 'video', "
            "'reason': 'Repurpose the top LinkedIn post into a short clip.'}]"
        )
        out = SR._parse_actions(text)
        item = SR._parse_playbook_item(out[0])
        assert item.platform == "tiktok"
        assert item.time_athens == "20:00"
        assert item.format == "video"

    def test_caps_at_max_actions(self):
        # Distinct actions: near-duplicates are now collapsed before the cap.
        many = json.dumps([
            "Publish a LinkedIn carousel on cloud migration costs at 09:00",
            "Share an Instagram reel showing a website speed test at 12:30",
            "Reply to every open LinkedIn comment before lunch today",
            "Repurpose last week's best post into a TikTok video clip",
            "Email the newsletter list a free audit offer this afternoon",
            "Post a Threads poll asking which Cloudflare feature they use",
            "Update the Facebook page cover with the new hosting banner",
            "Pin the customer testimonial to the top of the X profile",
        ])
        assert len(SR._parse_actions(many)) == SR._MAX_ACTIONS


class TestStructuredPayload:
    @pytest.mark.asyncio
    async def test_actions_list_of_mixed_shapes(self, monkeypatch):
        out = await _run(monkeypatch, {"actions": [
            A1,
            {"instruction": A2},
            {"foo": "Longest string field wins for unknown schema objects."},
            {"action": ""},           # empty → longest string ("" → dropped)
            None,                     # unsupported type → dropped
            "short",
        ]})
        assert out == [A1, A2, "Longest string field wins for unknown schema objects."]

    @pytest.mark.asyncio
    async def test_actions_list_without_usable_entries(self, monkeypatch):
        assert await _run(monkeypatch, {"actions": ["no", {"k": 1}]}) is None

    @pytest.mark.asyncio
    async def test_actions_string_holds_numbered_list(self, monkeypatch):
        out = await _run(monkeypatch, {"actions": f"1. {A1}\n2. {A2}"})
        assert out == [A1, A2]

    @pytest.mark.asyncio
    async def test_content_key_fallback(self, monkeypatch):
        assert await _run(monkeypatch, {"content": f"- {A2}"}) == [A2]

    @pytest.mark.asyncio
    async def test_no_text_anywhere(self, monkeypatch):
        assert await _run(monkeypatch, {"provider": "dmr", "usage": {}}) is None

    @pytest.mark.asyncio
    async def test_non_dict_reply(self, monkeypatch):
        assert await _run(monkeypatch, None) is None


@pytest.mark.asyncio
async def test_execute_report_actions_converts_playbook_to_executor_calls(monkeypatch):
    """The playbook strings are parsed (platform/time/format) and handed to
    the shared executor tagged as strategy-brief with tomorrow's date."""
    import app.services.monetization_executor as X
    from app.models.user import Team

    seen = {}

    async def _sched(db, team, actions, report_date, **kw):
        seen["actions"] = actions
        seen["report_date"] = report_date
        seen["kw"] = kw
        return [{"platform": a.platform, "status": "scheduled"} for a in actions]

    monkeypatch.setattr(X, "schedule_report_actions", _sched)

    report = SR.StrategyReport(
        generated_at=datetime(2026, 10, 27, 7, 0, tzinfo=UTC),
        timezone="Europe/Athens",
        team_name="T",
        actions=[
            "1. LinkedIn - 09:30 - carousel - recap the audit-win post",
            "2. TikTok - 20:00 - video - repurpose the top story",
            "3. Reply to all open comments before lunch today",
        ],
        insights={"platforms": {"linkedin": {"best_hour_athens": (9, 0.4)}}},
    )
    team = Team()
    team.id = "00000000-0000-0000-0000-000000000001"
    out = await SR.execute_report_actions(object(), team, report)

    # The platform-less action is dropped; the other two convert with
    # platform + Athens time + format intact.
    assert len(out) == 2
    acts = seen["actions"]
    assert (acts[0].platform, acts[0].time_athens, acts[0].format) == (
        "linkedin", "09:30", "carousel")
    assert (acts[1].platform, acts[1].time_athens, acts[1].format) == (
        "tiktok", "20:00", "video")
    assert seen["kw"]["source"] == "strategy-brief"
    assert seen["kw"]["dedup_field"] == "strategy_action"
    assert seen["kw"]["target_date"].isoformat() == "2026-10-28"  # Athens +1d
    assert seen["kw"]["learned_hours"] == {"linkedin": 9}
    assert seen["report_date"] == "2026-10-27"  # Athens date of the brief
    assert report.scheduled == out


def test_scheduled_results_render_in_text_and_html():
    """QUEUED BY THE PLATFORM block covers text + html scheduled output."""
    report = SR.StrategyReport(
        generated_at=datetime(2026, 10, 27, 7, 0, tzinfo=UTC),
        timezone="Europe/Athens", team_name="T",
        actions=[A1],
        scheduled=[
            {"platform": "linkedin", "status": "scheduled",
             "scheduled_at": "2026-10-28 09:30 EET", "reason": None},
            {"platform": "tiktok", "status": "drafted",
             "scheduled_at": None, "reason": "video format — left as draft"},
        ],
    )
    text = report.to_text()
    assert "QUEUED BY THE PLATFORM" in text
    assert "linkedin" in text and "2026-10-28 09:30 EET" in text
    assert "video format" in text
    html = report.to_html()
    assert "Queued by the platform" in html
    assert "drafted" in html and "linkedin" in html


@pytest.mark.asyncio
async def test_execute_report_actions_no_actions_noop():
    from app.models.user import Team

    report = SR.StrategyReport(
        generated_at=datetime(2026, 10, 27, 7, 0, tzinfo=UTC),
        timezone="Europe/Athens", team_name="T",
    )
    assert await SR.execute_report_actions(object(), Team(), report) == []


@pytest.mark.asyncio
async def test_llm_actions_uses_brain_learnings(monkeypatch):
    """When the company journal has recent outcomes, the learnings block is
    injected into the strategist prompt."""
    import app.services.company_brain as cb

    async def _recall(team_id, query, n_results=4):
        return {
            "hits": [
                {"text": "posted X at 09:00 → +40% reach", "day": "2026-10-21", "kind": "daily-journal"},
                {"text": "reels flopped twice", "day": None, "kind": "daily-journal"},
            ],
            "gap": None,
        }

    monkeypatch.setattr(cb, "recall_cited", _recall)
    seen = {}

    async def _inf(prompt, **kw):
        seen["prompt"] = prompt
        return {"text": "1. " + A1}

    monkeypatch.setattr(INF, "call_inference", _inf)
    out = await SR._llm_actions(object(), "tid", {}, _digest())
    assert out == [A1]
    assert "Recent outcomes from the company journal" in seen["prompt"]
    assert "+40% reach" in seen["prompt"]
    assert "2026-10-21" in seen["prompt"]  # cited date preserved


@pytest.mark.asyncio
async def test_llm_actions_brain_gap_named_in_prompt(monkeypatch):
    """When recall has no evidence, the gap reason is stated — the strategist
    must not assume a history that isn't there."""
    import app.services.company_brain as cb

    async def _recall(team_id, query, n_results=4):
        return {"hits": [], "gap": "no brain collection — journal has never run"}

    monkeypatch.setattr(cb, "recall_cited", _recall)
    seen = {}

    async def _inf(prompt, **kw):
        seen["prompt"] = prompt
        return {"text": "1. " + A2}

    monkeypatch.setattr(INF, "call_inference", _inf)
    out = await SR._llm_actions(object(), "tid", {}, _digest())
    assert out == [A2]
    assert "no brain collection" in seen["prompt"]
    assert "Recent outcomes" not in seen["prompt"]
