"""Unit tests for devapps_sync pure helpers — no DB, no network."""

from datetime import UTC, datetime, timedelta

from app.services.datalake_insights import compute_devapp_signals, pick_actions
from app.services.devapps_sync import build_channel_actions, monetization_actions_from_channels

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)


def _payload(**over):
    base = {
        "platform": "linkedin",
        "account_id": "acc-1",
        "username": "cloudless-gr",
        "ok": True,
        "health": {"token_valid": True},
        "insights": {"profile": {"followers_count": 1200}},
        "scopes": ["w_member_social"],
        "captured_at": NOW.isoformat(),
    }
    base.update(over)
    return base


class TestBuildChannelActions:
    def test_healthy_account_gets_content_hint(self):
        out = build_channel_actions([_payload()], now=NOW)
        assert len(out) == 1
        a = out[0]
        assert a["type"] == "channel_action"
        assert a["platform"] == "linkedin"
        assert a["followers"] == 1200.0
        assert a["media_required"] is False

    def test_dead_token_gets_health_action(self):
        p = _payload(health={"token_valid": False}, error="LinkedInAPIError: 401")
        out = build_channel_actions([p], now=NOW)
        assert out[0]["type"] == "channel_health"
        assert out[0]["priority"] == "high"
        assert out[0]["action"] == "heal_session"

    def test_failed_pull_gets_health_action(self):
        p = _payload(ok=False, error="RuntimeError: boom", health={})
        out = build_channel_actions([p], now=NOW)
        assert out[0]["type"] == "channel_health"
        assert out[0]["action"] == "heal_session"

    def test_expiring_token_gets_refresh_action(self):
        soon = (NOW + timedelta(days=2)).isoformat()
        p = _payload(token_expires_at=soon)
        out = build_channel_actions([p], now=NOW)
        assert out[0]["type"] == "channel_health"
        assert out[0]["action"] == "refresh_token"
        assert out[0]["priority"] == "medium"

    def test_expired_token_gets_reauthorize(self):
        past = (NOW - timedelta(days=1)).isoformat()
        p = _payload(token_expires_at=past)
        out = build_channel_actions([p], now=NOW)
        assert out[0]["action"] == "reauthorize"
        assert out[0]["priority"] == "high"

    def test_quota_exhausted_gets_health_action(self):
        p = _payload(insights={"quota_exhausted": True})
        out = build_channel_actions([p], now=NOW)
        assert out[0]["type"] == "channel_health"
        assert out[0]["action"] == "wait_for_quota_reset"

    def test_media_platforms_flagged(self):
        for platform in ("instagram", "tiktok"):
            out = build_channel_actions([_payload(platform=platform)], now=NOW)
            assert out[0]["media_required"] is True

    def test_non_dict_rows_skipped(self):
        out = build_channel_actions(["junk", None, _payload()], now=NOW)
        assert len(out) == 1


class TestComputeDevappSignals:
    def test_unhealthy_and_expiring_split(self):
        rows = [
            _payload(platform="linkedin"),
            _payload(platform="twitter", health={"token_valid": False}, username="tb"),
            _payload(
                platform="tiktok",
                token_expires_at=(NOW + timedelta(days=3)).isoformat(),
            ),
        ]
        sig = compute_devapp_signals(rows)
        assert len(sig["devapp_channels"]) == 3
        assert [c["platform"] for c in sig["devapp_unhealthy"]] == ["twitter"]
        assert [c["platform"] for c in sig["devapp_expiring"]] == ["tiktok"]

    def test_followers_extracted_from_nested_profile(self):
        sig = compute_devapp_signals([_payload()])
        assert sig["devapp_channels"][0]["followers"] == 1200.0

    def test_pick_actions_emits_devapp_health(self):
        sig = compute_devapp_signals([_payload(platform="twitter", health={"token_valid": False}, username="tb")])
        actions = pick_actions(sig)
        health = [a for a in actions if a["type"] == "devapp_health"]
        assert len(health) == 1
        assert health[0]["platform"] == "twitter"
        assert health[0]["priority"] == "high"

    def test_pick_actions_expiring_token_medium(self):
        sig = compute_devapp_signals([_payload(token_expires_at=(NOW + timedelta(days=2)).isoformat())])
        actions = pick_actions(sig)
        health = [a for a in actions if a["type"] == "devapp_health"]
        assert len(health) == 1
        assert health[0]["priority"] == "medium"
        assert "expires in" in health[0]["reason"]

    def test_healthy_channels_no_actions(self):
        sig = compute_devapp_signals([_payload()])
        actions = [a for a in pick_actions(sig) if a["type"] == "devapp_health"]
        assert actions == []


