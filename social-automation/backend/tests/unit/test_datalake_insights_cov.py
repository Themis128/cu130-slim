"""Coverage for datalake_insights async boundaries + pick_actions branches."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.datalake_insights as D


def _rows_result(rows):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: rows),
        mappings=lambda: SimpleNamespace(all=lambda: rows),
        all=lambda: rows,
    )


def test_pick_actions_all_branches():
    sig = {
        "best_converting": "linkedin",
        "lead_sources": [("site", 9), ("referral", 3), ("other", 1)],
        "ad_campaigns": {"camp1": {"cpl": 50.0, "spend": 500, "leads": 10}, "camp2": {"cpl": None}},
        "growth": {"tiktok": {"gate": {"eta_days": 30, "gap": 100}}, "ig": {"gate": {"eta_days": None}}},
        "platform_engagement": {"linkedin": 0.09, "x": 0.01},
        "email_funnel": {"marketing": {"sent": 100, "open_rate": 0.1}, "news": {"sent": 0}},
        "pain_topics": [("cost", 5), ("slow", 3), ("security", 2), ("misc", 1)],
        "audit_yes": ["a@b.c"],
        "price_anchor_median": 900,
        "top_objections": [("too expensive", 4), ("no time", 2), ("other", 1)],
        "coaching_latest": {"topic": "offers", "homework_due": "friday"},
    }
    types = [a["type"] for a in D.pick_actions(sig)]
    for t in ("platform_focus", "lead_source", "ad_efficiency", "monetization_eta",
              "engagement_high", "email_deliverability", "content_topic",
              "audit_escalation", "price_signal", "objection_copy", "coaching_prime"):
        assert t in types, t
    assert D.pick_actions({}) == []


def test_render_slack_full():
    lines = D.render_slack_lines({
        "responses": 5, "lead_volume_30d": 3, "best_converting": "linkedin",
        "platform_funnels": {"linkedin": {"views": 10, "leads": 2}},
        "lead_sources": [("site", 4)],
        "ad_campaigns": {"c1": {"cpl": 10.0, "spend": 100.0, "leads": 10}},
        "growth": {"ig": {"gate": {"eta_days": 7, "gap": 50}, "followers": 10, "per_week": 5}},
        "platform_engagement": {"x": 0.05},
        "email_funnel": {"m": {"sent": 50, "open_rate": 0.3}},
        "audit_yes": ["a"], "pain_topics": [("cost", 2)],
        "price_anchor_median": 100.0, "coaching_latest": {"topic": "t", "homework_due": "d"},
        "top_objections": [("obj", 2)],
    })
    blob = "\n".join(lines)
    for frag in ("best funnel", "leads from site", "ads: c1", "ig:", "engagement",
                 "email m:", "audit requests", "pain:", "price anchor", "coaching:", "objection"):
        assert frag in blob, frag


@pytest.mark.asyncio
async def test_build_and_apply_signals():
    from datetime import datetime
    responses = [{"q": "a"}]
    leads = [{"source": "site", "interest": "audit", "company_size": "1-10"}]
    funnel = [{"platform": "linkedin", "views": 10, "clicks": 3, "leads": 2}]
    ad = [{"campaign_name": "c1", "spend_eur": 100.0, "leads": 2, "impressions": 1000}]
    posts = [{"platform": "linkedin", "engagement_rate": 0.08, "post_id": "p1"}]
    foll = [{"platform": "linkedin", "followers": 10, "captured_at": datetime(2026, 1, 1)},
            {"platform": "linkedin", "followers": 20, "captured_at": datetime(2026, 1, 8)}]
    email = [{"kind": "marketing", "status": "opened", "n": 7}]
    coaching = [{"occurred_at": datetime(2026, 1, 1), "meta_data": {"topic": "x", "homework_due": "y"}}]
    results = [_rows_result(r) for r in (responses, leads, funnel, ad, posts, foll, email, coaching)]
    db = SimpleNamespace(execute=AsyncMock(side_effect=results), add=lambda o: None, commit=AsyncMock())
    sig = await D.build_signals(db, "00000000-0000-0000-0000-000000000001")
    assert sig["team_id"] and sig["actions"] and sig["coaching_latest"]["topic"] == "x"
    db.execute = AsyncMock(side_effect=[_rows_result(r) for r in (responses, leads, funnel, ad, posts, foll, email, coaching)])
    out = await D.apply_signals(db, "00000000-0000-0000-0000-000000000001")
    assert out["team_id"] and db.commit.called


@pytest.mark.asyncio
async def test_build_signals_no_coaching():
    empty = [_rows_result([]) for _ in range(8)]
    db = SimpleNamespace(execute=AsyncMock(side_effect=empty))
    sig = await D.build_signals(db, "00000000-0000-0000-0000-000000000002")
    assert "coaching_latest" not in sig and sig["actions"] == []


@pytest.mark.asyncio
async def test_funnel_answer_offline(monkeypatch):
    import app.services.minio_storage as MS
    monkeypatch.setattr(MS, "minio_enabled", lambda: False)
    out = await D.funnel_answer("how many leads")
    assert out["error"] == "datalake offline"


@pytest.mark.asyncio
async def test_funnel_answer_paths(monkeypatch):
    import app.services.ai_functions as AF
    import app.services.minio_storage as MS
    monkeypatch.setattr(MS, "minio_enabled", lambda: True)
    summary = json.dumps({"leads": {"rows": 42}, "exported_at": "x"})

    async def fake_get(key):
        if key == "funnel/summary.json":
            return summary.encode()
        raise Exception("no key")

    monkeypatch.setattr(MS, "get_object", fake_get)
    out = await D.funnel_answer("how many leads this month")
    assert out["answer"] == "42" and out["via"] == "summary"

    monkeypatch.setattr(AF, "ai_extract", AsyncMock(return_value="an answer"))
    out = await D.funnel_answer("what changed?")
    assert out["answer"] == "an answer" and out["rows_seen"] >= 1
    monkeypatch.setattr(AF, "ai_extract", AsyncMock(return_value="NONE"))
    assert (await D.funnel_answer("anything"))["answer"] is None

    async def empty_get(key):
        raise Exception("no")

    monkeypatch.setattr(MS, "get_object", empty_get)
    assert (await D.funnel_answer("q"))["error"] == "funnel zone empty"


def test_survey_signals_full_answers():
    responses = [
        {
            "answers": {
                "audit_interest": "YES",
                "worth_monthly": "about €1200",
                "hesitate_main": "too expensive",
                "it_support": "slow vendors and hidden cost",
            },
            "email": "A@B.C",
        },
        {"answers": {"worth_monthly": "800", "objection_x": "no time"}},
    ]
    out = D.compute_survey_signals(responses)
    assert out["audit_yes"] == ["a@b.c"]
    assert out["price_anchor_median"] == 1200.0
    assert ("too expensive", 1) in out["top_objections"]
    assert any(w == "vendors" or w == "hidden" for w, _ in out["pain_topics"])


def test_growth_gate_and_single_snap():
    from datetime import datetime
    snaps = [
        {"platform": "facebook", "followers": 100, "captured_at": datetime(2026, 1, 1)},
        {"platform": "facebook", "followers": 200, "captured_at": datetime(2026, 1, 15)},
        {"platform": "ig", "followers": 50, "captured_at": datetime(2026, 1, 1)},
    ]
    g = D.compute_growth_signals(snaps)["growth"]
    assert g["facebook"]["gate"]["threshold"] == 500 and g["facebook"]["gate"]["gap"] == 300
    assert g["facebook"]["gate"]["eta_days"] is not None
    assert g["ig"]["per_week"] is None


def test_email_statuses_and_rate():
    rows = [
        {"kind": "marketing", "status": "clicked", "n": 3},
        {"kind": "marketing", "status": "responded", "n": 2},
        {"kind": "marketing", "status": "sent", "n": 10},
    ]
    d = D.compute_email_signals(rows)["email_funnel"]["marketing"]
    assert d["sent"] == 13 and d["clicked"] == 3 and d["responses"] == 2
    assert "open_rate" in d


@pytest.mark.asyncio
async def test_funnel_answer_lead_fallback(monkeypatch):
    import app.services.minio_storage as MS
    monkeypatch.setattr(MS, "minio_enabled", lambda: True)
    body = json.dumps({"leads": {"rows": 9}, "exported_at": "x"}).encode()
    monkeypatch.setattr(MS, "get_object", AsyncMock(return_value=body))
    out = await D.funnel_answer("how many lead forms came in")
    assert out["answer"] == "9"
