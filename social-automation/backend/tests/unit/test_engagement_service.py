"""Outbound engagement engine — scoring, vector routing, 3C prompt, queueing.

Real code for app.services.engagement; only the AsyncSession is faked.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import engagement as E


def _cand(**kw):
    base = dict(
        platform="linkedin",
        author_handle="ana",
        author_name="Ana",
        post_id="p1",
        post_text="short post",
        posted_seconds_ago=60,
        connection_tier="medium",
        is_followed=False,
        is_connection=False,
        in_crm=False,
    )
    base.update(kw)
    return E.EngagementCandidate(**base)


def _words(n: int) -> str:
    return " ".join(["word"] * n)


class _Res:
    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v


class _DB:
    def __init__(self, counts=()):
        self.counts = list(counts)
        self.added = []
        self.commits = 0
        self.stmts = []

    async def execute(self, stmt):
        self.stmts.append(stmt)
        return _Res(self.counts.pop(0) if self.counts else 0)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


# ── _sentiment_kill / score_candidate ──────────────────────────────────


@pytest.mark.parametrize("text", ["We got LAID OFF today", "in the hospital", "grief is hard"])
def test_kill_switch_blocks_sensitive_posts(text):
    plan = E.score_candidate(_cand(post_text=text, in_crm=True, is_connection=True))
    assert plan.action is None
    assert plan.kill_switched is True
    assert plan.score == 0.0
    assert plan.reason.startswith("kill-switch")


def test_below_threshold_skips():
    # stale (>1 day) cold post with a medium-length body → only RYR weight
    plan = E.score_candidate(_cand(post_text=_words(20), posted_seconds_ago=2 * 86400), ryr_tier="low")
    assert plan.action is None
    assert plan.kill_switched is False
    assert plan.score == pytest.approx(0.3)
    assert "below threshold" in plan.reason


def test_unknown_ryr_tier_defaults_to_one(monkeypatch):
    plan = E.score_candidate(_cand(post_text=_words(20), posted_seconds_ago=2 * 86400), ryr_tier="bogus")
    assert plan.score == pytest.approx(1.0)


def test_comment_tier_for_rich_fresh_post(monkeypatch):
    monkeypatch.setattr(E.random, "randint", lambda a, b: 200)
    plan = E.score_candidate(
        _cand(post_text=_words(70), is_connection=True, is_followed=True, in_crm=True), ryr_tier="high"
    )
    # 3 + 2 + 4 + 2 (high) + 4 (fresh) + 2 (rich) = 17
    assert plan.score == pytest.approx(17.0)
    assert plan.action == "comment"
    assert plan.jitter_seconds == 200


def test_repost_tier_when_text_not_rich(monkeypatch):
    monkeypatch.setattr(E.random, "randint", lambda a, b: a)
    # connection + crm + medium + fresh = 3+4+1+4 = 12, words=20 → no comment
    plan = E.score_candidate(_cand(post_text=_words(20), is_connection=True, in_crm=True))
    assert plan.action == "repost"
    assert plan.jitter_seconds == E.JITTER_RANGE[0]


def test_like_tier_and_freshness_buckets():
    # followed(2) + medium(1) + 6h-bucket(2) + short(0.5) = 5.5 → like
    plan = E.score_candidate(_cand(is_followed=True, posted_seconds_ago=120 * 60))
    assert plan.action == "like"
    assert plan.score == pytest.approx(5.5)
    assert E.JITTER_RANGE[0] <= plan.jitter_seconds <= E.JITTER_RANGE[1]
    # <1 day bucket adds 0.5
    plan = E.score_candidate(_cand(is_connection=True, posted_seconds_ago=600 * 60, post_text=_words(20)))
    assert plan.score == pytest.approx(3 + 1 + 0.5)
    assert plan.action == "like"


# ── classify_vector / build_3c_prompt ──────────────────────────────────


def test_classify_vector_tech_vs_growth():
    v, hint = E.classify_vector("CTO", "serverless cloudflare workers on the edge")
    assert v == "TECH_STACK"
    assert "infra" in hint
    v, hint = E.classify_vector("founder", "bootstrap saas revenue growth")
    assert v == "GROWTH_UNIT_ECONOMICS"
    assert "14-day" in hint
    # tie → growth
    assert E.classify_vector("", "nothing relevant")[0] == "GROWTH_UNIT_ECONOMICS"


def test_build_3c_prompt_with_and_without_override():
    long_text = "x" * 2000
    p = E.build_3c_prompt(long_text, "Ana", ["warm"], vector_override="focus infra")
    assert "Ana's post" in p
    assert "- VECTOR FOCUS: focus infra" in p
    assert "['warm']" in p
    assert "x" * 1200 in p and "x" * 1201 not in p
    p2 = E.build_3c_prompt("hello", "Bob", [])
    assert "VECTOR FOCUS" not in p2
    assert "professional, technical" in p2


# ── queue_engagement ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_skips_unscored_candidate():
    db = _DB()
    out = await E.queue_engagement(_cand(post_text="my surgery went fine"), db)
    assert out == {"skipped": "kill-switch: sensitive content", "kill_switched": True}
    assert db.stmts == []


@pytest.mark.asyncio
async def test_queue_daily_cap_reached():
    db = _DB(counts=[30])
    out = await E.queue_engagement(_cand(is_followed=True, is_connection=True), db)
    assert out == {"skipped": "daily cap 30 reached", "queued_for_tomorrow": True}
    assert db.added == []


@pytest.mark.asyncio
async def test_queue_unknown_platform_uses_default_cap():
    db = _DB(counts=[25])
    out = await E.queue_engagement(_cand(platform="mastodon", is_followed=True, is_connection=True), db)
    assert out["skipped"] == "daily cap 25 reached"


@pytest.mark.asyncio
async def test_queue_like_is_auto_approved():
    db = _DB(counts=[None])  # scalar() None → treated as 0
    out = await E.queue_engagement(_cand(is_followed=True, posted_seconds_ago=7200), db)
    assert out["queued"] == "like"
    assert out["needs_approval"] is False
    assert db.commits == 1
    row = db.added[0]
    assert row.status == "pending"
    assert row.action == "like"
    assert row.platform == "linkedin"
    assert row.target_post_id == "p1"


@pytest.mark.asyncio
async def test_queue_comment_is_human_gated():
    db = _DB(counts=[0])
    c = _cand(post_text=_words(70), is_connection=True, is_followed=True, in_crm=True)
    out = await E.queue_engagement(c, db)
    assert out["queued"] == "comment"
    assert out["needs_approval"] is True
    assert db.added[0].status == "pending_human_gate"


@pytest.mark.asyncio
async def test_queue_facebook_hourly_comment_cap():
    db = _DB(counts=[0, 5])
    c = _cand(platform="facebook", post_text=_words(70), is_connection=True, is_followed=True, in_crm=True)
    out = await E.queue_engagement(c, db)
    assert out == {"skipped": "fb hourly comment cap (5/h)"}
    assert len(db.stmts) == 2


@pytest.mark.asyncio
async def test_queue_facebook_comment_under_hourly_cap():
    db = _DB(counts=[0, None])
    c = _cand(platform="facebook", post_text=_words(70), is_connection=True, is_followed=True, in_crm=True)
    out = await E.queue_engagement(c, db)
    assert out["queued"] == "comment"
    assert db.added[0].platform == "facebook"


@pytest.mark.asyncio
async def test_queue_viral_tiktok_becomes_gated_duet_flag():
    db = _DB(counts=[0])
    c = _cand(platform="tiktok", is_followed=True, posted_seconds_ago=7200, meta_data={"viral": True})
    out = await E.queue_engagement(c, db)
    assert out["queued"] == "duet_flag"
    row = db.added[0]
    assert row.action == "duet_flag"
    assert row.status == "pending_human_gate"


@pytest.mark.asyncio
async def test_queue_non_viral_tiktok_without_meta_data():
    """Regression: the dataclass had no meta_data field → AttributeError on tiktok."""
    db = _DB(counts=[0])
    c = _cand(platform="tiktok", is_followed=True, posted_seconds_ago=7200)
    out = await E.queue_engagement(c, db)
    assert out["queued"] == "like"
    assert db.added[0].action == "like"
    assert db.added[0].status == "pending"


def test_plan_dataclass_roundtrip():
    p = E.EngagementPlan("like", 1.0, 3, None, False, "r")
    assert SimpleNamespace(**p.__dict__).action == "like"
