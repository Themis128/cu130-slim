"""Remaining branch arms of linkedin_ads_report: sparse scrape pages,
campaign-group/API edge responses, zero-traffic metrics, non-positive
deltas, zero-spend pacing, credit safety for completed/unlisted
campaigns, an org section without follower history, and notebook
attachments that were not produced.

Reuses the DB/HTTP fakes from test_linkedin_ads_report.py; LinkedIn's API,
the sidecar and the notebook runner are all faked."""

from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.linkedin_ads_report as R
from tests.unit.test_linkedin_ads_report import _DB, _Http, _http_resp, _metrics, _Res


def test_parse_overview_without_engagement_table():
    out = R._parse_overview("Spend by objective\n€5.00\n\nClicks\n2")
    assert out["spend_eur"] == 5.0 and out["clicks"] == 2
    assert "engagements" not in out and "ctr" not in out
    assert "ad_set_statuses" not in out


def test_parse_campaign_page_sparse():
    out = R._parse_campaign_page("Lifetime budget: €80.00", "907100946")
    assert out == {"budget_eur": 80.0}


async def _collect(monkeypatch, responses, bundle):
    import app.services.analytics_sync as A

    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {})
    http = _Http(responses)
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: http)
    monkeypatch.setattr(
        A,
        "_fetch_linkedin_ad_stats",
        AsyncMock(return_value={"urn:li:sponsoredCampaign:c1": bundle}),
    )
    m = R.CampaignMetrics(account_id="acct", campaign_id="c1")
    ok = await R._collect_api_metrics(_DB([]), m)
    return ok, m, http


@pytest.mark.asyncio
async def test_api_group_lookup_fails_and_zero_traffic(monkeypatch):
    end_ms = 1_760_054_400_000
    campaign = {"name": "B", "status": "ACTIVE", "runSchedule": {"start": 1_757_529_600_000, "end": end_ms}, "campaignGroup": "urn:li:adCampaignGroup:777"}
    bundle = SimpleNamespace(raw={"costLocal": "0"}, clicks=0, impressions=0, engagement=0)
    ok, m, http = await _collect(
        monkeypatch,
        [_http_resp(200, campaign), _http_resp(404, {}), _http_resp(200, {"elements": []})],
        bundle,
    )
    assert ok is True
    assert any("adCampaignGroups/777" in u for u in http.urls)
    # group lookup failed → campaign end alone sets the schedule end
    assert m.schedule_end == "2025-10-10"
    # no impressions/clicks → derived rates stay unset (no division by zero)
    assert m.ctr == 0 and m.engagement_rate == 0 and m.cpc_eur == 0


@pytest.mark.asyncio
async def test_api_group_without_end_and_campaign_without_end(monkeypatch):
    campaign = {"name": "B", "status": "ACTIVE", "runSchedule": {"start": 1_757_529_600_000}, "campaignGroup": "urn:li:adCampaignGroup:777"}
    bundle = SimpleNamespace(raw={"costLocal": "5"}, clicks=1, impressions=100, engagement=2)
    ok, m, _ = await _collect(
        monkeypatch,
        [_http_resp(200, campaign), _http_resp(200, {"runSchedule": {}}), _http_resp(200, {"elements": []})],
        bundle,
    )
    assert ok is True
    assert not m.schedule_end
    assert m.ctr == 1.0 and m.cpc_eur == 5.0


def test_report_no_positive_deltas_omits_since_line():
    from datetime import UTC, datetime

    prev = SimpleNamespace(spend_eur=40.0, clicks=15, engagements=80, captured_at=datetime.now(UTC))
    out = R.build_report_text(_metrics(status="active", spend_eur=40.0, clicks=15, engagements=80), prev, None)
    assert "Since last report" not in out


def test_report_zero_spend_has_no_pace_line():
    end = date.today() + timedelta(days=20)
    m = _metrics(status="active", spend_eur=0.0, budget_eur=100.0, schedule_start=(date.today() - timedelta(days=3)).isoformat(), schedule_end=end.isoformat())
    out = R.build_report_text(m, None, end)
    assert "*Pace:*" not in out


