"""IntraVox service-feed: CRM clients + email-campaign feeds, and the
no-team / bad-config branches of the existing feeds.

Reuses the request/settings/DB helpers from test_service_feed.py. EspoCRM is
mocked; the feed contract (no raw emails, never 500 the widget) is asserted.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Response

import app.api.service_feed as feed_api
from app.models.email_campaign import CampaignKind, CampaignStatus
from app.services import espocrm_client
from tests.unit.test_service_feed import TEAM_ID, _fake_db, _request, _Result, _settings


def _ordered_db(*results):
    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=[_Result(r) for r in results])
    return db


class _ScalarResult(_Result):
    def scalar(self):
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_invalid_pinned_team_id_is_500():
    with _settings(team_id="not-a-uuid"):
        with pytest.raises(HTTPException) as e:
            await feed_api.feed_activity(_request(), response=Response(), limit=5, db=_fake_db())
    assert e.value.status_code == 500 and "INTRANET_FEED_TEAM_ID" in e.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("feed,source", [
    ("feed_digest", "SocialAuto daily digest"),
    ("feed_status", "SocialAuto status"),
    ("feed_leads", "SocialAuto leads"),
    ("feed_campaigns", "Email campaigns"),
])
async def test_feeds_without_team_are_empty(feed, source):
    db = _fake_db(team=None, team_counts=[])
    kwargs = {"db": db}
    if feed == "feed_digest":
        kwargs["days"] = 1
    elif feed in ("feed_leads", "feed_campaigns"):
        kwargs["limit"] = 5
    with _settings():
        out = await getattr(feed_api, feed)(_request(), response=Response(), **kwargs)
    assert out.source == source and out.items == []


class TestClientsFeed:
    @pytest.mark.asyncio
    async def test_unconfigured_returns_marker_item(self, monkeypatch):
        monkeypatch.setattr(espocrm_client, "configured", lambda: False)
        response = Response()
        with _settings():
            out = await feed_api.feed_clients(_request(), response=response, limit=10)
        assert [i.title for i in out.items] == ["CRM not configured"]
        assert response.headers["cache-control"] == "private, max-age=300"

    @pytest.mark.asyncio
    async def test_maps_contacts_and_leads_without_emails(self, monkeypatch):
        monkeypatch.setattr(espocrm_client, "configured", lambda: True)
        monkeypatch.setattr(espocrm_client, "list_contacts", AsyncMock(return_value=[
            {"id": "c1", "firstName": "Ann", "lastName": "Lee", "accountName": "Acme",
             "title": "CTO", "emailAddress": "ann@acme.test"},
            {"id": "c2", "accountName": "Solo", "emailAddress": "solo@x.test"},
            {"id": "c3"},
            {"id": "c4", "firstName": "Over", "emailAddress": "limit@x.test"},
        ]))
        monkeypatch.setattr(espocrm_client, "list_leads_for_campaign", AsyncMock(return_value=[
            {"id": "l1", "firstName": "Lou", "status": "New", "source": "Web",
             "createdAt": "2026-10-10 08:00:00", "emailAddress": "lou@x.test"},
            {"id": "l2"},
        ]))
        with _settings():
            out = await feed_api.feed_clients(_request(), response=Response(), limit=3)
        titles = [i.title for i in out.items]
        assert titles == ["Ann Lee — Acme", "Solo — Solo", "Contact — contact",
                          "Lou — New", "Lead — lead"]
        assert out.items[0].url == "https://espocrm.cloudless.gr/#Contact/view/c1"
        assert out.items[0].excerpt == "CTO · CRM Contact"
        assert out.items[3].url.endswith("/#Lead/view/l1")
        assert out.items[3].excerpt == "source: Web · CRM Lead"
        assert out.items[3].date == "2026-10-10 08:00:00"
        assert out.items[4].excerpt == "CRM Lead"
        assert "@" not in out.model_dump_json()

    @pytest.mark.asyncio
    async def test_custom_ui_url_and_outage(self, monkeypatch):
        monkeypatch.setattr(espocrm_client, "configured", lambda: True)
        monkeypatch.setattr(espocrm_client, "list_contacts", AsyncMock(side_effect=TimeoutError()))
        settings = SimpleNamespace(INTRANET_FEED_TOKEN="s3cret", INTRANET_FEED_TEAM_ID="",
                                   ESPOCRM_UI_URL="https://crm.example/")
        with patch.object(feed_api, "get_settings", return_value=settings):
            out = await feed_api.feed_clients(_request(), response=Response(), limit=3)
        assert [(i.title, i.excerpt) for i in out.items] == [("CRM temporarily unreachable", "TimeoutError")]


class TestCampaignsFeed:
    @pytest.mark.asyncio
    async def test_counts_and_survey_responses(self):
        when = datetime(2026, 10, 12, tzinfo=UTC)
        survey = SimpleNamespace(id=uuid.uuid4(), name="Onboarding survey", kind=CampaignKind.survey,
                                 status=CampaignStatus.sent, created_at=when)
        promo = SimpleNamespace(id=uuid.uuid4(), name="Promo", kind=CampaignKind.marketing,
                                status=CampaignStatus.draft, created_at=when)
        empty_survey = SimpleNamespace(id=uuid.uuid4(), name="Fresh", kind=CampaignKind.survey,
                                       status=CampaignStatus.draft, created_at=when)
        team = SimpleNamespace(id=TEAM_ID, name="T")
        db = SimpleNamespace()
        db.execute = AsyncMock(side_effect=[
            _Result([team]),                          # pinned team
            _Result([survey, promo, empty_survey]),   # campaigns
            _Result([("sent", 5), ("failed", 1)]), _ScalarResult([3]),
            _Result([]), _ScalarResult([None]),
            _Result([]), _ScalarResult([0]),
        ])
        with _settings(team_id=str(TEAM_ID)):
            out = await feed_api.feed_campaigns(_request(), response=Response(), limit=20, db=db)
        assert [i.title for i in out.items] == ["Onboarding survey — sent", "Promo — draft", "Fresh — draft"]
        assert out.items[0].excerpt == "failed:1 · sent:5 · responses:3"
        assert out.items[0].author == "survey"
        assert out.items[1].excerpt == "no recipients yet"
        assert out.items[2].excerpt == "responses:0"
        assert out.items[0].date == when.isoformat()

    @pytest.mark.asyncio
    async def test_no_campaigns_hint(self):
        team = SimpleNamespace(id=TEAM_ID, name="T")
        db = _ordered_db([team], [])
        with _settings(team_id=str(TEAM_ID)):
            out = await feed_api.feed_campaigns(_request(), response=Response(), limit=20, db=db)
        assert [i.title for i in out.items] == ["No campaigns yet"]

    @pytest.mark.asyncio
    async def test_requires_token(self):
        with _settings():
            with pytest.raises(HTTPException) as e:
                await feed_api.feed_campaigns(_request("bad"), response=Response(), limit=5, db=_fake_db())
        assert e.value.status_code == 401
