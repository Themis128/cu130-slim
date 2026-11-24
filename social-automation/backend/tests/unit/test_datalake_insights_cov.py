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
    for t in (
        "platform_focus",
        "lead_source",
        "ad_efficiency",
        "monetization_eta",
        "engagement_high",
        "email_deliverability",
        "content_topic",
        "audit_escalation",
        "price_signal",
        "objection_copy",
        "coaching_prime",
    ):
        assert t in types, t
    assert D.pick_actions({}) == []


def test_render_slack_full():
    lines = D.render_slack_lines(
        {
            "responses": 5,
            "lead_volume_30d": 3,
            "best_converting": "linkedin",
            "platform_funnels": {"linkedin": {"views": 10, "leads": 2}},
            "lead_sources": [("site", 4)],
            "ad_campaigns": {"c1": {"cpl": 10.0, "spend": 100.0, "leads": 10}},
            "growth": {"ig": {"gate": {"eta_days": 7, "gap": 50}, "followers": 10, "per_week": 5}},
            "platform_engagement": {"x": 0.05},
            "email_funnel": {"m": {"sent": 50, "open_rate": 0.3}},
            "audit_yes": ["a"],
            "pain_topics": [("cost", 2)],
            "price_anchor_median": 100.0,
            "coaching_latest": {"topic": "t", "homework_due": "d"},
            "top_objections": [("obj", 2)],
        }
    )
    blob = "\n".join(lines)
    for frag in (
        "best funnel",
        "leads from site",
        "ads: c1",
        "ig:",
        "engagement",
        "email m:",
        "audit requests",
        "pain:",
        "price anchor",
        "coaching:",
        "objection",
    ):
        assert frag in blob, frag


@pytest.mark.asyncio
async def test_build_and_apply_signals():
    from datetime import datetime

    responses = [({"q": "a"}, "t@e.st")]
    leads = [{"source": "site", "interest": "audit", "company_size": "1-10"}]
    funnel = [{"platform": "linkedin", "views": 10, "clicks": 3, "leads": 2}]
    ad = [{"campaign_name": "c1", "spend_eur": 100.0, "leads": 2, "impressions": 1000}]
    posts = [{"platform": "linkedin", "engagement_rate": 0.08, "post_id": "p1"}]
    foll = [
        {"platform": "linkedin", "followers": 10, "captured_at": datetime(2026, 1, 1)},
        {"platform": "linkedin", "followers": 20, "captured_at": datetime(2026, 1, 8)},
    ]
    email = [{"kind": "marketing", "status": "opened", "n": 7}]
    devapp = [{"meta_data": {"platform": "linkedin", "ok": True, "health": {"token_valid": True}}}]
    coaching = [{"occurred_at": datetime(2026, 1, 1), "meta_data": {"topic": "x", "homework_due": "y"}}]
    results = [_rows_result(r) for r in (responses, leads, funnel, ad, posts, foll, email, devapp, coaching, [])]
    db = SimpleNamespace(execute=AsyncMock(side_effect=results), add=lambda o: None, commit=AsyncMock())
    sig = await D.build_signals(db, "00000000-0000-0000-0000-000000000001")
    assert sig["team_id"] and sig["actions"] and sig["coaching_latest"]["topic"] == "x"
    db.execute = AsyncMock(side_effect=[_rows_result(r) for r in (responses, leads, funnel, ad, posts, foll, email, devapp, coaching, [])])
    out = await D.apply_signals(db, "00000000-0000-0000-0000-000000000001")
    assert out["team_id"] and db.commit.called


@pytest.mark.asyncio
async def test_build_signals_no_coaching():
    empty = [_rows_result([]) for _ in range(10)]
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
    assert out["price_anchor_median"] == 1000.0  # true median of [800, 1200]
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


def test_survey_pain_answer_stopwords_skipped():
    """Stopwords in free-text survey answers must not enter pain_topics —
    covers the stopword-filter branch in compute_survey_signals."""
    out = D.compute_survey_signals([{"answers": {"it_support": "that cost is the problem"}, "email": "a@b.c"}])
    words = [w for w, _ in out["pain_topics"]]
    assert "that" not in words and "cost" in words


