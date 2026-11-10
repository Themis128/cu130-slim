"""Unit tests for the central brain decision layer.

``decide()`` and ``_snapshot_health()`` are pure — these tests pin the
scoring, health gating, market-trend adjustment, and action caps without
touching the DB or the publish executor.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.content import PostStatus
from app.services.central_brain import (
    MAX_POST_ACTIONS,
    MAX_TREND_ACTIONS,
    BrainDecision,
    BrainSnapshot,
    ChannelState,
    _parse_llm_decisions,
    _snapshot_health,
    collect_snapshot,
    decide,
    run_for_team,
)
from app.worker.tasks import central_brain as cb_task

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
        channels = {f"plat{i}": _state(f"plat{i}", followers=100 * i, engagement_rate_7d=1.0) for i in range(MAX_POST_ACTIONS + 4)}
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
        trends = [{"query": f"trend{i}", "classification": "early_window", "traffic": 100 - i} for i in range(MAX_TREND_ACTIONS + 2)]
        channels = {f"plat{i}": _state(f"plat{i}", followers=100 + i, engagement_rate_7d=1.0) for i in range(MAX_POST_ACTIONS)}
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


# ── DB layer: collect_snapshot / run_for_team ─────────────────────────


class _Exec:
    """Result stand-in — supports scalars()/unique()/all() chains."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return self._rows


class _DB:
    """AsyncSession stand-in — execute() pops preset rows in call order."""

    def __init__(self, *result_sets):
        self._results = list(result_sets)

    async def execute(self, *a, **k):
        return _Exec(self._results.pop(0))


def _devapp(platform, account_id, **payload):
    return SimpleNamespace(
        social_account_id=account_id,
        meta_data={"platform": platform, **payload},
    )


class TestCollectSnapshot:
    @pytest.mark.asyncio
    async def test_merges_devapp_engagement_outcomes_followers_trends(self):
        team = SimpleNamespace(id=uuid.uuid4())
        devapps = [
            _devapp("LinkedIn", "acc-li", username="cloudless", ok=True, insights={"followers": 1200}),
            _devapp("instagram", "acc-ig", ok=False, error="token revoked"),
        ]
        engagement = [("LinkedIn", 4.5, 12), ("threads", 1.0, 3)]
        posts = [
            SimpleNamespace(
                status=PostStatus.PUBLISHED,
                targets=[
                    SimpleNamespace(social_account=SimpleNamespace(platform="LinkedIn")),
                    SimpleNamespace(social_account=SimpleNamespace(platform="facebook")),
                ],
            ),
            SimpleNamespace(
                status=PostStatus.FAILED,
                targets=[
                    SimpleNamespace(social_account=SimpleNamespace(platform="facebook")),
                ],
            ),
        ]
        followers = [("linkedin", 5000), ("tiktok", 800)]
        trends = [SimpleNamespace(query="ai agents", source="gtrends", geo="GR", classification="opportunity", traffic=95, period_key="2026-11-08")]
        snap = await collect_snapshot(_DB(devapps, engagement, posts, followers, trends), team, now=NOW)

        li = snap.channels["linkedin"]
        assert li.username == "cloudless"
        assert li.followers == 1200  # devapp insights beat the follower snapshot
        assert li.engagement_rate_7d == 4.5
        assert li.posts_7d == 12
        assert li.published_7d == 1 and li.failed_7d == 0

        fb = snap.channels["facebook"]
        assert fb.published_7d == 1 and fb.failed_7d == 1
        assert snap.channels["instagram"].healthy is False
        assert snap.channels["instagram"].health_reason == "token revoked"
        assert snap.channels["tiktok"].followers == 800
        assert snap.trends[0]["query"] == "ai agents"

    @pytest.mark.asyncio
    async def test_empty_sources_produce_empty_snapshot(self):
        team = SimpleNamespace(id=uuid.uuid4())
        snap = await collect_snapshot(_DB([], [], [], [], []), team, now=NOW)
        assert snap.channels == {}
        assert snap.trends == []


class TestRunForTeam:
    def _db_with_healthy_channel(self):
        return _DB(
            [_devapp("linkedin", "acc-1", ok=True, insights={"followers": 900})],
            [],
            [],
            [],
            [],
        )

    @pytest.mark.asyncio
    async def test_dispatches_post_decisions_and_journals(self, monkeypatch):
        scheduled_calls = []

        async def fake_schedule(db, team, actions, **kw):
            scheduled_calls.append((actions, kw))
            return [{"platform": a.platform, "status": "scheduled"} for a in actions]

        remembered = []

        async def fake_remember(*a, **k):
            remembered.append((a, k))

        monkeypatch.setattr("app.services.monetization_executor.schedule_report_actions", fake_schedule)
        monkeypatch.setattr("app.services.company_brain.remember", fake_remember)

        async def no_llm(*a, **k):
            return None

        monkeypatch.setattr("app.services.central_brain._llm_adjust", no_llm)

        team = SimpleNamespace(id=uuid.uuid4())
        result = await run_for_team(self._db_with_healthy_channel(), team, now=NOW)

        assert result["posts_dispatched"] >= 1
        assert result["scheduled"]
        actions, kw = scheduled_calls[0]
        assert kw["source"] == "central-brain"
        assert kw["dedup_field"] == "central_brain_action"
        assert any(a.platform == "linkedin" for a in actions)
        assert remembered  # plan journaled to company_brain

    @pytest.mark.asyncio
    async def test_no_post_decisions_skips_dispatch(self, monkeypatch):
        async def fail_schedule(*a, **k):
            raise AssertionError("schedule_report_actions must not be called")

        monkeypatch.setattr("app.services.monetization_executor.schedule_report_actions", fail_schedule)
        # instagram ok=False → health decision only, no post actions
        db = _DB(
            [_devapp("instagram", "acc-1", ok=False, error="banned")],
            [],
            [],
            [],
            [],
        )
        team = SimpleNamespace(id=uuid.uuid4())
        result = await run_for_team(db, team, now=NOW)
        assert result["posts_dispatched"] == 0
        assert result["decisions"][0]["kind"] == "health"


