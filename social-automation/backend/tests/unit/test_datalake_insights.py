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
