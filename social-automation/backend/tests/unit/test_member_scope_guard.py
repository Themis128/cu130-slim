"""Guard tests for the r_member_postAnalytics scope check.

Regression: memberCreatorPostAnalytics requires the member permission
``r_member_postAnalytics`` (Community Management API). The dev app doesn't
carry it, so every call returned 403 and was retried on every sync. The
recorded OAuth scope list is authoritative — when it lacks the scope the
caller must short-circuit instead of issuing a guaranteed-fail request.
"""

from app.services.analytics_sync import _member_post_analytics_scope_missing


def test_missing_scope_detected():
    scopes = ["openid", "profile", "email", "w_member_social"]
    assert _member_post_analytics_scope_missing(scopes) is True


def test_granted_scope_passes():
    scopes = ["openid", "w_member_social", "r_member_postAnalytics"]
    assert _member_post_analytics_scope_missing(scopes) is False


def test_unrecorded_scopes_still_probe():
    # Accounts linked before scope persistence have empty lists — keep probing
    assert _member_post_analytics_scope_missing([]) is False
    assert _member_post_analytics_scope_missing(None) is False
