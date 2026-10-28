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
from unittest.mock import AsyncMock

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
