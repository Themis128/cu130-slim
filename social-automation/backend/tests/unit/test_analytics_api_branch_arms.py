"""Remaining reachable branch arms in app/api/analytics.py.

Covers:
- a feed card whose time line has no body after it
- an X snapshot with no id
- follower fetchers returning empty or zero (LinkedIn org, FB personal
  sidecar, Threads metric rows with empty values, TikTok sidecar
  without stats), which must give -1 and not a false 0
- the latest-snapshot subquery without the date and post-only filters
- account insights keeping the newest event of each type
- platform metrics for a status that is neither published nor scheduled
- an empty CSV export
- the bot summary with non-pricing guardrails, zero latency, a repeat
  account and day-less rows

Reuses the fakes from the existing analytics suites. There is no
network and no DB."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api import analytics as A
from app.models.content import PostStatus
from tests.unit.test_analytics_api_endpoints import _DB, _USER, _with_team  # noqa: F401 — fixture
from tests.unit.test_analytics_api_helpers import _HTTP, _account, _Resp, http  # noqa: F401


def test_feed_card_time_line_without_body_falls_back_to_chrome_filter():
    out = A._strip_feed_card_chrome("Feed post number 1\nCloudless\n2d •\nFollow")
    assert "Feed post number" not in out and "Follow" not in out
    assert out.startswith("Cloudless")


def test_twitter_snapshot_without_any_id_has_no_url():
    assert A._snapshot_external_url("twitter", {}, None) is None


@pytest.mark.asyncio
async def test_linkedin_zero_count_is_unknown(monkeypatch):
    class C:
        def __init__(self, **kw):
            pass

        async def get_follower_count(self, urn):
            return 0

    monkeypatch.setattr(A, "LinkedInAPIClient", C)
    assert await A._linkedin_follower_count(_account("linkedin", account_id="42")) == -1


@pytest.mark.asyncio
async def test_facebook_personal_sidecar_without_followers(monkeypatch):
    class SC:
        def __init__(self, **kw):
            pass

        async def get_profile_stats(self, expected_name=None):
            return {"name": "Themis"}

    monkeypatch.setattr("app.services.facebook_sidecar.FacebookSidecarClient", SC)
    assert await A._facebook_follower_count(_account("facebook", meta_data={"account_type": "user"})) == -1


@pytest.mark.asyncio
@pytest.mark.usefixtures("http")
async def test_threads_skips_metric_row_with_empty_values():
    _HTTP.response = _Resp(200, {"data": [
        {"name": "followers_count", "values": []},
        {"name": "followers_count", "total_value": {"value": 5}},
    ]})
    assert await A._threads_follower_count(_account("threads")) == 5


@pytest.mark.asyncio
@pytest.mark.usefixtures("http")
async def test_tiktok_sidecar_without_follower_stat():
    _HTTP.response = _Resp(200, {"data": {"user": {}}, "stats": {"likes": 3}})
    assert await A._tiktok_follower_count(_account("tiktok", username="@cl")) == -1
    assert any("profile/videos" in u for u in _HTTP.requests)


def _sql(stmt) -> str:
    return " ".join(str(stmt.compile()).split()).lower()


def test_latest_snapshot_subq_filters_toggle():
    team = uuid.uuid4()
    bare = _sql(A._latest_snapshot_ids_subq(team))
    assert "captured_at >=" not in bare and "captured_at <" not in bare
    assert "is distinct from" not in bare and "notlike" not in bare and "not like" not in bare
    full = _sql(A._latest_snapshot_ids_subq(team, datetime.now(UTC), posts_only=True))
    assert "captured_at >=" in full and "is distinct from" in full


@pytest.mark.asyncio
async def test_account_insights_keeps_newest_event_per_type():
    acct = SimpleNamespace(id=uuid.uuid4(), platform="instagram", username="cl")
    now = datetime.now(UTC)
    newest = SimpleNamespace(event_type="account_insights", occurred_at=now, meta_data={"reach": 20})
    older = SimpleNamespace(event_type="account_insights", occurred_at=now - timedelta(days=1), meta_data={"reach": 10})
    out = await A.get_account_insights(acct.id, days=30, db=_DB(acct, [newest, older], []), current_user=_USER)
    assert out["latest"]["account_insights"]["reach"] == 20
    assert [h["reach"] for h in out["history"]["account_insights"]] == [20, 10]


@pytest.mark.asyncio
async def test_platform_metrics_counts_draft_only_in_total(request):
    team = request.getfixturevalue("_with_team")
    db = _DB(
        [SimpleNamespace(platform="threads")],
        [("threads", PostStatus.DRAFT, 2), ("threads", PostStatus.PUBLISHED, 1)],
        [],
        [("threads", 0, 0, 0)],
    )
    p = (await A.get_platform_metrics(team.id, days=30, db=db, current_user=_USER))[0]
    assert p.posts_count == 3 and p.published_count == 1 and p.scheduled_count == 0


@pytest.mark.asyncio
async def test_export_csv_with_no_rows_is_empty(request):
    team = request.getfixturevalue("_with_team")
    out = await A.export_report(team.id, format="csv", days=30, platform=None, account_id=None, post_id=None,
                                start_date=None, end_date=None, db=_DB([]), current_user=_USER)
    assert out.media_type == "text/csv" and out.body == b""


@pytest.mark.asyncio
async def test_bot_summary_other_guardrail_zero_latency_and_dayless_rows(request):
    team = request.getfixturevalue("_with_team")
    acct = uuid.uuid4()
    day = datetime.now(UTC)
    rows = [
        SimpleNamespace(provider="cf", language="english", guardrail="profanity", success="true", latency_ms=0,
                        social_account_id=acct, day=None, reply_count=2),
        SimpleNamespace(provider="cf", language="english", guardrail="", success="true", latency_ms=50,
                        social_account_id=acct, day=day, reply_count=1),
    ]
    out = await A.get_bot_analytics_summary(team.id, days=30, db=_DB(rows), current_user=_USER)
    assert out.guardrail_triggers == 2 and out.pricing_guardrail_triggers == 0
    assert out.avg_latency_ms == 50  # zero-latency rows don't drag the mean
    assert len(out.by_account) == 1 and out.by_account[0]["replies"] == 3
    assert len(out.by_day) == 1 and out.by_day[0]["replies"] == 1