# ── celery task wrapper ───────────────────────────────────────────────


class _SessionCM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


class _CommitDB(_DB):
    def __init__(self, *result_sets):
        super().__init__(*result_sets)
        self.committed = False

    async def commit(self):
        self.committed = True


class TestCentralBrainTask:
    def test_entrypoint_delegates_to_run_async(self, monkeypatch):
        sentinel = {"ok": True}

        def fake_run_async(coro):
            coro.close()
            return sentinel

        monkeypatch.setattr(cb_task, "run_async", fake_run_async)
        assert cb_task.run_central_brain() is sentinel

    @pytest.mark.asyncio
    async def test_run_isolates_per_team_failures(self, monkeypatch):
        good, bad = uuid.uuid4(), uuid.uuid4()
        db = _CommitDB([SimpleNamespace(id=good), SimpleNamespace(id=bad)])

        async def fake_run_for_team(db_, team, now=None):
            if team.id == bad:
                raise RuntimeError("boom")
            return {"team_id": str(team.id), "posts_dispatched": 1}

        monkeypatch.setattr(cb_task, "run_for_team", fake_run_for_team)
        monkeypatch.setattr(cb_task, "task_session", lambda: _SessionCM(db))

        out = await cb_task._run()
        assert out["teams"] == 2
        assert out["results"][0]["posts_dispatched"] == 1
        assert out["results"][1]["status"] == "failed"


# ── LLM decision refinement ───────────────────────────────────────────


def _cand(platform: str, priority: int = 50) -> BrainDecision:
    return BrainDecision(
        platform=platform,
        kind="post",
        instruction=f"rule instruction for {platform}",
        priority=priority,
        reason="score=x er7d=1.0 posts7d=2",
        trend_query="ai agents",
    )


class TestParseLLMDecisions:
    def test_valid_payload_rewrites_instructions(self):
        text = 'sure! {"posts":[{"platform":"linkedin","instruction":"post about edge latency win","priority":88}]}'
        out = _parse_llm_decisions(text, [_cand("linkedin"), _cand("instagram")])
        assert out is not None
        assert len(out) == 1
        assert out[0].platform == "linkedin"
        assert out[0].instruction == "post about edge latency win"
        assert out[0].priority == 88
        assert out[0].kind == "post"
        assert "llm-refined" in out[0].reason
        assert out[0].trend_query == "ai agents"

    def test_unknown_platforms_and_dupes_are_dropped(self):
        text = (
            '{"posts":['
            '{"platform":"tiktok","instruction":"nope","priority":90},'
            '{"platform":"linkedin","instruction":"first","priority":80},'
            '{"platform":"linkedin","instruction":"dupe","priority":70},'
            '{"platform":"instagram","instruction":"","priority":60}'
            "]}"
        )
        out = _parse_llm_decisions(text, [_cand("linkedin"), _cand("instagram")])
        assert out is not None
        assert [d.platform for d in out] == ["linkedin"]

    def test_no_json_or_empty_posts_returns_none(self):
        assert _parse_llm_decisions("no json here", [_cand("linkedin")]) is None
        assert _parse_llm_decisions('{"posts":[]}', [_cand("linkedin")]) is None
        assert _parse_llm_decisions("not json {broken", [_cand("linkedin")]) is None

    def test_priority_is_clamped(self):
        text = '{"posts":[{"platform":"linkedin","instruction":"x","priority":500}]}'
        out = _parse_llm_decisions(text, [_cand("linkedin")])
        assert out[0].priority == 100

    @pytest.mark.asyncio
    async def test_run_for_team_uses_refined_decisions_and_meta(self, monkeypatch):
        scheduled_calls = []

        async def fake_schedule(db, team, actions, **kw):
            scheduled_calls.append(actions)
            return [{"platform": a.platform, "status": "scheduled"} for a in actions]

        async def fake_remember(*a, **k):
            return True

        refined = BrainDecision(
            platform="linkedin",
            kind="post",
            instruction="LLM-refined instruction",
            priority=91,
            reason="llm-refined | rule=score=x",
            trend_query="ai agents",
        )

        async def fake_llm(*a, **k):
            return [refined]

        monkeypatch.setattr("app.services.monetization_executor.schedule_report_actions", fake_schedule)
        monkeypatch.setattr("app.services.company_brain.remember", fake_remember)
        monkeypatch.setattr("app.services.central_brain._llm_adjust", fake_llm)

        team = SimpleNamespace(id=uuid.uuid4())
        db = _DB(
            [_devapp("linkedin", "acc-1", ok=True, insights={"followers": 900})],
            [],
            [],
            [],
            [],
        )
        result = await run_for_team(db, team, now=NOW)

        assert result["posts_dispatched"] == 1
        actions = scheduled_calls[0]
        assert len(actions) == 1
        assert actions[0].instruction == "LLM-refined instruction"
        assert actions[0].extra_meta["brain_priority"] == 91
        assert "llm-refined" in actions[0].extra_meta["brain_reason"]
        assert actions[0].extra_meta["brain_trend"] == "ai agents"
