"""Coverage: app.services.datalake_insights pure compute/pick/render fns."""

from datetime import UTC, datetime, timedelta

import app.services.datalake_insights as D


def test_survey_signals_full():
    responses = [
        {
            "email": "A@x.com",
            "answers": {"audit_interest": "Yes", "worth_per_month": "500", "hesitate_why": "too pricey", "embarrass": "manual server maintenance overhead"},
        },
        {"email": "b@y.com", "answers": {"audit_interest": "No", "worth_per_month": "900", "sell_online_barrier": "shipping logistics problems"}},
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
        "ad_campaigns": {"c1": {"spend": 100.0, "leads": 2, "cpl": 50.0}, "c2": {"spend": 10.0, "leads": 5, "cpl": 2.0}},
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
    assert {
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
    } <= types
    assert all("reason" in a for a in acts)


def test_render_slack_lines():
    signals = {
        "responses": 2,
        "lead_volume_30d": 5,
        "best_converting": "linkedin",
        "platform_funnels": {"linkedin": {"views": 9, "leads": 3}},
        "lead_sources": [("linkedin", 5)],
        "ad_campaigns": {"c1": {"spend": 40.0, "leads": 2, "cpl": 20.0}},
        "growth": {"threads": {"followers": 100, "per_week": 7.0, "gate": {"gap": 50, "eta_days": 10}}},
        "platform_engagement": {"tiktok": 0.1},
        "email_funnel": {"survey": {"sent": 10, "open_rate": 0.4, "responses": 1}},
    }
    lines = D.render_slack_lines(signals)
    assert any("datalake signals" in ln for ln in lines)
    assert any("best funnel" in ln for ln in lines)
    assert len(lines) >= 6


def test_engagement_signals_dedups_snapshot_rows():
    # The same post captured 3x must weigh once (latest capture), not 3x.
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    rows = [
        {"platform": "linkedin", "engagement_rate": 0.10, "post_id": "i1", "platform_post_id": "p1", "captured_at": t0},
        {"platform": "linkedin", "engagement_rate": 0.20, "post_id": "i1", "platform_post_id": "p1", "captured_at": t0 + timedelta(days=1)},
        {"platform": "linkedin", "engagement_rate": 0.30, "post_id": "i1", "platform_post_id": "p1", "captured_at": t0 + timedelta(days=2)},
        {"platform": "linkedin", "engagement_rate": 0.10, "post_id": "i2", "platform_post_id": "p2", "captured_at": t0},
        # same internal post_id published to threads — its own row, not a dup
        {"platform": "threads", "engagement_rate": 0.40, "post_id": "i1", "platform_post_id": "tp1", "captured_at": t0},
        # orphan snapshot: no internal post link, deduped on platform_post_id
        {"platform": "linkedin", "engagement_rate": 0.05, "post_id": "", "platform_post_id": "p3", "captured_at": t0},
        {"platform": "linkedin", "engagement_rate": 0.50, "post_id": "", "platform_post_id": "p3", "captured_at": t0 + timedelta(days=3)},
    ]
    out = D.compute_engagement_signals(rows)
    # linkedin: latest p1=0.30, p2=0.10, p3=0.50 → mean 0.3
    assert out["platform_engagement"]["linkedin"] == 0.3
    assert out["platform_posts"]["linkedin"] == 3
    assert out["platform_posts"]["threads"] == 1
    assert out["top_post"]["platform"] == "linkedin"


def test_growth_signals_drops_bogus_spike_and_marks_stalled():
    now = datetime.now(UTC)
    snaps = [
        {"platform": "instagram", "followers": 1000, "captured_at": now - timedelta(days=20)},
        {"platform": "instagram", "followers": 40000, "captured_at": now - timedelta(days=15)},  # metric mix-up
        {"platform": "instagram", "followers": 1010, "captured_at": now - timedelta(days=10)},
        {"platform": "instagram", "followers": 1015, "captured_at": now - timedelta(days=5)},
        {"platform": "instagram", "followers": 1015, "captured_at": now},
    ]
    out = D.compute_growth_signals(snaps)
    g = out["growth"]["instagram"]
    assert g["cleaned_readings"] == 1  # the 40k spike dropped
    assert g["followers"] == 1015
    # cleaned series grows 1000→1015 over 20d ≈ 5/wk — real modest
    # velocity, not the ~2800/wk the spike would have faked
    assert g["per_week"] is not None and 0 < g["per_week"] < 10


def test_growth_signals_short_span_no_fake_velocity():
    now = datetime.now(UTC)
    snaps = [
        {"platform": "x", "followers": 100, "captured_at": now - timedelta(days=1)},
        {"platform": "x", "followers": 150, "captured_at": now},
    ]
    out = D.compute_growth_signals(snaps)
    g = out["growth"]["x"]
    # 1-day span ×7 = fake 350/wk — must be suppressed
    assert g["per_week"] is None
    assert g["followers"] == 150


def test_survey_price_anchor_filters_years():
    responses = [
        {"email": "a@x.com", "answers": {"worth_per_month": "maybe 500 since 2026"}},
        {"email": "b@y.com", "answers": {"worth_per_month": "900"}},
    ]
    out = D.compute_survey_signals(responses)
    assert out["price_anchor_median"] == 700.0  # median(500, 900), 2026 excluded


def test_email_signals_click_and_response_rates():
    rows = [
        {"kind": "survey", "status": "sent", "n": 10},
        {"kind": "survey", "status": "opened", "n": 4},
        {"kind": "survey", "status": "clicked", "n": 2},
        {"kind": "survey", "status": "responded", "n": 1},
    ]
    # opened/clicked rows are also sends in this funnel model: sent = 10+4+2 = 16
    d = D.compute_email_signals(rows)["email_funnel"]["survey"]
    assert d["sent"] == 16
    assert d["open_rate"] == 0.25
    assert d["click_rate"] == 0.125
    assert d["response_rate"] == 0.062


def test_ad_signals_ctr_cpc():
    daily = [
        {"campaign": "c1", "spend_eur": 100.0, "leads": 4, "impressions": 1000, "clicks": 20},
    ]
    c = D.compute_ad_signals(daily)["ad_campaigns"]["c1"]
    assert c["ctr_pct"] == 2.0
    assert c["cpc"] == 5.0
    assert c["cpl"] == 25.0


def test_pick_actions_stalled_growth():
    acts = D.pick_actions({"growth": {"threads": {"per_week": -1.0, "stalled": True, "gate": {"threshold": 500, "gap": 50, "eta_days": None}}}})
    assert any(a["type"] == "growth_stalled" and a["platform"] == "threads" for a in acts)


def test_engagement_signals_dedup_edge_cases():
    base = datetime(2026, 11, 1, tzinfo=UTC)
    rows = [
        # same post, older capture arrives AFTER newer → must not overwrite
        {"platform": "linkedin", "platform_post_id": "p1", "post_id": 1, "captured_at": base + timedelta(days=2), "engagement_rate": 0.9},
        {"platform": "linkedin", "platform_post_id": "p1", "post_id": 1, "captured_at": base + timedelta(days=1), "engagement_rate": 0.1},
        # row with neither id → cannot dedup, counts as its own post
        {"platform": "threads", "engagement_rate": 0.5},
        {"platform": "threads", "engagement_rate": 0.5},
        # captured_at None comparisons stay safe
        {"platform": "x", "platform_post_id": "x1", "engagement_rate": 0.2, "captured_at": None},
        {"platform": "x", "platform_post_id": "x1", "engagement_rate": 0.4, "captured_at": None},
    ]
    out = D.compute_engagement_signals(rows)
    # linkedin: single post, latest capture (0.9) wins
    assert out["platform_engagement"]["linkedin"] == 0.9
    assert out["platform_posts"]["linkedin"] == 1
    # threads: two id-less rows count separately
    assert out["platform_posts"]["threads"] == 2
    # x: both None-cap captures — second replaces first (cap >= prev_cap)
    assert out["platform_engagement"]["x"] == 0.4
    assert out["platform_posts"]["x"] == 1


def test_ad_signals_zero_denominators():
    daily = [
        {"campaign": "c1", "spend_eur": 100.0, "leads": 0, "impressions": 0, "clicks": 0},
        {"campaign": "c2", "spend_eur": 50.0, "leads": 2, "impressions": 500, "clicks": 0},
    ]
    out = D.compute_ad_signals(daily)
    c1 = out["ad_campaigns"]["c1"]
    assert c1["cpl"] is None and c1["ctr_pct"] is None and c1["cpc"] is None
    c2 = out["ad_campaigns"]["c2"]
    assert c2["cpl"] == 25.0 and c2["ctr_pct"] == 0.0 and c2["cpc"] is None


def test_growth_signals_slope_over_min_span():
    base = datetime(2026, 11, 1, tzinfo=UTC)
    snaps = [{"platform": "linkedin", "followers": 1000 + i * 10, "captured_at": base + timedelta(days=i)} for i in range(0, 15)]
    out = D.compute_growth_signals(snaps)
    g = out["growth"]["linkedin"]
    assert g["per_week"] is not None and g["per_week"] > 0


def test_survey_price_anchor_bounds():
    responses = [
        {"answers": {"worth_per_month": "5 50 500 50000 2026 15000"}},
    ]
    out = D.compute_survey_signals(responses)
    # 50 and 500 survive (10..10000, non-year); 50000/15000 too big, 2026 a year
    assert out["price_anchor_median"] == 275.0


def test_growth_signals_all_zero_readings_mark_stalled():
    # Every snapshot is a failed scrape (followers=0) → cleaned series is
    # empty → honest stalled entry, not a fabricated trend.
    now = datetime.now(UTC)
    snaps = [
        {"platform": "x", "followers": 0, "captured_at": now - timedelta(days=7)},
        {"platform": "x", "followers": 0, "captured_at": now},
    ]
    out = D.compute_growth_signals(snaps)
    g = out["growth"]["x"]
    assert g["stalled"] is True and g["points"] == 0 and g["cleaned_readings"] == 2


def test_devapp_followers_skips_non_dict_containers_and_finds_value():
    # insights.profile as a list hits the isinstance guard; a numeric
    # follower_count inside a dict container is returned.
    assert D._devapp_followers({"insights": {"profile": [1, 2], "organization": "x"}}) is None
    assert D._devapp_followers({"insights": {"creator_info": {"follower_count": 42}}}) == 42.0
    assert D._devapp_followers({"insights": {"followers": 7}}) == 7.0


def test_devapp_signals_token_expiry_parsing():
    # Valid ISO expiry → days_to_expiry; garbage string → ValueError swallowed.
    rows = [
        {"platform": "linkedin", "username": "u1", "ok": True, "token_valid": True, "health": {"expires_at": "2999-01-01T00:00:00+00:00"}},
        {"platform": "x", "username": "u2", "ok": True, "token_valid": True, "token_expires_at": "not-a-date"},
        {"platform": "y", "username": "u3", "ok": True, "token_valid": True, "token_expires_at": "2999-06-01T00:00:00"},  # naive ISO → assumed UTC
    ]
    out = D.compute_devapp_signals(rows)
    assert out["devapp_channels"][0]["days_to_expiry"] > 30000
    assert out["devapp_channels"][1]["days_to_expiry"] is None
    assert out["devapp_channels"][2]["days_to_expiry"] > 30000


def test_pick_actions_devapp_health():
    signals = {
        "devapp_unhealthy": [
            {"platform": "x", "username": "bad", "error": "token revoked"},
            {"platform": "y", "username": "quota", "quota_exhausted": True},
        ],
        "devapp_expiring": [{"platform": "z", "username": "soon", "days_to_expiry": 3}],
    }
    acts = D.pick_actions(signals)
    health = [a for a in acts if a["type"] == "devapp_health"]
    assert len(health) == 3
    assert health[0]["reason"] == "token revoked"
    assert health[1]["reason"] == "quota exhausted"
    assert health[2]["reason"] == "token expires in 3d"
