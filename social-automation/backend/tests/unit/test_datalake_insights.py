"""Unit tests for the datalake read-back loop's pure core."""
from __future__ import annotations

import app.services.datalake_insights as di


def _resp(**answers):
    return {"answers": answers}


class TestSurveySignals:
    def test_empty(self):
        s = di.compute_survey_signals([])
        assert s["responses"] == 0
        assert s["audit_yes"] == []
        assert s["price_anchor_median"] is None

    def test_audit_yes_collected(self):
        s = di.compute_survey_signals([
            _resp(audit_interest="Yes"),
            _resp(audit_interest="no"),
        ])
        assert s["responses"] == 2

    def test_price_anchor_median(self):
        s = di.compute_survey_signals([
            _resp(worth_month="500"),
            _resp(worth_month="300"),
            _resp(worth_month="800"),
        ])
        assert s["price_anchor_median"] == 500.0

    def test_pain_topics_extracted(self):
        s = di.compute_survey_signals([
            _resp(embarrass="my website is slow and the branding looks dated"),
            _resp(vendors="three different vendors never talk to each other"),
        ])
        topics = dict(s["pain_topics"])
        assert topics.get("website") == 1
        assert topics.get("vendors") == 1
        assert "the" not in topics  # stopword stripped


class TestPickActions:
    def test_audit_escalation_action(self):
        s2 = di.compute_survey_signals([])
        s2["audit_yes"] = ["a@b.c"]
        kinds2 = {a["type"] for a in di.pick_actions(s2)}
        assert "audit_escalation" in kinds2

    def test_content_topic_action(self):
        s = {"pain_topics": [("downtime", 4), ("branding", 2)], "audit_yes": [],
             "price_anchor_median": None, "top_objections": []}
        actions = di.pick_actions(s)
        assert actions[0]["type"] == "content_topic"
        assert actions[0]["topic"] == "downtime"

    def test_render_lines(self):
        s = {"responses": 3, "audit_yes": ["a@b.c"],
             "pain_topics": [("site", 2)],
             "price_anchor_median": 500.0, "top_objections": []}
        lines = di.render_slack_lines(s)
        assert any("audit" in ln for ln in lines)
        assert any("€500" in ln for ln in lines)


class TestFunnelSignals:
    def test_lead_signals(self):
        s = di.compute_lead_signals([
            {"source": "website", "interest": "audit", "company_size": "2-5"},
            {"source": "website", "interest": "cloud", "company_size": "2-5"},
            {"source": "telegram_dm", "interest": None, "company_size": None},
        ])
        assert s["lead_volume_30d"] == 3
        assert s["lead_sources"][0] == ("website", 2)

    def test_funnel_best_converting(self):
        s = di.compute_funnel_signals([
            {"platform": "ig", "views": 10, "clicks": 2, "leads": 0},
            {"platform": "linkedin", "views": 5, "clicks": 3, "leads": 2},
        ])
        assert s["best_converting"] == "linkedin"
        assert s["platform_funnels"]["linkedin"]["leads"] == 2

    def test_ad_cpl_flagged(self):
        s = di.compute_ad_signals([
            {"campaign": "exp", "spend_eur": 90.0, "leads": 2, "impressions": 1000},
        ])
        assert s["ad_campaigns"]["exp"]["cpl"] == 45.0
        acts = di.pick_actions({"ad_campaigns": s["ad_campaigns"]})
        assert any(a["type"] == "ad_efficiency" for a in acts)

    def test_empty_sources_safe(self):
        s = {}
        acts = di.pick_actions(s)
        assert acts == []