class TestMonetizationActionsFromChannels:
    def test_channel_action_becomes_instruction(self):
        actions = build_channel_actions([_payload(platform="linkedin", account_type="organization")], now=NOW)
        out = monetization_actions_from_channels(actions)
        assert len(out) == 1
        a = out[0]
        assert a["platform"] == "linkedin"
        assert "cloudless-gr" in a["instruction"]
        assert "company page" in a["instruction"]
        assert "1200 followers" in a["instruction"]

    def test_health_rows_never_generate_posts(self):
        actions = build_channel_actions([_payload(health={"token_valid": False})], now=NOW)
        assert monetization_actions_from_channels(actions) == []

    def test_media_required_gets_image_format(self):
        actions = build_channel_actions([_payload(platform="instagram")], now=NOW)
        out = monetization_actions_from_channels(actions)
        assert out[0]["format"] == "image"

    def test_text_channel_has_no_format(self):
        actions = build_channel_actions([_payload(platform="linkedin")], now=NOW)
        out = monetization_actions_from_channels(actions)
        assert out[0]["format"] == ""

    def test_junk_rows_dropped(self):
        assert monetization_actions_from_channels(["x", None, {"type": "other"}]) == []


class TestPrivateHelpers:
    """Coverage for the small pure helpers the action builders rely on."""

    def test_num_nested_dict_and_bool_exclusion(self):
        from app.services.devapps_sync import _num

        assert _num({"a": {"b": 4}}, "a", "b") == 4.0
        assert _num({"a": True, "b": 2}, "a", "b") == 2.0
        assert _num({"a": "x"}, "a", "b") is None
        assert _num({}, "a") is None

    def test_followers_creator_info_and_bad_container(self):
        from app.services.devapps_sync import _followers

        p = {"insights": {"profile": "not-a-dict", "creator_info": {"follower_count": 88}}}
        assert _followers(p) == 88.0
        p2 = {"insights": {"organization": {"followerCounts": {"totalFollowerCount": 5}}}}
        assert _followers(p2) is None or _followers(p2) == 5.0

    def test_days_to_expiry_naive_and_invalid(self):
        from app.services.devapps_sync import _days_to_expiry

        naive = {"health": {"expires_at": "2026-11-10T00:00:00"}}
        d = _days_to_expiry(naive, NOW)
        assert d is not None and d > 0
        bad = {"token_expires_at": "not-a-date"}
        assert _days_to_expiry(bad, NOW) is None
        assert _days_to_expiry({}, NOW) is None

    def test_content_hint_surfaces_engagement_counters(self):
        from app.services.devapps_sync import _content_hint

        p = _payload(
            platform="facebook",
            insights={
                "profile": {"followers_count": 10},
                "reach_30d": 900,
                "page_impressions_unique_30d": 1200,
                "accounts_engaged_30d": "NaN-ish",
            },
        )
        hint = _content_hint(p)
        assert hint["reach_30d"] == 900
        assert hint["page_impressions_unique_30d"] == 1200
        assert "accounts_engaged_30d" not in hint

    def test_monetization_actions_drops_platformless_rows(self):
        out = monetization_actions_from_channels(
            [
                {"type": "channel_action"},  # no platform → dropped
                {"type": "channel_health", "platform": "x"},  # health → dropped
                "junk",
                {"type": "channel_action", "platform": "linkedin", "username": "u", "followers": 5},
            ]
        )
        assert len(out) == 1
        assert out[0]["platform"] == "linkedin"
        assert "5 followers" in out[0]["instruction"]
