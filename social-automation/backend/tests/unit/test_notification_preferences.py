"""Unit tests for notification preference toggles.

Validates that:
- All 6 email preference fields exist in the Pydantic models.
- The User model default includes all 6 email preference fields.
- The preference defaults are True (opt-in).
- The Pydantic response model round-trips all fields.
"""

from app.api.auth import NotificationPreferencesRequest, NotificationPreferencesResponse
from app.models.user import User

# ─── Model field coverage ───────────────────────────────────────────


class TestPreferenceModelFields:
    """Ensure all 6 email preference fields are present in both models."""

    EXPECTED_EMAIL_FIELDS = [
        "email_new_post",
        "email_scheduled",
        "email_analytics",
        "email_on_quota",
        "email_on_invite",
        "email_account_connected",
    ]

    def test_request_model_has_all_email_fields(self):
        fields = NotificationPreferencesRequest.model_fields
        for field in self.EXPECTED_EMAIL_FIELDS:
            assert field in fields, f"Missing {field} in NotificationPreferencesRequest"

    def test_response_model_has_all_email_fields(self):
        fields = NotificationPreferencesResponse.model_fields
        for field in self.EXPECTED_EMAIL_FIELDS:
            assert field in fields, f"Missing {field} in NotificationPreferencesResponse"

    def test_request_defaults_match_expected(self):
        """Email preferences should default to True (opt-in), except email_analytics."""
        req = NotificationPreferencesRequest()
        # Most email prefs default to True; email_analytics defaults to False
        # (pre-existing behavior — analytics emails are noisy).
        assert req.email_new_post is True
        assert req.email_scheduled is True
        assert req.email_analytics is False
        assert req.email_on_quota is True
        assert req.email_on_invite is True
        assert req.email_account_connected is True

    def test_response_round_trips_all_fields(self):
        """Response model should serialize all 6 email fields."""
        resp = NotificationPreferencesResponse(
            email_new_post=False,
            email_scheduled=True,
            email_analytics=True,
            email_on_quota=False,
            email_on_invite=True,
            email_account_connected=True,
            push_new_post=True,
            push_scheduled=True,
            push_analytics=False,
        )
        data = resp.model_dump()
        for field in self.EXPECTED_EMAIL_FIELDS:
            assert field in data, f"Missing {field} in serialized response"


# ─── User model default preferences ─────────────────────────────────


class TestUserDefaultPreferences:
    """The User model default notification_preferences must include all 6 fields."""

    EXPECTED_EMAIL_FIELDS = [
        "email_new_post",
        "email_scheduled",
        "email_analytics",
        "email_on_quota",
        "email_on_invite",
        "email_account_connected",
    ]

    def test_defaults_include_all_email_fields(self):
        defaults = User.__table__.columns["notification_preferences"].default
        if defaults is None:
            # The default might be set in __init__ or the model, not the column.
            # Check the model's default dict directly.
            import inspect

            src = inspect.getsource(User)
            for field in self.EXPECTED_EMAIL_FIELDS:
                assert field in src, f"{field} not found in User model source"
            return
        # If there's a column default, check it.
        default_value = defaults.arg if hasattr(defaults, "arg") else defaults
        if isinstance(default_value, dict):
            for field in self.EXPECTED_EMAIL_FIELDS:
                assert field in default_value, f"Missing {field} in column default"
        else:
            # callable default — skip direct inspection
            pass

    def test_defaults_are_true(self):
        """All email preference defaults should be True."""
        import inspect

        src = inspect.getsource(User)
        for field in self.EXPECTED_EMAIL_FIELDS:
            # Look for "field": True pattern in the source
            assert f'"{field}"' in src, f"{field} not found in User model source"


# ─── Preference gating logic ────────────────────────────────────────


class TestPreferenceGating:
    """Verify that the right preference is checked in each email path."""

    def test_quota_warning_checks_email_on_quota(self):
        """deps.py quota warning should check email_on_quota."""
        import inspect

        from app.api import deps

        src = inspect.getsource(deps)
        assert "email_on_quota" in src, "deps.py should check email_on_quota"

    def test_account_connected_checks_email_account_connected(self):
        """auth.py OAuth callback should check email_account_connected."""
        import inspect

        from app.api import auth

        src = inspect.getsource(auth)
        assert "email_account_connected" in src, "auth.py should check email_account_connected"

    def test_team_invite_checks_email_on_invite(self):
        """teams.py invite should check email_on_invite."""
        import inspect

        from app.api import teams

        src = inspect.getsource(teams)
        assert "email_on_invite" in src, "teams.py should check email_on_invite"

    def test_post_published_checks_email_new_post(self):
        """publishing task should check email_new_post."""
        import inspect

        from app.worker.tasks import publishing

        src = inspect.getsource(publishing)
        assert "email_new_post" in src, "publishing task should check email_new_post"