def test_report_credit_completed_campaign_and_unlisted_campaigns(monkeypatch):
    end = date.today() + timedelta(days=10)
    monkeypatch.setattr(R.get_settings(), "LINKEDIN_ADS_CREDIT_EUR", 200.0)
    # completed campaign: no remaining exposure from it; listing unavailable → unverified
    m = _metrics(status="completed", spend_eur=30.0, budget_eur=0.0, account_spend_eur=30.0, campaign_id="c1", campaign_name="Boost", raw={})
    out = R.build_report_text(m, None, end)
    assert "Promo credit" in out and "€30 of €200" in out
    assert "unverified" in out


@pytest.mark.asyncio
async def test_org_section_without_follower_history():
    account = SimpleNamespace(id=uuid.uuid4())
    db = _DB([_Res(first=account), _Res(all_=[]), _Res(one={}), _Res(one={}), _Res(one={}), _Res(one=None)])
    out = await R.build_org_section(db)
    assert "*Followers:*" not in out


@pytest.mark.asyncio
async def test_notebook_skips_missing_attachments(monkeypatch, tmp_path):
    text_f = tmp_path / "report.txt"
    text_f.write_text("body")
    present = tmp_path / "chart.png"
    present.write_bytes(b"PNG")
    fake_nb = SimpleNamespace(
        run_report_notebook=lambda *a, **kw: {
            "manifest": {
                "text_file": str(text_f),
                "attachments": [
                    {"file": str(tmp_path / "never-rendered.png"), "cid": "x"},
                    {"file": None},
                    {"file": str(present), "cid": "c", "mime": "image/png"},
                ],
            },
            "manifest_path": str(tmp_path / "manifest.json"),
        }
    )
    monkeypatch.setitem(sys.modules, "app.services.notebook_runner", fake_nb)
    out = await R._render_report_notebook(campaign_id="c", status="active", end_date=None, is_final=False, metrics=_metrics(status="active"))
    assert out is not None
    _, _, attachments = out
    assert [a["name"] for a in attachments] == ["chart.png"]


@pytest.mark.asyncio
async def test_api_rollup_when_adanalytics_denied(monkeypatch):
    """adAnalytics 403 + no tracked campaign → inventory rollup with spend flagged unavailable."""
    import app.services.analytics_sync as A

    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {})
    campaigns_body = {
        "elements": [
            {"id": "c1", "name": "Boost", "status": "ACTIVE", "runSchedule": {"start": 1_757_529_600_000}},
            {"id": "c2", "name": "Old", "status": "COMPLETED", "runSchedule": {"start": 1_757_000_000_000}},
        ]
    }
    http = _Http([_http_resp(200, campaigns_body)])
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: http)
    err = SimpleNamespace(notes="403 partnerApiAdAnalytics denied")
    monkeypatch.setattr(A, "_fetch_linkedin_ad_stats", AsyncMock(return_value={"_error": err}))
    m = R.CampaignMetrics(account_id="acct", campaign_id=None)
    ok = await R._collect_api_metrics(_DB([]), m)
    assert ok is True
    assert m.raw["account_rollup"] is True
    assert m.raw["spend_unavailable"] is True
    assert "403" in m.raw["analytics_error"]
    assert m.status == "active"
    assert m.campaign_name == "account rollup (2 campaigns)"
    assert m.raw["source"] == "api"
    # only the adCampaigns inventory call — no per-campaign fetch without camp
    assert len(http.urls) == 1 and "adCampaigns" in http.urls[0]


