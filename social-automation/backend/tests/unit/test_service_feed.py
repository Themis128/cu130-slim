"""Tests for the IntraVox service-feed endpoints (X-Service-Token auth).

The DB session is the only stubbed boundary — a fake AsyncSession that
dispatches on the queried entities. Everything else (token check, team
selection, item mapping) runs the real code path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Request, Response

import app.api.service_feed as feed_api
from app.services.slack_digest import DigestIssue, DigestReport

TEAM_ID = uuid.uuid4()


def _request(token: str = "s3cret") -> Request:
    return Request(
        scope={
            "type": "http",
            "headers": [(b"x-service-token", token.encode()), (b"host", b"test")],
        }
    )


def _settings(token: str = "s3cret", team_id: str = ""):
    return patch.object(
        feed_api,
        "get_settings",
        return_value=SimpleNamespace(INTRANET_FEED_TOKEN=token, INTRANET_FEED_TEAM_ID=team_id),
    )


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


def _fake_db(*, team=None, team_counts=None, targets=None, media=None, queue=None, attention=None, leads=None):
    """AsyncSession stand-in dispatching on the selected entities."""
    team = team if team is not None else SimpleNamespace(id=TEAM_ID, name="T")

    async def execute(stmt):
        s = str(stmt).lower()
        if "post_targets" in s:
            return _Result(targets or [])
        if "media_assets" in s:
            return _Result(media or [])
        if "publish_queue" in s:
            return _Result(queue or [])
        if " leads" in s or "from leads" in s:
            return _Result(leads or [])
        if "social_accounts" in s:
            # select(team_id, count()) → auto team pick; select(SocialAccount)
            # → unhealthy-account list.
            return _Result(team_counts if "count(" in s else attention or [])
        if "teams" in s:
            return _Result([team] if team else [])
        return _Result([])

    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=execute)
    return db


@pytest.mark.asyncio
async def test_503_when_token_unconfigured():
    with _settings(token=""):
        with pytest.raises(HTTPException) as e:
            await feed_api.feed_index(_request("s3cret"), response=Response())
    assert e.value.status_code == 503
    assert "INTRANET_FEED_TOKEN" in e.value.detail


@pytest.mark.asyncio
async def test_401_on_missing_or_wrong_token():
    with _settings():
        with pytest.raises(HTTPException) as missing:
            await feed_api.feed_index(_request(""), response=Response())
        with pytest.raises(HTTPException) as wrong:
            await feed_api.feed_index(_request("nope"), response=Response())
    assert missing.value.status_code == 401
    assert wrong.value.status_code == 401


@pytest.mark.asyncio
async def test_feed_index_lists_feeds():
    response = Response()
    with _settings():
        out = await feed_api.feed_index(_request(), response=response)
    paths = {f["path"] for f in out["feeds"]}
    assert "/service-feed/activity" in paths
    assert "/service-feed/digest" in paths
    assert "/service-feed/status" in paths
    assert out["mapping_hint"]["items"] == "items"
    assert response.headers["cache-control"] == "private, max-age=300"


def _post_target_row(status="published", error=None, platform="linkedin"):
    post_id = uuid.uuid4()
    post = SimpleNamespace(
        id=post_id,
        team_id=TEAM_ID,
        content_text="Managed cloud hosting = peace of mind for your business",
        media_ids=[uuid.uuid4()],
        updated_at=datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
    )
    target = SimpleNamespace(
        post_id=post_id,
        status=status,
        error_message=error,
        platform_url="https://linkedin.com/feed/x" if status == "published" else None,
        published_at=datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
    )
    account = SimpleNamespace(platform=platform, username="cloudless", display_name="cloudless.gr")
    return target, post, account


@pytest.mark.asyncio
async def test_activity_maps_targets_to_feed_items():
    row_pub = _post_target_row("published")
    row_fail = _post_target_row("failed", error="browser closed", platform="facebook")
    db = _fake_db(
        team_counts=[(TEAM_ID, 11)],
        targets=[row_pub, row_fail],
        media=[],
    )
    with _settings():
        out = await feed_api.feed_activity(_request(), response=Response(), limit=20, db=db)
    assert len(out.items) == 2
    pub = out.items[0]
    assert pub.url == "https://linkedin.com/feed/x"
    assert "linkedin" in pub.excerpt
    assert pub.author == "cloudless.gr"
    fail = out.items[1]
    assert "browser closed" in fail.excerpt
    assert fail.url == ""


@pytest.mark.asyncio
async def test_activity_no_team_returns_empty():
    db = _fake_db(team=None, team_counts=[])
    with _settings():
        out = await feed_api.feed_activity(_request(), response=Response(), db=db)
    assert out.items == []


@pytest.mark.asyncio
async def test_pinned_team_id_used():
    db = _fake_db(targets=[])
    with _settings(team_id=str(TEAM_ID)):
        out = await feed_api.feed_activity(_request(), response=Response(), limit=20, db=db)
    assert out.items == []


@pytest.mark.asyncio
async def test_status_maps_queue_and_unhealthy_accounts():
    db = _fake_db(
        team_counts=[(TEAM_ID, 11)],
        queue=[("pending", 2), ("failed", 1)],
        attention=[
            SimpleNamespace(
                platform="linkedin",
                username="tbaltzakis",
                display_name=None,
                status="expired",
            )
        ],
    )
    with _settings():
        out = await feed_api.feed_status(_request(), response=Response(), db=db)
    assert "pending" in out.items[0].title
    assert any("linkedin" in i.title and "expired" in i.title for i in out.items)


@pytest.mark.asyncio
async def test_status_healthy_message_when_no_attention():
    db = _fake_db(team_counts=[(TEAM_ID, 11)], queue=[], attention=[])
    with _settings():
        out = await feed_api.feed_status(_request(), response=Response(), db=db)
    assert out.items[-1].title == "All connected accounts healthy"


@pytest.mark.asyncio
async def test_digest_reshapes_real_report(monkeypatch):
    report = DigestReport(
        generated_at=datetime(2026, 10, 15, 17, 0, tzinfo=UTC),
        timezone="Europe/Athens",
        team_name="Baltzakis Themistoklis's Team",
        days=1,
        overview={
            "total_posts": 18,
            "published_posts": 16,
            "scheduled_posts": 0,
            "draft_posts": 1,
            "failed_posts": 1,
            "connected_accounts": 10,
        },
        impressions_24h=2083,
        engagement_24h=250,
        top_posts=[{"snippet": "Managed cloud hosting", "engagement": 239, "impressions": 1331, "url": "https://x"}],
        issues=[DigestIssue(severity="error", title="A post didn't publish", detail="attempts 3/3")],
    )

    async def fake_build(db, *, team, days):
        assert team.id == TEAM_ID
        return report

    monkeypatch.setattr("app.services.slack_digest.build_daily_digest", fake_build)
    db = _fake_db(team_counts=[(TEAM_ID, 11)])
    with _settings():
        out = await feed_api.feed_digest(_request(), response=Response(), db=db)
    assert "16 published" in out.items[0].title
    assert "2083" in out.items[0].excerpt
    titles = [i.title for i in out.items]
    assert any("Managed cloud" in t for t in titles)
    assert any("post didn't publish" in t for t in titles)


@pytest.mark.asyncio
async def test_leads_feed_never_exposes_email():
    lead = SimpleNamespace(
        name="Jane Doe",
        email="jane@example.com",
        source="website",
        interest="cloud",
        company_size="2-5",
        notes="newsletter — Free Cloud Migration Playbook",
        created_at=datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
    )
    db = _fake_db(team_counts=[(TEAM_ID, 11)], leads=[lead])
    with _settings():
        out = await feed_api.feed_leads(_request(), response=Response(), limit=10, db=db)
    assert len(out.items) == 1
    item = out.items[0]
    assert "Jane Doe" in item.title and "website" in item.title
    assert "jane@example.com" not in item.model_dump_json()
    assert "interest: cloud" in item.excerpt


@pytest.mark.asyncio
async def test_leads_feed_requires_token():
    db = _fake_db(team_counts=[(TEAM_ID, 11)])
    with _settings():
        with pytest.raises(HTTPException) as e:
            await feed_api.feed_leads(_request(""), response=Response(), limit=10, db=db)
    assert e.value.status_code == 401
