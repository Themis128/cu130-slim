"""Tests for app/services/monetization_report.py + monetization.py updates."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import app.services.monetization_report as R
from app.services.monetization import evaluate_program


def _gate(platform="facebook", account_type="user", followers=66):
    return R.AccountGate(
        platform=platform,
        handle="@test",
        account_type=account_type,
        followers=followers,
        gain_per_day=0.5,
        followers_live=False,
        program=evaluate_program(
            platform,
            {"followers": followers, "days_held": 0},
            account_type=account_type,
        ),
        funnel_note="funnel" if platform in ("linkedin", "threads") else "",
    )


# ── X program: Original Content Rewards (Revenue Sharing retired 2026-09) ──


def test_twitter_program_is_original_content_rewards():
    rep = evaluate_program("twitter", {"followers": 600, "impressions_90d": 600_000})
    assert rep is not None
    assert rep.program == "X Original Content Rewards"
    assert any("verified followers" in c.name for c in rep.criteria)
    imp = next(c for c in rep.criteria if "impressions" in c.name)
    assert imp.target == 500_000
    assert imp.met is True


def test_twitter_below_gate_not_met():
    rep = evaluate_program("twitter", {"followers": 100, "impressions_90d": 0})
    assert rep is not None
    assert rep.monetizable is False


# ── _llm_strategy output normalization ────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_strategy_parses_structured_actions(monkeypatch):
    async def fake_infer(prompt, **kw):
        return {
            "sections": [
                {"platform": "facebook", "headline": "Grow to 500", "strategy": "Post raw build logs daily."},
                {"platform": "", "headline": "bad", "strategy": ""},  # dropped
            ],
            "actions": [
                {"platform": "x", "time_local": "9:30", "format": "image", "instruction": "Post the build log"},
                {"platform": "LinkedIn", "time_local": "27:99", "format": "essay", "instruction": "Corp post"},
                {"platform": "instagram", "instruction": ""},  # dropped — no instruction
            ],
        }

    monkeypatch.setattr("app.services.inference.call_inference", AsyncMock(side_effect=fake_infer))
    sections, actions = await R._llm_strategy(None, None, [], {}, {})
    assert len(sections) == 1
    assert [a.platform for a in actions] == ["twitter", "linkedin"]
    assert actions[0].time_athens == "09:30"
    assert actions[0].format == "image"
    assert actions[1].time_athens == ""  # invalid time rejected
    assert actions[1].format == ""  # unknown format rejected


@pytest.mark.asyncio
async def test_llm_strategy_inference_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.inference.call_inference",
        AsyncMock(side_effect=RuntimeError("dmr down")),
    )
    sections, actions = await R._llm_strategy(None, None, [], {}, {})
    assert sections == [] and actions == []


# ── rule fallback ─────────────────────────────────────────────────────────


def test_rule_actions_targets_fb_growth_and_li_funnel():
    gates = [_gate("facebook", "user"), _gate("linkedin", "organization")]
    actions = R._rule_actions(gates)
    plats = [a.platform for a in actions]
    assert "facebook" in plats and "linkedin" in plats
    fb = next(a for a in actions if a.platform == "facebook")
    assert "Stars" in fb.instruction or "500" in fb.instruction


def test_rule_actions_empty_when_no_gates():
    assert R._rule_actions([]) == []


# ── gate gathering shape ─────────────────────────────────────────────────


def test_compact_gates_marks_funnel_and_unmet():
    gates = [_gate("facebook", "user", followers=66), _gate("linkedin", "organization")]
    compact = R._compact_gates(gates)
    fb = next(g for g in compact if g["platform"] == "facebook")
    li = next(g for g in compact if g["platform"] == "linkedin")
    assert fb["followers"] == 66
    assert "followers ≥ 500" in fb["unmet"]
    assert li["role"] == "funnel" and "program" not in li


# ── report rendering ──────────────────────────────────────────────────────


def test_report_renders_text_and_html():
    rep = R.MonetizationReport(
        generated_at=datetime.now(UTC),
        timezone="Europe/Athens",
        team_name="Cloudless",
        gates=[_gate("facebook", "user")],
        telegram_members=42,
        telegram_leads_7d=3,
        actions=[R.MonetizationAction("facebook", "post build log", "12:00", "image")],
        scheduled=[{"platform": "facebook", "status": "scheduled", "scheduled_at": "2026-10-14 12:00 EEST", "instruction": "post build log"}],
        llm_used=True,
    )
    text = rep.to_text()
    html = rep.to_html()
    assert "MONETIZATION REPORT" in text
    assert "channel members: 42" in text
    assert "post build log" in text
    assert "Program gates" in html and "Actions executed" in html
