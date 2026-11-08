"""Unit tests for the central brain decision layer.

``decide()`` and ``_snapshot_health()`` are pure — these tests pin the
scoring, health gating, market-trend adjustment, and action caps without
touching the DB or the publish executor.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.services.central_brain import (
    MAX_POST_ACTIONS,
    MAX_TREND_ACTIONS,
    BrainDecision,
    BrainSnapshot,
    ChannelState,
    _snapshot_health,
    decide,
)

NOW = datetime(2026, 11, 8, 6, 30, tzinfo=UTC)


def _snap(channels: dict[str, ChannelState], trends: list[dict] | None = None) -> BrainSnapshot:
    return BrainSnapshot(
        team_id=uuid.uuid4(),
        generated_at=NOW,
        channels=channels,
        trends=trends or [],
    )


def _state(platform: str, **kw) -> ChannelState:
    return ChannelState(platform=platform, **kw)


class TestSnapshotHealth:
    def test_empty_payload_is_neutral(self):
        assert _snapshot_health({}, NOW) == (None, "", None)

    def test_ok_false_is_unhealthy(self):
        healthy, reason, _ = _snapshot_health({"ok": False, "error": "boom"}, NOW)
        assert healthy is False
        assert "boom" in reason

    def test_token_valid_false_is_unhealthy(self):
        healthy, _, _ = _snapshot_health({"health": {"token_valid": False}}, NOW)
        assert healthy is False

    def test_expiring_soon_warns_but_stays_healthy(self):
        expiry = (NOW + timedelta(days=3)).isoformat()
        healthy, reason, days = _snapshot_health({"token_expires_at": expiry}, NOW)
        assert healthy is True
        assert days == 3.0
        assert "expires" in reason

    def test_far_expiry_is_clean(self):
        expiry = (NOW + timedelta(days=60)).isoformat()
        healthy, reason, _ = _snapshot_health({"token_expires_at": expiry}, NOW)
        assert healthy is True
        assert reason == ""

    def test_garbage_expiry_ignored(self):
        healthy, _, days = _snapshot_health({"token_expires_at": "not-a-date"}, NOW)
        assert healthy is True
        assert days is None


class TestDecide:
    def test_empty_snapshot_no_decisions(self):
        assert decide(_snap({})) == []

    def test_unhealthy_channel_never_posts(self):
        snap = _snap({"twitter": _state("twitter", healthy=False, health_reason="401")})
        decisions = decide(snap)
        assert len(decisions) == 1
        assert decisions[0].kind == "health"
        assert decisions[0].platform == "twitter"

    def test_healthy_channels_get_post_actions(self):
        snap = _snap(
            {
                "linkedin": _state("linkedin", followers=1200, engagement_rate_7d=5.0, posts_7d=4),
                "facebook": _state("facebook", followers=300, engagement_rate_7d=1.0, posts_7d=2),
            }
        )
        decisions = decide(snap)
        posts = [d for d in decisions if d.kind == "post"]
        assert {d.platform for d in posts} == {"linkedin", "facebook"}
        # higher score first
        assert posts[0].platform == "linkedin"

    def test_post_cap_respected(self):
        channels = {
            f"plat{i}": _state(f"plat{i}", followers=100 * i, engagement_rate_7d=1.0)
            for i in range(MAX_POST_ACTIONS + 4)
        }
        posts = [d for d in decide(_snap(channels)) if d.kind == "post"]
        assert len(posts) == MAX_POST_ACTIONS

    def test_majority_failure_channel_becomes_health(self):
        snap = _snap(
            {
                "tiktok": _state(
                    "tiktok",
                    followers=500,
                    published_7d=1,
                    failed_7d=3,
                )
            }
        )
        decisions = decide(snap)
        assert all(d.kind == "health" for d in decisions)
        assert "failed" in decisions[0].reason

    def test_neutral_channel_with_no_devapp_data_still_posts(self):
        snap = _snap({"bluesky": _state("bluesky", followers=50)})
        decisions = decide(snap)
        assert any(d.kind == "post" and d.platform == "bluesky" for d in decisions)

    def test_trend_signals_shape_instructions(self):
        trends = [
            {
                "query": "agentic ai",
                "source": "google-trends",
                "geo": "GR",
                "classification": "opportunity",
                "traffic": 100,
            }
        ]
        snap = _snap({"linkedin": _state("linkedin", followers=900)}, trends=trends)
        posts = [d for d in decide(snap) if d.kind == "post"]
        assert len(posts) == 1
        assert "agentic ai" in posts[0].instruction
        assert posts[0].trend_query == "agentic ai"

    def test_trend_rotation_caps_at_max_trend_actions(self):
        trends = [
            {"query": f"trend{i}", "classification": "early_window", "traffic": 100 - i}
            for i in range(MAX_TREND_ACTIONS + 2)
        ]
        channels = {
            f"plat{i}": _state(f"plat{i}", followers=100 + i, engagement_rate_7d=1.0)
            for i in range(MAX_POST_ACTIONS)
        }
        posts = [d for d in decide(_snap(channels, trends)) if d.kind == "post"]
        trend_posts = [d for d in posts if d.trend_query]
        assert len(trend_posts) == MAX_TREND_ACTIONS
        # remaining posts keep the evergreen instruction
        assert any(not d.trend_query for d in posts)

    def test_expiry_warning_recorded_in_reason(self):
        snap = _snap(
            {
                "x": _state(
                    "x",
                    followers=10,
                    healthy=True,
                    days_to_expiry=2.5,
                )
            }
        )
        posts = [d for d in decide(snap) if d.kind == "post"]
        assert "expiry_warn=2.5d" in posts[0].reason

    def test_health_decisions_sort_before_or_after_by_kind(self):
        snap = _snap(
            {
                "dead": _state("dead", healthy=False, health_reason="x"),
                "live": _state("live", followers=10),
            }
        )
        decisions = decide(snap)
        kinds = [d.kind for d in decisions]
        # posts rank first (kind ordering puts post before health)
        assert kinds[0] == "post"


class TestDecideShape:
    def test_decision_is_brain_decision_dataclass(self):
        snap = _snap({"a": _state("a", followers=1)})
        for d in decide(snap):
            assert isinstance(d, BrainDecision)
            assert d.platform
            assert d.kind in {"post", "health"}