@pytest.mark.asyncio
async def test_api_account_rollup_aggregates_all_campaigns(monkeypatch):
    """No tracked campaign → account-level spend/clicks/impressions summed across adAnalytics rows."""
    import app.services.analytics_sync as A

    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {})
    campaigns_body = {
        "elements": [
            {"id": "c1", "name": "Boost", "status": "ACTIVE", "runSchedule": {"start": 1_757_529_600_000}},
            {"id": "c2", "name": "Old", "status": "PAUSED", "runSchedule": {"start": 1_757_000_000_000}},
        ]
    }
    http = _Http([_http_resp(200, campaigns_body)])
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: http)
    b1 = SimpleNamespace(raw={"costLocal": "10.5"}, clicks=3, impressions=300, engagement=6)
    b2 = SimpleNamespace(raw={"costLocal": "4.5"}, clicks=2, impressions=100, engagement=4)
    stats = {"urn:li:sponsoredCampaign:c1": b1, "urn:li:sponsoredCampaign:c2": b2}
    monkeypatch.setattr(A, "_fetch_linkedin_ad_stats", AsyncMock(return_value=stats))
    m = R.CampaignMetrics(account_id="acct", campaign_id=None)
    ok = await R._collect_api_metrics(_DB([]), m)
    assert ok is True
    assert m.spend_eur == 15.0
    assert m.clicks == 5
    assert m.impressions == 400
    assert m.raw["account_rollup"] is True
    assert m.raw["spend_by_campaign"] == {"c1": 10.5, "c2": 4.5}
    assert m.account_spend_eur == 15.0
    assert m.campaign_name == "account rollup (2 campaigns)"
    assert m.status == "active"


def test_report_spend_unavailable_note():
    m = _metrics(status="active", spend_eur=0.0, raw={"spend_unavailable": True, "analytics_error": "403 denied"})
    out = R.build_report_text(m, None, None)
    assert "unavailable" in out.lower()
    assert "denied adAnalytics" in out


def test_eur_int_unparseable_values():
    # regex matches but float()/int() still reject the captured text
    assert R._eur("x\n€1.2.3", r"€([\d.,]+)") == 0.0
    assert R._int("Clicks\n,,,", r"Clicks\s*\n\s*([\d,]+)") == 0


def test_report_engagement_rate_and_budget_reached():
    out = R.build_report_text(_metrics(status="active", engagement_rate=2.5), None, None)
    assert "ER 2.50%" in out
    end = date.today() + timedelta(days=30)
    m = _metrics(
        status="active",
        spend_eur=100.0,
        budget_eur=100.0,
        schedule_start=(date.today() - timedelta(days=1)).isoformat(),
        schedule_end=end.isoformat(),
    )
    out2 = R.build_report_text(m, None, end)
    assert "lifetime budget reached" in out2


def test_report_pace_nan_spend_falls_through():
    # NaN spend is truthy but fails every numeric comparison — no pace line
    end = date.today() + timedelta(days=30)
    m = _metrics(
        status="active",
        spend_eur=float("nan"),
        budget_eur=100.0,
        schedule_start=(date.today() - timedelta(days=1)).isoformat(),
        schedule_end=end.isoformat(),
    )
    out = R.build_report_text(m, None, end)
    assert "*Pace:*" not in out


def test_report_credit_uncapped_and_capped_other_campaigns(monkeypatch):
    end = date.today() + timedelta(days=30)
    monkeypatch.setattr(R.get_settings(), "LINKEDIN_ADS_CREDIT_EUR", 500.0)
    m = _metrics(
        status="active",
        spend_eur=10.0,
        budget_eur=50.0,
        account_spend_eur=60.0,
        campaign_id="c1",
        raw={
            "campaigns": [
                {"id": "c1", "status": "active", "budget": 50.0},
                {"id": "c2", "status": "paused", "name": "NoCap"},
                {"id": "c3", "status": "active", "budget": 30.0},
            ],
            "spend_by_campaign": {"c1": 10.0, "c3": 5.0},
        },
    )
    out = R.build_report_text(m, None, end)
    assert "unverified" in out and "NoCap" in out


