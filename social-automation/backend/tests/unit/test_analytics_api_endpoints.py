"""Endpoint tests for app.api.analytics — the query-format route bodies.

_route functions are called directly with a queued-result FakeDB; external
clients (TikTok, CF analytics, growth initiatives, celery) patched at their
source modules since several are imported inside the function body.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import analytics as A
from app.api.analytics import (
    InitiativeEventIn,
    SyncAnalyticsRequest,
)
from app.models.content import PostStatus


class _Res:
    def __init__(self, v=None):
        self._v = v

    def scalar(self):
        return self._v[0] if isinstance(self._v, list) else self._v

    def scalar_one_or_none(self):
        return self.scalar()

    def one(self):
        return self.scalar()

    def scalars(self):
        return self

    def first(self):
        return self.scalar()

    def all(self):
        if self._v is None:
            return []
        return self._v if isinstance(self._v, list) else [self._v]


class _DB:
    def __init__(self, *results):
        self._q = list(results)
        self.added = []

    async def execute(self, stmt):
        return _Res(self._q.pop(0) if self._q else None)

    def add(self, o):
        self.added.append(o)

    async def commit(self):
        pass

    async def flush(self):
        pass


_USER = SimpleNamespace(id=uuid.uuid4())


def _team():
    return SimpleNamespace(id=uuid.uuid4())


@pytest.fixture
def _with_team(monkeypatch):
    team = _team()
    monkeypatch.setattr(A, "_team_for_user", AsyncMock(return_value=team))
    monkeypatch.setattr(A, "_follower_count", AsyncMock(return_value=0))
    return team


@pytest.fixture
def _no_team(monkeypatch):
    monkeypatch.setattr(A, "_team_for_user", AsyncMock(return_value=None))


# ── overview / post / account metrics ─────────────────────────────────


@pytest.mark.asyncio
async def test_overview_happy(_with_team):
    acc_id = uuid.uuid4()
    acc = SimpleNamespace(id=acc_id, platform="linkedin")
    now = datetime.now(UTC)
    db = _DB(
        [(PostStatus.PUBLISHED, 3), (PostStatus.DRAFT, 1)],  # post counts
        2,  # scheduled upcoming
        1,  # connected accounts
        [acc],  # accounts
        [SimpleNamespace(social_account_id=acc_id, followers=321)],
        50,  # snap engagement
        900,  # snap impressions
        now,  # last_sync_at
    )
    out = await A.get_overview(_with_team.id, days=30, db=db, current_user=_USER)
    assert out.total_posts == 4 and out.published_posts == 3
    assert out.draft_posts == 1 and out.scheduled_posts == 2
    assert out.total_followers == 321
    assert out.total_engagement == 50 and out.total_impressions == 900
    assert out.last_sync_at == now


@pytest.mark.asyncio
async def test_overview_events_fallback_and_live_followers(_with_team, monkeypatch):
    monkeypatch.setattr(A, "_follower_count", AsyncMock(return_value=77))
    acc_id = uuid.uuid4()
    db = _DB(
        [(PostStatus.PUBLISHED, 1)],
        0,
        1,
        [SimpleNamespace(id=acc_id, platform="tiktok")],
        [],  # no snapshot followers → live call
        0,  # snap engagement 0 → event fallback
        12,  # event engagement
        0,  # snap impressions
        None,  # last sync
    )
    out = await A.get_overview(_with_team.id, days=30, db=db, current_user=_USER)
    assert out.total_followers == 77
    assert out.total_engagement == 12
    A._follower_count.assert_awaited_once()


@pytest.mark.asyncio
async def test_overview_no_team_400(_no_team):
    with pytest.raises(Exception) as e:
        await A.get_overview(uuid.uuid4(), days=30, db=_DB(), current_user=_USER)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_post_metrics_404_and_happy():
    db = _DB(None)
    with pytest.raises(Exception) as e:
        await A.get_post_metrics(uuid.uuid4(), db=db, current_user=_USER)
    assert e.value.status_code == 404

    acct_id = uuid.uuid4()
    target = SimpleNamespace(
        social_account_id=acct_id,
        social_account=SimpleNamespace(platform="threads"),
    )
    post = SimpleNamespace(targets=[target])
    db = _DB(
        post,
        [(acct_id, "impression", 100), (acct_id, "like", 8), (acct_id, "share", 2)],
    )
    out = await A.get_post_metrics(uuid.uuid4(), db=db, current_user=_USER)
    assert len(out) == 1 and out[0].platform == "threads"
    assert out[0].impressions == 100 and out[0].likes == 8 and out[0].shares == 2
    assert out[0].engagement_rate == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_account_metrics_snapshot_path(monkeypatch):
    acct = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4(), platform="linkedin", username="li")
    db = _DB(
        acct,  # account lookup
        4,  # posts_count
        (500, 40),  # current snap .one()
        (250, 20),  # prev snap .one()
        321,  # latest followers
    )
    out = await A.get_account_metrics(acct.id, days=30, db=db, current_user=_USER)
    assert out.followers == 321 and out.posts_count == 4
    assert out.total_impressions == 500 and out.total_engagement == 40
    assert out.impressions_change_pct == 100.0
    assert out.engagement_change_pct == 100.0


@pytest.mark.asyncio
async def test_account_metrics_events_fallback_and_live_followers(monkeypatch):
    acct = SimpleNamespace(id=uuid.uuid4(), team_id=uuid.uuid4(), platform="twitter", username="tw")
    monkeypatch.setattr(A, "_follower_count", AsyncMock(return_value=55))
    db = _DB(
        acct,
        2,
        (0, 0),  # no snapshots → events path
        (0, 0),
        [("impression", 200), ("like", 5)],  # current events
        [("impression", 100)],  # prev events
        None,  # no follower snapshot → live
    )
    out = await A.get_account_metrics(acct.id, days=30, db=db, current_user=_USER)
    assert out.followers == 55
    assert out.total_impressions == 200 and out.total_engagement == 5
    assert out.impressions_change_pct == 100.0


@pytest.mark.asyncio
async def test_account_metrics_404():
    with pytest.raises(Exception) as e:
        await A.get_account_metrics(uuid.uuid4(), days=30, db=_DB(None), current_user=_USER)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_account_insights_grouping():
    acct = SimpleNamespace(id=uuid.uuid4(), platform="threads", username="th")
    ts = datetime.now(UTC)
    ev = SimpleNamespace(event_type="account_insights", occurred_at=ts, meta_data={"views": 10})
    ev2 = SimpleNamespace(event_type="profile_sync", occurred_at=ts, meta_data={"followers_count": 9})
    db = _DB(
        acct,
        [ev, ev2],  # events scalars().all()
        [(ts, 100), (ts, 110)],  # follower tuples .all()
    )
    out = await A.get_account_insights(acct.id, days=90, db=db, current_user=_USER)
    assert out["latest"]["account_insights"]["views"] == 10
    assert out["latest"]["profile_sync"]["followers_count"] == 9
    assert len(out["follower_trend"]) == 2
    assert out["platform"] == "threads"


# ── insights / top-posts / trends / followers / platforms ──────────────


@pytest.mark.asyncio
async def test_team_insights(_with_team, monkeypatch):
    monkeypatch.setattr(A, "build_team_insights", AsyncMock(return_value={"ok": 1}))
    out = await A.get_team_insights(_with_team.id, days=90, db=_DB(), current_user=_USER)
    assert out == {"ok": 1}


@pytest.mark.asyncio
async def test_team_insights_404(_no_team):
    with pytest.raises(Exception) as e:
        await A.get_team_insights(uuid.uuid4(), days=90, db=_DB(), current_user=_USER)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_top_posts_snapshots_path(_with_team):
    snap = SimpleNamespace(
        post_id=uuid.uuid4(),
        id=uuid.uuid4(),
        platform="linkedin",
        impressions=100,
        engagement=10,
        reach=80,
        raw=None,
        source="api",
        platform_post_id="urn:1",
    )
    org_snap = SimpleNamespace(
        post_id=None,
        id=uuid.uuid4(),
        platform="linkedin",
        impressions=999,
        engagement=1,
        reach=0,
        raw=None,
        source="linkedin_org_lifetime",
        platform_post_id="urn:2",
    )
    db = _DB([(snap, "hello post"), (org_snap, None)])
    out = await A.get_top_posts(_with_team.id, limit=10, days=30, db=db, current_user=_USER)
    assert len(out) == 1  # org_lifetime filtered out
    assert out[0].content_text == "hello post" and out[0].impressions == 100


@pytest.mark.asyncio
async def test_top_posts_events_fallback(_with_team):
    pid = uuid.uuid4()
    db = _DB(
        [],  # no snapshots
        [(pid, "text", "twitter", None, 300, 12)],
    )
    out = await A.get_top_posts(_with_team.id, limit=10, days=30, db=db, current_user=_USER)
    assert out[0].post_id == pid and out[0].engagement == 12
    assert out[0].engagement_rate == pytest.approx(0.04)


@pytest.mark.asyncio
async def test_engagement_trends(_with_team):
    day = datetime.now(UTC)
    db = _DB(
        [(day, "impression", 100), (day, "like", 4), (day, "unknown", 9)],
        [(day, 60)],
    )
    out = await A.get_engagement_trends(_with_team.id, days=30, db=db, current_user=_USER)
    assert len(out) == 1
    assert out[0].impressions == 60  # snapshot value wins the day bucket
    assert out[0].likes == 4 and out[0].total == 4


@pytest.mark.asyncio
async def test_engagement_trends_no_team(_no_team):
    out = await A.get_engagement_trends(uuid.uuid4(), days=30, db=_DB(), current_user=_USER)
    assert out == []


@pytest.mark.asyncio
async def test_follower_counts(_with_team, monkeypatch):
    acc_synced = SimpleNamespace(id=uuid.uuid4(), platform="linkedin", username="li", status="active")
    acc_live = SimpleNamespace(id=uuid.uuid4(), platform="tiktok", username="tt", status="active")
    monkeypatch.setattr(A, "_follower_count", AsyncMock(return_value=42))
    ts = datetime.now(UTC) - timedelta(days=3)
    db = _DB(
        [acc_synced, acc_live],  # accounts
        [(acc_synced.id, ts, 100), (acc_synced.id, datetime.now(UTC), 110)],
        [(acc_synced.id, 90)],  # baseline rows
    )
    out = await A.get_follower_counts(_with_team.id, days=30, db=db, current_user=_USER)
    assert len(out) == 2
    li = next(r for r in out if r.platform == "linkedin")
    tt = next(r for r in out if r.platform == "tiktok")
    assert li.current == 110 and li.change == 20
    assert tt.current == 42 and tt.series[0].followers == 42


@pytest.mark.asyncio
async def test_platform_metrics(_with_team):
    acc = SimpleNamespace(platform="threads")
    db = _DB(
        [acc],
        [("threads", PostStatus.PUBLISHED, 3), ("threads", PostStatus.SCHEDULED, 1)],
        [("threads", "impression", 50), ("threads", "like", 4)],
        [("threads", 200, 150, 12)],  # platform, imp, reach, eng
    )
    out = await A.get_platform_metrics(_with_team.id, days=30, db=db, current_user=_USER)
    assert len(out) == 1
    p = out[0]
    assert p.posts_count == 4 and p.published_count == 3 and p.scheduled_count == 1
    assert p.total_impressions == 200 and p.total_engagement == 12
    assert p.engagement_rate == pytest.approx(0.06)


@pytest.mark.asyncio
async def test_platform_metrics_empty(_with_team):
    out = await A.get_platform_metrics(_with_team.id, days=30, db=_DB([]), current_user=_USER)
    assert out == []


# ── pipeline / export / sync / snapshots ───────────────────────────────


@pytest.mark.asyncio
async def test_publish_pipeline(_with_team):
    day = datetime.now(UTC)
    post = SimpleNamespace(
        id=uuid.uuid4(),
        content_text="upcoming",
        scheduled_at=day,
        targets=[SimpleNamespace(social_account=SimpleNamespace(platform="x"))],
    )
    db = _DB(
        [(A.QueueStatus.PENDING, 2), (A.QueueStatus.PROCESSING, 1)],  # queue
        1,  # stuck processing
        [("linkedin", "published", 5), ("linkedin", "failed", 1)],
        [("linkedin", day)],  # last published
        [("linkedin", "token expired", day)],  # last error
        [(day, "linkedin", 5)],  # daily volume
        [("linkedin", "cu", "active", day)],  # accounts
        [post],  # upcoming scalars().all()
    )
    out = await A.get_publish_pipeline(_with_team.id, days=30, db=db, current_user=_USER)
    assert out.queue.pending == 2 and out.queue.stuck_processing == 1
    assert out.platforms[0].platform == "linkedin"
    assert out.platforms[0].success_rate == pytest.approx(5 / 6)
    assert out.platforms[0].last_error == "token expired"
    assert out.daily[0].published == 5
    assert out.accounts[0].username == "cu"
    assert out.upcoming[0].platforms == ["x"]


@pytest.mark.asyncio
async def test_export_report_csv_and_json(_with_team):
    pid = uuid.uuid4()
    ts = datetime.now(UTC)
    row = (pid, "linkedin", PostStatus.PUBLISHED, ts, ts, 100, 5, 2, 1, 3)
    out = await A.export_report(
        _with_team.id, format="csv", days=30, platform=None, account_id=None, post_id=None, start_date=None, end_date=None, db=_DB([row]), current_user=_USER
    )
    assert out.media_type == "text/csv"
    assert "post_id" in out.body.decode() and str(pid) in out.body.decode()

    out = await A.export_report(
        _with_team.id, format="json", days=30, platform=None, account_id=None, post_id=None, start_date=None, end_date=None, db=_DB([row]), current_user=_USER
    )
    assert out.media_type == "application/json"
    import json

    body = json.loads(out.body.decode())
    assert body[0]["engagement_rate"] == pytest.approx(0.11)
    assert body[0]["status"] == "published"


@pytest.mark.asyncio
async def test_trigger_sync_async_and_sync(_with_team, monkeypatch):
    task = SimpleNamespace(delay=lambda *a: SimpleNamespace(id="task-1"))
    monkeypatch.setattr(A, "sync_team_analytics_task", task)
    out = await A.trigger_analytics_sync(_with_team.id, body=SyncAnalyticsRequest(async_mode=True), db=_DB(), current_user=_USER)
    assert out.status == "queued" and out.task_id == "task-1"

    monkeypatch.setattr(A, "sync_team_analytics", AsyncMock(return_value=SimpleNamespace(synced=3, skipped=1, errors=[], snapshots=[])))
    out = await A.trigger_analytics_sync(_with_team.id, body=SyncAnalyticsRequest(async_mode=False), db=_DB(), current_user=_USER)
    assert out.status == "completed" and out.synced == 3 and out.skipped == 1


@pytest.mark.asyncio
async def test_list_snapshots(_with_team):
    out = await A.list_analytics_snapshots(_with_team.id, days=30, post_id=None, limit=100, db=_DB(["row"]), current_user=_USER)
    assert out == ["row"]


@pytest.mark.asyncio
async def test_list_snapshots_no_team(_no_team):
    out = await A.list_analytics_snapshots(uuid.uuid4(), days=30, post_id=None, limit=100, db=_DB(), current_user=_USER)
    assert out == []


# ── tiktok videos / bots / cloudflare / ads / initiatives ──────────────


@pytest.mark.asyncio
async def test_tiktok_videos(_with_team, monkeypatch):
    acct = SimpleNamespace(access_token_enc="enc", account_id="open1")
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok")
    client = SimpleNamespace(
        list_videos=AsyncMock(return_value={"data": {"videos": [{"id": "v1", "view_count": 9, "title": "t"}], "cursor": 4, "has_more": True}})
    )
    import app.services.tiktok_api as tt

    monkeypatch.setattr(tt, "TikTokAPIClient", lambda **kw: client)
    db = _DB(acct)
    out = await A.list_tiktok_videos(_with_team.id, account_id=uuid.uuid4(), cursor=0, max_count=20, db=db, current_user=_USER)
    assert out.videos[0].id == "v1" and out.videos[0].view_count == 9
    assert out.cursor == 4 and out.has_more is True


@pytest.mark.asyncio
async def test_tiktok_videos_not_found(_with_team):
    with pytest.raises(Exception) as e:
        await A.list_tiktok_videos(_with_team.id, account_id=uuid.uuid4(), cursor=0, max_count=20, db=_DB(None), current_user=_USER)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_bot_analytics_summary(_with_team):
    day = datetime.now(UTC)
    rows = [
        SimpleNamespace(provider="dmr", language="greek", guardrail="", success="true", latency_ms=100, social_account_id=uuid.uuid4(), day=day, reply_count=3),
        SimpleNamespace(
            provider="dmr", language="english", guardrail="pricing", success="false", latency_ms=200, social_account_id=None, day=day, reply_count=2
        ),
    ]
    out = await A.get_bot_analytics_summary(_with_team.id, days=30, db=_DB(rows), current_user=_USER)
    assert out.total_replies == 5 and out.successful_replies == 3
    assert out.failed_replies == 2 and out.guardrail_triggers == 2
    assert out.pricing_guardrail_triggers == 2
    assert out.greek_replies == 3 and out.english_replies == 2
    assert out.avg_latency_ms == 140  # (100*3 + 200*2)/5
    assert out.by_provider[0]["provider"] == "dmr"
    assert out.by_day[0]["replies"] == 3 and out.by_day[0]["errors"] == 2


@pytest.mark.asyncio
async def test_cloudflare_ai_usage(monkeypatch):
    import app.services.cf_analytics as cf

    monkeypatch.setattr(
        cf, "get_workers_ai_usage", AsyncMock(return_value={"total_requests": 10, "total_neurons": 5000, "total_errors": 1, "by_model": [], "by_day": []})
    )
    out = await A.get_cloudflare_ai_usage(days=2, current_user=_USER)
    assert out.free_tier_limit == 20000
    assert out.free_tier_remaining == 15000


@pytest.mark.asyncio
async def test_ad_campaigns(_with_team):
    from app.models.linkedin_ads import AdCampaignSnapshot

    ts = datetime.now(UTC)
    s1 = AdCampaignSnapshot(
        id=uuid.uuid4(),
        team_id=_with_team.id,
        campaign_id="c1",
        campaign_name="Boost",
        platform="linkedin",
        status="ACTIVE",
        captured_at=ts - timedelta(days=1),
        spend_eur=10.0,
        impressions=1000,
        clicks=20,
        engagements=30,
        ctr=2.0,
        cpc_eur=0.5,
        engagement_rate=3.0,
        budget_eur=100.0,
    )
    s2 = AdCampaignSnapshot(
        id=uuid.uuid4(),
        team_id=_with_team.id,
        campaign_id="c1",
        campaign_name="Boost",
        platform="linkedin",
        status="ACTIVE",
        captured_at=ts,
        spend_eur=15.0,
        impressions=1500,
        clicks=25,
        engagements=40,
        ctr=1.6,
        cpc_eur=0.6,
        engagement_rate=2.7,
        budget_eur=100.0,
    )
    out = await A.get_ad_campaigns(_with_team.id, days=30, db=_DB([s1, s2]), current_user=_USER)
    assert out.totals.campaigns == 1
    assert out.totals.spend_eur == 15.0  # max, not sum
    assert out.totals.impressions == 1500
    c = out.campaigns[0]
    assert c.latest.budget_eur == 100.0 and len(c.series) == 2


@pytest.mark.asyncio
async def test_record_initiative_event(_with_team, monkeypatch):
    import app.services.growth_initiatives as gi

    monkeypatch.setattr(gi, "INITIATIVE_TYPES", {"linkedin_page_invite"})
    rec = AsyncMock()
    monkeypatch.setattr(gi, "record_initiative_event", rec)
    monkeypatch.setattr(gi, "initiative_summary", AsyncMock(return_value=[{"event_type": "linkedin_page_invite", "platform": "linkedin", "units": 5}]))
    acct_id = uuid.uuid4()
    db = _DB(acct_id)  # social_account_id validation hit
    body = InitiativeEventIn(units=5, social_account_id=acct_id)
    out = await A.record_initiative_event(body, _with_team.id, db=db, current_user=_USER)
    assert out["units"] == 5
    rec.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_initiative_event_org_default(_with_team, monkeypatch):
    import app.services.growth_initiatives as gi

    monkeypatch.setattr(gi, "INITIATIVE_TYPES", {"linkedin_page_invite"})
    monkeypatch.setattr(gi, "record_initiative_event", AsyncMock())
    monkeypatch.setattr(gi, "initiative_summary", AsyncMock(return_value=[{"event_type": "linkedin_page_invite", "platform": "linkedin"}]))
    org_id = uuid.uuid4()
    db = _DB(org_id)  # org fallback lookup
    body = InitiativeEventIn(units=2)
    await A.record_initiative_event(body, _with_team.id, db=db, current_user=_USER)
    assert body.social_account_id == org_id


@pytest.mark.asyncio
async def test_record_initiative_event_bad_type_400():
    with pytest.raises(Exception) as e:
        await A.record_initiative_event(InitiativeEventIn(event_type="bogus"), uuid.uuid4(), db=_DB(), current_user=_USER)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_list_initiatives(_with_team, monkeypatch):
    import app.services.growth_initiatives as gi

    monkeypatch.setattr(gi, "initiative_summary", AsyncMock(return_value=[{"x": 1}]))
    out = await A.list_initiatives(_with_team.id, days=30, db=_DB(), current_user=_USER)
    assert out == [{"x": 1}]
