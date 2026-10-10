"""Unit tests for the monetization-readiness evaluator."""

from app.services import monetization as m


def test_facebook_profile_stars_criteria():
    r = m.evaluate_program("facebook", {"followers": 66, "days_held": 0}, account_type="user")
    assert r is not None and "Stars" in r.program
    by_name = {c.name: c for c in r.criteria}
    assert by_name["followers ≥ 500"].met is False
    assert by_name["followers ≥ 500"].current == 66
    assert by_name["held ≥500 for 30 consecutive days"].met is False
    # manual criteria undetermined
    assert by_name["eligible country"].met is None
    assert r.monetizable is False


def test_facebook_page_gets_page_program_not_stars():
    r = m.evaluate_program("facebook", {"followers": 1}, account_type="page")
    assert "page" in r.program.lower() and "Stars (professional" not in r.program


def test_tiktok_creator_rewards_partial():
    r = m.evaluate_program("tiktok", {"followers": 10_500, "views_30d": 50_000})
    by_name = {c.name: c for c in r.criteria}
    assert by_name["followers ≥ 10,000"].met is True
    assert by_name["≥100,000 video views in last 30 days"].met is False
    assert r.monetizable is False


def test_twitter_needs_premium_flag():
    r = m.evaluate_program("twitter", {"followers": 600})
    by_name = {c.name: c for c in r.criteria}
    assert by_name["followers ≥ 500"].met is True
    assert by_name["X Premium / Premium+ active"].met is None  # manual


def test_funnel_platforms_have_no_program():
    for p in ("linkedin", "threads", "bluesky", "whatsapp", "telegram", "viber"):
        assert m.evaluate_program(p, {}) is None
        assert p in m.FUNNEL_ONLY


def test_days_to_goal():
    assert m.days_to_goal(500, 500, 0) == 0
    assert m.days_to_goal(66, 500, 2.0) == 217
    assert m.days_to_goal(66, 500, 0) is None  # not growing
    assert m.days_to_goal(66, 500, -1) is None