@pytest.mark.asyncio
async def test_api_campaigns_list_http_error(monkeypatch):
    campaign = {
        "name": "B",
        "status": "ACTIVE",
        "runSchedule": {"start": 1_757_529_600_000},
        "campaignGroup": "urn:li:adCampaignGroup:777",
    }
    bundle = SimpleNamespace(raw={"costLocal": "1"}, clicks=0, impressions=0, engagement=0)
    ok, m, http = await _collect(
        monkeypatch,
        [_http_resp(200, campaign), _http_resp(200, {"runSchedule": {}}), _http_resp(503, {})],
        bundle,
    )
    assert ok is True
    assert "503" in m.raw["campaigns_note"]


@pytest.mark.asyncio
async def test_api_stats_error_with_campaign_raises(monkeypatch):
    import app.services.analytics_sync as A

    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {})
    campaign = {
        "name": "B",
        "status": "ACTIVE",
        "runSchedule": {"start": 1_757_529_600_000},
        "campaignGroup": "urn:li:adCampaignGroup:777",
    }
    http = _Http([_http_resp(200, campaign), _http_resp(200, {"runSchedule": {}}), _http_resp(200, {"elements": []})])
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: http)
    err = SimpleNamespace(notes="403 denied")
    monkeypatch.setattr(A, "_fetch_linkedin_ad_stats", AsyncMock(return_value={"_error": err}))
    m = R.CampaignMetrics(account_id="a", campaign_id="c1")
    with pytest.raises(RuntimeError, match="adAnalytics"):
        await R._collect_api_metrics(_DB([]), m)


@pytest.mark.asyncio
async def test_api_rollup_skips_underscore_keys(monkeypatch):
    import app.services.analytics_sync as A

    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {})
    campaigns_body = {"elements": [{"id": "c1", "name": "B", "status": "ACTIVE", "runSchedule": {"start": 1_757_529_600_000}}]}
    http = _Http([_http_resp(200, campaigns_body)])
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: http)
    b1 = SimpleNamespace(raw={"costLocal": "7"}, clicks=2, impressions=70, engagement=3)
    stats = {"_meta": {"note": "internal"}, "urn:li:sponsoredCampaign:c1": b1}
    monkeypatch.setattr(A, "_fetch_linkedin_ad_stats", AsyncMock(return_value=stats))
    m = R.CampaignMetrics(account_id="a", campaign_id=None)
    ok = await R._collect_api_metrics(_DB([]), m)
    assert ok is True
    assert m.spend_eur == 7.0 and m.clicks == 2 and m.impressions == 70


@pytest.mark.asyncio
async def test_geo_names_request_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))

    class _DeadClient:
        def __init__(self, **kw):
            raise RuntimeError("dns dead")

    monkeypatch.setattr(R.httpx, "AsyncClient", _DeadClient)
    assert await R._geo_names(_DB([]), ["12345"]) == {}


class _SM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