def test_email_funnel_multi_kind_and_unsent():
    """A second campaign kind + an unsent kind cover the loop-back and
    the sent-guard falsy arc in compute_email_signals."""
    rows = [
        {"kind": "marketing", "status": "sent", "n": 10},
        {"kind": "news", "status": "sent", "n": 0},
    ]
    funnel = D.compute_email_signals(rows)["email_funnel"]
    assert funnel["marketing"]["sent"] == 10
    assert "open_rate" in funnel["marketing"]
    assert funnel["news"]["sent"] == 0 and "open_rate" not in funnel["news"]


def test_render_slack_negative_arcs():
    """Campaign without CPL, growth platform without a gate, and an unsent
    email kind cover the filter-false arcs in render_slack_lines."""
    lines = D.render_slack_lines(
        {
            "responses": 0,
            "lead_volume_30d": 0,
            "best_converting": "",
            "platform_funnels": {},
            "lead_sources": [],
            "ad_campaigns": {"c2": {"cpl": None, "spend": 5.0, "leads": 0}},
            "growth": {"tt": {"followers": 3}},
            "platform_engagement": {},
            "email_funnel": {"n": {"sent": 0}},
            "audit_yes": [],
            "pain_topics": [],
            "devapp_unhealthy": [],
            "devapp_expiring": [],
        }
    )
    blob = "\n".join(lines)
    assert "ads: c2" not in blob  # cpl None → skipped
    assert "tt:" not in blob  # no gate → skipped
    assert "email n:" not in blob  # sent 0 → skipped


@pytest.mark.asyncio
async def test_funnel_answer_model_fallback(monkeypatch):
    """Count-question over a summary JSON with no matching key and no lead
    in the question falls through to the DMR extract path."""
    import app.services.ai_functions as AF
    import app.services.minio_storage as MS

    monkeypatch.setattr(MS, "minio_enabled", lambda: True)
    body = json.dumps({"contacts": {"rows": 4}, "exported_at": "x"}).encode()
    monkeypatch.setattr(MS, "get_object", AsyncMock(return_value=body))
    monkeypatch.setattr(AF, "ai_extract", AsyncMock(return_value="4 contacts"))
    out = await D.funnel_answer("how many rows total")
    assert out["answer"] == "4 contacts"
    assert "rows_seen" in out


def test_render_slack_devapp_lines():
    """devapp_unhealthy/devapp_expiring entries cover the heal-line renders
    and the error/quota/username label arcs."""
    lines = D.render_slack_lines(
        {
            "responses": 0,
            "devapp_unhealthy": [
                {"platform": "x", "username": "me", "error": "401"},
                {"platform": "tiktok", "quota_exhausted": True},
                {"platform": "li"},
            ],
            "devapp_expiring": [{"platform": "fb", "username": "pg", "days_to_expiry": 3}],
        }
    )
    blob = "\n".join(lines)
    assert "devapp x/@me: 401" in blob
    assert "devapp tiktok: quota exhausted" in blob
    assert "devapp li: token invalid" in blob
    assert "devapp fb/@pg: token expires in 3d" in blob


def test_pick_actions_gated_content_topic_break():
    """When reward weights gate content_topic, the pain-topic loop breaks
    instead of re-proposing drafts (line ~371)."""
    signals = {
        "responses": 0,
        "pain_topics": [("cost", 5), ("speed", 3)],
        "action_weights": {"content_topic": {"n": 10, "avg_reward": 0.0}},
    }
    actions = D.pick_actions(signals)
    assert not any(a.get("type") == "content_topic" for a in actions)


@pytest.mark.asyncio
async def test_funnel_answer_malformed_summary_falls_through(monkeypatch):
    """Unparseable summary JSON hits the except-pass arc then the DMR path."""
    import app.services.ai_functions as AF
    import app.services.minio_storage as MS

    monkeypatch.setattr(MS, "minio_enabled", lambda: True)
    monkeypatch.setattr(MS, "get_object", AsyncMock(return_value=b"{not json"))
    monkeypatch.setattr(AF, "ai_extract", AsyncMock(return_value="NONE"))
    out = await D.funnel_answer("how many leads total")
    assert out["answer"] is None and "rows_seen" in out
