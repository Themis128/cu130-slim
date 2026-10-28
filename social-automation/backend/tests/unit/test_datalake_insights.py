"""Coverage: app.services.datalake_insights pure compute/pick/render fns."""
from datetime import UTC, datetime, timedelta

import app.services.datalake_insights as D


def test_survey_signals_full():
    responses = [
        {"email": "A@x.com", "answers": {
            "audit_interest": "Yes",
            "worth_per_month": "500",
            "hesitate_why": "too pricey",
            "embarrass": "manual server maintenance overhead"}},
        {"email": "b@y.com", "answers": {
            "audit_interest": "No",
            "worth_per_month": "900",
            "sell_online_barrier": "shipping logistics problems"}},
    ]
    out = D.compute_survey_signals(responses)
    assert out["responses"] == 2
    assert out["audit_yes"] == ["a@x.com"]
    assert out["price_anchor_median"] == 900.0 or out["price_anchor_median"] in (500.0, 700.0)
    assert out["top_objections"][0][0] == "too pricey"
    assert any(w == "maintenance" for w, _ in out["pain_topics"])


def test_survey_signals_empty():
    out = D.compute_survey_signals([])
    assert out["responses"] == 0 and out["price_anchor_median"] is None


def test_lead_signals():
    leads = [
        {"source": "linkedin", "interest": "cloud", "company_size": "1-10"},
        {"source": "linkedin", "interest": "cloud"},
        {"source": None},
    ]
    out = D.compute_lead_signals(leads)
    assert out["lead_volume_30d"] == 3
    assert ("linkedin", 2) in out["lead_sources"]
    assert ("unknown", 1) in out["lead_sources"]


def test_funnel_signals():
    rows = [
        {"platform": "linkedin", "views": 10, "clicks": 3, "leads": 2},
        {"platform": "x", "views": 100, "clicks": 1, "leads": 0},
        {"platform": "linkedin", "views": 5, "clicks": 1, "leads": 1},
    ]
    out = D.compute_funnel_signals(rows)
    assert out["best_converting"] == "linkedin"
    assert out["platform_funnels"]["linkedin"]["leads"] == 3
    assert D.compute_funnel_signals([])["best_converting"] is None


def test_ad_signals_cpl():
    daily = [
        {"campaign": "c1", "spend_eur": 100.0, "leads": 4, "impressions": 1000},
        {"campaign": "c1", "spend_eur": 50.0, "leads": 1, "impressions": 500},
        {"campaign": "c2", "spend_eur": 20.0, "leads": 0, "impressions": 100},
    ]
    out = D.compute_ad_signals(daily)
    assert out["ad_campaigns"]["c1"]["cpl"] == 30.0
    assert out["ad_campaigns"]["c2"]["cpl"] is None


def test_engagement_signals():
    rows = [
        {"platform": "tiktok", "engagement_rate": 0.1, "post_id": "p1"},
        {"platform": "tiktok", "engagement_rate": 0.3, "post_id": "p2"},
        {"platform": "x", "engagement_rate": 0.05, "post_id": "p3"},
    ]
    out = D.compute_engagement_signals(rows)
    assert out["platform_engagement"]["tiktok"] == 0.2
    assert out["top_post"]["post_id"] == "p2"
    assert D.compute_engagement_signals([])["top_post"] is None


def test_growth_signals_velocity_and_single_snap():
    now = datetime.now(UTC)
    snaps = [
        {"platform": "threads", "followers": 100, "captured_at": now - timedelta(days=14)},
        {"platform": "threads", "followers": 114, "captured_at": now},
        {"platform": "x", "followers": 500, "captured_at": now},
    ]
    out = D.compute_growth_signals(snaps)
    assert out["growth"]["threads"]["per_week"] == 7.0
    assert out["growth"]["x"]["per_week"] is None


def test_email_signals_funnel():
    rows = [
        {"kind": "survey", "status": "sent", "n": 10},
        {"kind": "survey", "status": "opened", "n": 4},
        {"kind": "survey", "status": "clicked", "n": 1},
        {"kind": "survey", "status": "responded", "n": 2},
        {"kind": None, "status": "delivered", "n": 5},
    ]
    out = D.compute_email_signals(rows)
    d = out["email_funnel"]["survey"]
    assert d["sent"] == 15 and d["opened"] == 4 and d["responses"] == 2
    assert d["open_rate"] == 0.267


def test_pick_actions_branches():
    signals = {
        "best_converting": "linkedin",
        "lead_sources": [("linkedin", 7)],
        "ad_campaigns": {"c1": {"spend": 100.0, "leads": 2, "cpl": 50.0},
                         "c2": {"spend": 10.0, "leads": 5, "cpl": 2.0}},
        "growth": {"threads": {"gate": {"gap": 50, "eta_days": 10}}},
        "platform_engagement": {"tiktok": 0.08, "x": 0.01},
        "email_funnel": {"survey": {"sent": 10, "open_rate": 0.1}},
        "pain_topics": [("servers", 5)],
        "audit_yes": ["a@x.com"],
        "price_anchor_median": 800.0,
        "top_objections": [("too pricey", 3)],
    }
    acts = D.pick_actions(signals)
    types = {a["type"] for a in acts}
    assert {"platform_focus", "lead_source", "ad_efficiency", "monetization_eta",
            "engagement_high", "email_deliverability", "content_topic",
            "audit_escalation", "price_signal", "objection_copy"} <= types
    assert all("reason" in a for a in acts)


def test_render_slack_lines():
    signals = {
        "responses": 2, "lead_volume_30d": 5,
        "best_converting": "linkedin",
        "platform_funnels": {"linkedin": {"views": 9, "leads": 3}},
        "lead_sources": [("linkedin", 5)],
        "ad_campaigns": {"c1": {"spend": 40.0, "leads": 2, "cpl": 20.0}},
        "growth": {"threads": {"followers": 100, "per_week": 7.0,
                               "gate": {"gap": 50, "eta_days": 10}}},
        "platform_engagement": {"tiktok": 0.1},
        "email_funnel": {"survey": {"sent": 10, "open_rate": 0.4, "responses": 1}},
    }
    lines = D.render_slack_lines(signals)
    assert any("datalake signals" in ln for ln in lines)
    assert any("best funnel" in ln for ln in lines)
    assert len(lines) >= 6