def _patch_daily_run(monkeypatch, db):
    settings = R.get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_ADS_CAMPAIGN_ID", "c1", raising=False)
    monkeypatch.setattr(settings, "LINKEDIN_ADS_END_DATE", "", raising=False)
    monkeypatch.setattr(settings, "LINKEDIN_ADS_EMAIL_TO", "", raising=False)
    monkeypatch.setattr(settings, "DIGEST_EMAIL_TO", "ops@x.io", raising=False)
    for k in (
        "SLACK_ADS_WEBHOOK_URL",
        "SLACK_WEBHOOK_URL",
        "SLACK_BOT_TOKEN",
        "SLACK_ACCESS_TOKEN",
        "SLACK_ADS_CHANNEL_ID",
        "SLACK_CHANNEL_ID",
    ):
        monkeypatch.setattr(settings, k, "", raising=False)
    monkeypatch.setattr(R, "async_session_maker", lambda: _SM(db))
    monkeypatch.setattr(R, "_get_team_id", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(R, "_final_sent", AsyncMock(return_value=False))
    monkeypatch.setattr(R, "_latest_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(
        R,
        "collect_metrics",
        AsyncMock(return_value=_metrics(status="active", spend_eur=1.0, clicks=1)),
    )
    monkeypatch.setattr(R, "_post_slack_text", AsyncMock(return_value=(True, None, None)))
    return settings


@pytest.mark.asyncio
async def test_run_daily_report_notebook_render_and_email_failure(monkeypatch):
    db = _DB([])
    _patch_daily_run(monkeypatch, db)
    monkeypatch.setattr(
        R,
        "_render_report_notebook",
        AsyncMock(return_value=("nb report", "<p>nb</p>", [{"data": b"x", "cid": "c"}])),
    )
    email = Mock(side_effect=RuntimeError("smtp down"))
    monkeypatch.setattr(R, "send_email_smtp", email)
    out = await R.run_daily_report()
    assert out["ok"] is True and out["final"] is False
    assert out["email_error"] == "smtp down"
    assert out["posted_to_slack"] is True


@pytest.mark.asyncio
async def test_run_daily_report_code_path_org_failure_and_bad_end_date(monkeypatch):
    db = _DB([])
    settings = _patch_daily_run(monkeypatch, db)
    monkeypatch.setattr(R, "_render_report_notebook", AsyncMock(return_value=None))
    monkeypatch.setattr(R, "build_org_section", AsyncMock(side_effect=RuntimeError("org dead")))
    monkeypatch.setattr(R, "send_email_smtp", Mock())
    out = await R.run_daily_report()
    assert out["ok"] is True and out["final"] is False
    # malformed END_DATE → warning, treated as no end date, run still completes
    monkeypatch.setattr(settings, "LINKEDIN_ADS_END_DATE", "not-a-date", raising=False)
    out2 = await R.run_daily_report()
    assert out2["ok"] is True


@pytest.mark.asyncio
async def test_run_daily_report_same_day_dedupe_skips(monkeypatch):
    from datetime import datetime

    db = _DB([])
    _patch_daily_run(monkeypatch, db)
    collect = AsyncMock()
    monkeypatch.setattr(R, "collect_metrics", collect)
    # naive captured_at = UTC-stored row written earlier today → dedupe hit
    monkeypatch.setattr(R, "_latest_snapshot", AsyncMock(return_value=SimpleNamespace(captured_at=datetime.now())))
    out = await R.run_daily_report()
    assert out["ok"] is True and out["skipped"] == "report already sent today"
    collect.assert_not_called()


@pytest.mark.asyncio
async def test_run_daily_report_force_bypasses_same_day(monkeypatch):
    from datetime import UTC, datetime

    db = _DB([])
    _patch_daily_run(monkeypatch, db)
    # tz-aware captured_at (covers the non-naive branch) still today →
    # dedupe would hit, but force=True must bypass it.
    monkeypatch.setattr(R, "_latest_snapshot", AsyncMock(return_value=SimpleNamespace(captured_at=datetime.now(UTC), spend_eur=0.0, clicks=0, engagements=0)))
    monkeypatch.setattr(R, "_render_report_notebook", AsyncMock(return_value=None))
    monkeypatch.setattr(R, "build_org_section", AsyncMock(return_value=""))
    monkeypatch.setattr(R, "send_email_smtp", Mock())
    out = await R.run_daily_report(force=True)
    assert out["ok"] is True and "skipped" not in out


@pytest.mark.asyncio
async def test_run_daily_report_prior_day_snapshot_runs(monkeypatch):
    from datetime import UTC, datetime

    db = _DB([])
    _patch_daily_run(monkeypatch, db)
    monkeypatch.setattr(
        R,
        "_latest_snapshot",
        AsyncMock(return_value=SimpleNamespace(captured_at=datetime.now(UTC) - timedelta(days=1), spend_eur=0.0, clicks=0, engagements=0)),
    )
    monkeypatch.setattr(R, "_render_report_notebook", AsyncMock(return_value=None))
    monkeypatch.setattr(R, "build_org_section", AsyncMock(return_value=""))
    monkeypatch.setattr(R, "send_email_smtp", Mock())
    out = await R.run_daily_report()
    assert out["ok"] is True and "skipped" not in out
