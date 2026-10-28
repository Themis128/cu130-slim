"""Every strategy parse path shares the near-duplicate filter and the cap."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import app.services.inference as INF
import app.services.strategy_report as SR
from app.services.slack_digest import DigestReport

A1 = "Post a LinkedIn carousel at 09:00 Athens about audit wins."
A1_DUP = "Post a LinkedIn carousel at 09:00 Athens about audit wins again."
A2 = "Share an Instagram image at 12:30 Athens with a client quote."


def _digest():
    return DigestReport(
        generated_at=datetime(2026, 10, 17, 18, 0, tzinfo=UTC),
        timezone="Europe/Athens",
        team_name="T",
        days=1,
        overview={},
        impressions_24h=10,
        engagement_24h=3,
    )


_DISTINCT = [
    "Publish a LinkedIn carousel on cloud migration costs at 09:00",
    "Share an Instagram reel showing a website speed test at 12:30",
    "Reply to every open LinkedIn comment before lunch today",
    "Repurpose last week's best post into a TikTok video clip",
    "Email the newsletter list a free audit offer this afternoon",
    "Post a Threads poll asking which Cloudflare feature they use",
    "Update the Facebook page cover with the new hosting banner",
    "Pin the customer testimonial to the top of the X profile",
    "Write a LinkedIn article explaining managed DNS failover",
    "Schedule an Instagram story countdown for Friday's webinar",
]


def _many(n):
    return _DISTINCT[:n]


def test_finalize_dedupes_strips_and_caps():
    out = SR._finalize_actions([f' "{A1}" ', A1_DUP, "short", None, A2, *_many(10)])
    assert out[:2] == [A1, A2]
    assert len(out) == SR._MAX_ACTIONS


def test_json_array_path_dedupes():
    text = json.dumps([A1, {"action": A1_DUP}, A2])
    assert SR._parse_actions(text) == [A1, A2]


def test_json_array_path_caps():
    assert len(SR._parse_actions(json.dumps(_many(10)))) == SR._MAX_ACTIONS


def test_numbered_lines_path_dedupes():
    assert SR._parse_actions(f"1. {A1}\n2. {A1_DUP}\n3. {A2}") == [A1, A2]


@pytest.mark.asyncio
async def test_structured_actions_payload_dedupes(monkeypatch):
    resp = {"actions": [A1, {"text": A1_DUP}, {"action": A2}]}
    monkeypatch.setattr(INF, "call_inference", AsyncMock(return_value=resp))
    assert await SR._llm_actions(object(), "tid", {}, _digest()) == [A1, A2]


@pytest.mark.asyncio
async def test_structured_actions_payload_caps(monkeypatch):
    monkeypatch.setattr(INF, "call_inference", AsyncMock(return_value={"actions": _many(10)}))
    out = await SR._llm_actions(object(), "tid", {}, _digest())
    assert len(out) == SR._MAX_ACTIONS
