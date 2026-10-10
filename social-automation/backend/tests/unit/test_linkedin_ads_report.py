"""Unit tests for app/services/linkedin_ads_report.py pure layer.

Covers the Campaign Manager page-text parsers, money/date helpers, the
multi-branch build_report_text (status, pace, promo credit, card-safety
verdicts), Slack control blocks, and the email text converter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.linkedin_ads_report as R

# ── regex helpers ─────────────────────────────────────────────────────


def test_eur_and_int():
    assert R._eur("Spend\n€1,234.56", r"Spend\n\s*€([\d.,]+)") == 1234.56
    assert R._eur("nothing", r"€([\d.,]+)") == 0.0
    assert R._eur("€abc", r"€([\d.,]+)") == 0.0
    assert R._int("Clicks\n1,234", r"Clicks\s*\n\s*([\d,]+)") == 1234
    assert R._int("none", r"(\d+)") == 0


def test_parse_overview():
    text = "Spend by objective\n€42.50\n\nClicks\n17\n\nCPC\n€2.50\n\nActive\n2\n\nPaused\n1\n\n230\nEngagements\n\n5.45%\n\n+12%\n\n3.06%\n\nActions"
    out = R._parse_overview(text)
    assert out["spend_eur"] == 42.50
    assert out["clicks"] == 17
    assert out["cpc_eur"] == 2.50
    assert out["ad_set_statuses"]["active"] == 2
    assert out["engagements"] == 230
    assert out["engagement_rate"] == 5.45
    assert out["ctr"] == 3.06


def test_parse_campaign_page():
    text = "Cloudless boost - Sep 2026 - coupon\n\n907100946 · Sponsored Content Schedule: 9/24/2026 - 10/23/2026\n\nActive\n\nLifetime budget: €100.00"
    out = R._parse_campaign_page(text, "907100946")
    assert out["budget_eur"] == 100.0
    assert "9/24/2026" in out["schedule"]
    assert out["status"] == "active"
    assert "Cloudless boost" in out["campaign_name"]


def test_parse_schedule_date():
    assert R._parse_schedule_date("9/24/2026 - 10/23/2026") == date(2026, 9, 24)
    assert R._parse_schedule_date("") is None
    assert R._parse_schedule_date("13/99/2026") is None


def test_fmt_money():
    assert R._fmt_money(0) == "€0"
    assert R._fmt_money(12.5) == "€12.5"
    assert R._fmt_money(1234.00) == "€1,234"
    assert R._fmt_money(3.14159) == "€3.14"


def test_control_blocks():
    active = R._control_blocks("active", "cid-1")
    pause = active[1]["elements"][0]
    assert pause["action_id"] == "linkedin_ads_pause"
    assert pause["value"] == "cid-1"
    assert "confirm" in pause

    paused = R._control_blocks("paused", "cid-1")
    resume = paused[1]["elements"][0]
    assert resume["action_id"] == "linkedin_ads_resume"
    assert "confirm" not in resume

    assert active[1]["elements"][1]["action_id"] == "linkedin_ads_status"


def test_to_email_text():
    assert R._to_email_text("*bold* text") == "bold text"


# ── build_report_text branches ────────────────────────────────────────


def _metrics(**kw):
    m = R.CampaignMetrics(account_id="a", campaign_id="c")
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def test_report_status_labels():
    out = R.build_report_text(_metrics(status="active"), None, None)
    assert "Running" in out
    out = R.build_report_text(_metrics(status="paused"), None, None)
    assert "Paused" in out
    end = date.today() + timedelta(days=30)
    m = _metrics(status="draft", budget_eur=100.0, schedule_end=end.isoformat())
    out = R.build_report_text(m, None, end)
    assert "Draft" in out and "isn't spending" in out
    out = R.build_report_text(_metrics(status="weird"), None, None)
    assert "Weird" in out


def test_report_spend_and_benchmark():
    m = _metrics(status="active", spend_eur=40.0, budget_eur=100.0, daily_budget_eur=25.0, ctr=0.50)
    out = R.build_report_text(m, None, None)
    assert "€40" in out and "€100" in out and "40% used" in out
    assert "above the LinkedIn sponsored median" in out

    m2 = _metrics(status="active", spend_eur=40.0, ctr=0.30)
    out2 = R.build_report_text(m2, None, None)
    assert "Spend so far" in out2
    assert "below the LinkedIn sponsored median" in out2


def test_report_delta_vs_previous():
    prev = SimpleNamespace(spend_eur=30.0, clicks=10, engagements=50, captured_at=datetime.now(UTC) - timedelta(days=1))
    m = _metrics(status="active", spend_eur=40.0, clicks=15, engagements=80)
    out = R.build_report_text(m, prev, None)
    assert "€10" in out or "+€10" in out  # spend delta shown


def test_report_pace_exhaustion_and_on_track():
    end = date.today() + timedelta(days=30)
    # nearly exhausted → warns early stop
    m = _metrics(status="active", spend_eur=95.0, budget_eur=100.0, schedule_start=(date.today() - timedelta(days=1)).isoformat(), schedule_end=end.isoformat())
    out = R.build_report_text(m, None, end)
    assert "budget exhausts" in out or "budget reached" in out

    # plenty of headroom → on track
    m2 = _metrics(
        status="active", spend_eur=10.0, budget_eur=1000.0, schedule_start=(date.today() - timedelta(days=1)).isoformat(), schedule_end=end.isoformat()
    )
    out2 = R.build_report_text(m2, None, end)
    assert "on track" in out2 or "running hot" in out2


def test_report_promo_credit_safety(monkeypatch):
    end = date.today() + timedelta(days=30)
    m = _metrics(
        status="active",
        spend_eur=10.0,
        budget_eur=50.0,
        account_spend_eur=10.0,
        campaign_id="c1",
        campaign_name="Boost",
        raw={"campaigns": [{"id": "c1", "status": "active", "budget": 50.0}], "spend_by_campaign": {"c1": 10.0}},
    )
    monkeypatch.setattr(R.get_settings(), "LINKEDIN_ADS_CREDIT_EUR", 200.0)
    out = R.build_report_text(m, None, end)
    assert "Promo credit" in out and "not expected to be charged" in out

    # uncapped resumable campaign → unverified warning
    m2 = _metrics(
        status="active",
        spend_eur=10.0,
        budget_eur=0.0,
        account_spend_eur=10.0,
        campaign_id="c1",
        campaign_name="Boost",
        raw={"campaigns": [{"id": "c1", "status": "active"}], "spend_by_campaign": {}},
    )
    out2 = R.build_report_text(m2, None, end)
    assert "unverified" in out2

    # spend could exceed credit → warn
    m3 = _metrics(
        status="active",
        spend_eur=10.0,
        budget_eur=500.0,
        account_spend_eur=10.0,
        campaign_id="c1",
        campaign_name="Boost",
        raw={"campaigns": [{"id": "c1", "status": "active", "budget": 500.0}], "spend_by_campaign": {"c1": 10.0}},
    )
    out3 = R.build_report_text(m3, None, end)
    assert "may be charged" in out3

    # no credit configured → unconditional safe line
    monkeypatch.setattr(R.get_settings(), "LINKEDIN_ADS_CREDIT_EUR", 0.0)
    out4 = R.build_report_text(m, None, end)
    assert "stays inside the promo credit" in out4


# ── async layer ───────────────────────────────────────────────────────


class _Res:
    def __init__(self, first=None, all_=None, one=None):
        self._first, self._all, self._one = first, all_, one

    def scalars(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all if self._all is not None else []

    def scalar_one_or_none(self):
        return self._one if self._one is not None else self._first

    def scalar(self):
        return self._first


class _DB:
    """Result-queue fake — each db.execute pops the next _Res."""

    def __init__(self, results):
        self._q = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Res()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_linkedin_org_token(monkeypatch):
    # no account
    assert await R._linkedin_org_token(_DB([_Res(first=None)])) is None
    # account without token
    acc = SimpleNamespace(access_token_enc=None)
    assert await R._linkedin_org_token(_DB([_Res(first=acc)])) is None
    # decrypt ok / fail
    monkeypatch.setattr(R, "decrypt_token", lambda enc: f"tok:{enc}")
    acc = SimpleNamespace(access_token_enc="enc1")
    assert await R._linkedin_org_token(_DB([_Res(first=acc)])) == "tok:enc1"

    def _boom(enc):
        raise ValueError("bad")

    monkeypatch.setattr(R, "decrypt_token", _boom)
    assert await R._linkedin_org_token(_DB([_Res(first=acc)])) is None


def _http_resp(status=200, body=None):
    import httpx

    return httpx.Response(status, json=body or {})


class _Http:
    """Sequenced httpx.AsyncClient fake."""

    def __init__(self, responses):
        self._q = list(responses)
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.urls.append(url)
        return self._q.pop(0) if self._q else _http_resp(200, {})

    async def post(self, url, **kw):
        self.urls.append(url)
        return self._q.pop(0) if self._q else _http_resp(200, {})


@pytest.mark.asyncio
async def test_collect_api_metrics(monkeypatch):
    import app.services.analytics_sync as A

    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {"Authorization": t})

    start_ms = 1_757_529_600_000  # ~Sep 2026
    end_ms = 1_760_054_400_000
    campaign = {
        "name": "Boost",
        "status": "ACTIVE",
        "totalBudget": {"amount": "100.0"},
        "dailyBudget": {"amount": "25.0"},
        "runSchedule": {"start": start_ms, "end": end_ms},
        "campaignGroup": "urn:li:adCampaignGroup:777",
    }
    group = {"runSchedule": {"end": end_ms + 1000}}
    listing = {
        "elements": [
            {"id": "c1", "name": "Boost", "status": "ACTIVE", "totalBudget": {"amount": "100.0"}, "runSchedule": {"start": start_ms}},
            {"id": "c2", "name": "Other", "status": "PAUSED", "runSchedule": {"start": start_ms}},
        ]
    }
    http = _Http([_http_resp(200, campaign), _http_resp(200, group), _http_resp(200, listing)])
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: http)

    bundle = SimpleNamespace(raw={"costLocal": "40.0", "costUsd": "44.0", "daily": [{"totalEngagements": 5}]}, clicks=12, impressions=1500, engagement=5)
    monkeypatch.setattr(A, "_fetch_linkedin_ad_stats", AsyncMock(return_value={"urn:li:sponsoredCampaign:c1": bundle}))

    m = R.CampaignMetrics(account_id="acct", campaign_id="c1")
    assert await R._collect_api_metrics(_DB([]), m) is True
    assert m.campaign_name == "Boost" and m.status == "active"
    assert m.budget_eur == 100.0 and m.daily_budget_eur == 25.0
    assert m.spend_eur == 40.0 and m.clicks == 12
    assert m.impressions == 1500 and m.engagements == 5
    assert m.ctr == round(12 / 1500 * 100, 2)
    assert m.raw["spend_by_campaign"] == {"c1": 40.0}
    assert m.raw["campaigns"][0]["status"] == "active"
    assert m.raw["source"] == "api"
    assert m.schedule_start and m.schedule_end


@pytest.mark.asyncio
async def test_collect_api_metrics_no_campaign_row(monkeypatch):
    import app.services.analytics_sync as A

    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {})
    http = _Http([_http_resp(200, {"name": "B", "status": "ACTIVE", "runSchedule": {}, "campaignGroup": "x"}), _http_resp(200, {"elements": []})])
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: http)
    monkeypatch.setattr(A, "_fetch_linkedin_ad_stats", AsyncMock(return_value={}))
    m = R.CampaignMetrics(account_id="a", campaign_id="c1")
    assert await R._collect_api_metrics(_DB([]), m) is False
    assert m.raw["api_note"] == "campaign not in adAnalytics response"


@pytest.mark.asyncio
async def test_collect_api_metrics_http_error_and_no_token(monkeypatch):
    import app.services.analytics_sync as A

    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value=None))
    m = R.CampaignMetrics(account_id="a", campaign_id="c")
    assert await R._collect_api_metrics(_DB([]), m) is False

    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {})
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: _Http([_http_resp(500, {})]))
    with pytest.raises(RuntimeError, match="adCampaigns HTTP 500"):
        await R._collect_api_metrics(_DB([]), m)


@pytest.mark.asyncio
async def test_collect_metrics_api_path(monkeypatch):
    settings = R.get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_ADS_CAMPAIGN_ID", "c1", raising=False)
    monkeypatch.setattr(R, "_collect_api_metrics", AsyncMock(return_value=True))
    m = await R.collect_metrics(_DB([]))
    assert m.campaign_id == "c1"


@pytest.mark.asyncio
async def test_collect_metrics_sidecar_fallback(monkeypatch):
    settings = R.get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_ADS_CAMPAIGN_ID", "907100946", raising=False)
    monkeypatch.setattr(R, "_collect_api_metrics", AsyncMock(side_effect=RuntimeError("api down")))
    overview = "Spend by objective\n€20.00\n\nClicks\n8\n\nCPC\n€2.50\n\nActive\n1\n\n40\nEngagements\n\n4.00%\n\n0.80%\n\nActions"
    campaign_page = "My campaign\n\n907100946 · Sponsored Content Schedule: 9/24/2026\n\nActive\n\nLifetime budget: €100.00"
    texts = [overview, campaign_page]
    monkeypatch.setattr(R, "_sidecar_page_text", AsyncMock(side_effect=texts))
    m = await R.collect_metrics(_DB([]))
    assert m.spend_eur == 20.0 and m.clicks == 8
    assert m.status == "active" and m.budget_eur == 100.0
    assert m.campaign_name == "My campaign"
    assert m.raw["api_error"] == "api down"
    # impressions derived from clicks/ctr
    assert m.impressions == round(8 / (0.80 / 100))


@pytest.mark.asyncio
async def test_collect_metrics_scrape_errors_swallowed(monkeypatch):
    settings = R.get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_ADS_CAMPAIGN_ID", "c", raising=False)
    monkeypatch.setattr(R, "_collect_api_metrics", AsyncMock(return_value=False))
    monkeypatch.setattr(R, "_sidecar_page_text", AsyncMock(side_effect=RuntimeError("scrape dead")))
    m = await R.collect_metrics(_DB([]))
    assert m.raw["overview_error"] == "scrape dead"
    assert m.raw["campaign_error"] == "scrape dead"


@pytest.mark.asyncio
async def test_geo_names(monkeypatch):
    import app.services.analytics_sync as A

    # no clean ids
    assert await R._geo_names(_DB([]), ["abc"]) == {}
    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(A, "_linkedin_headers", lambda t: {})
    http = _Http([_http_resp(200, {"results": {"103644278": {"defaultLocalizedName": {"value": "Greece"}}}})])
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: http)
    out = await R._geo_names(_DB([]), ["103644278"])
    assert out == {"103644278": "Greece"}
    # API failure → {}
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: _Http([_http_resp(500)]))
    assert await R._geo_names(_DB([]), ["1"]) == {}
    # no token → {}
    monkeypatch.setattr(R, "_linkedin_org_token", AsyncMock(return_value=None))
    assert await R._geo_names(_DB([]), ["1"]) == {}


@pytest.mark.asyncio
async def test_build_org_section_empty_and_full(monkeypatch):
    # no linkedin org account → ""
    assert await R.build_org_section(_DB([_Res(first=None)])) == ""

    account = SimpleNamespace(id=uuid.uuid4())
    follower_stats = {"follower_gains": {"organic": 12, "paid": 3}}
    page_stats = {"period": {"daily": [{"totalPageStatistics": {"views": {"allPageViews": {"pageViews": 100, "uniquePageViews": 60}}}}]}}
    lifetime = {
        "organizationalEntityShareStatistics": {
            "totalShareStatistics": {
                "impressionCount": 5000,
                "uniqueImpressionsCount": 3000,
                "clickCount": 120,
                "likeCount": 80,
                "commentCount": 10,
                "shareCount": 5,
            }
        }
    }
    top_post = SimpleNamespace(impressions=900, likes=30, comments=4, shares=2)
    db = _DB(
        [
            _Res(first=account),
            _Res(all_=[150, 140]),
            _Res(one=follower_stats),
            _Res(one=page_stats),
            _Res(one=lifetime),
            _Res(one=top_post),
        ]
    )
    out = await R.build_org_section(db)
    assert "*Followers:* 150 (+10" in out
    assert "*New followers (30d):* 12 organic · 3 paid" in out
    assert "*Page views (30d):* 100 (60 unique visitors)" in out
    assert "5,000 impressions" in out
    assert "*Top post:* 900 impressions" in out


@pytest.mark.asyncio
async def test_build_org_section_geo_names(monkeypatch):
    account = SimpleNamespace(id=uuid.uuid4())
    follower_stats = {
        "demographics": {
            "followerCountsByGeoCountry": [
                {"geoCountry": "urn:li:geo:1", "followerCounts": {"organicFollowerCount": 50}},
                {"geoCountry": "urn:li:geo:2", "followerCounts": {"organicFollowerCount": 20}},
            ]
        }
    }
    db = _DB(
        [
            _Res(first=account),
            _Res(all_=[200]),
            _Res(one=follower_stats),
            _Res(one={}),
            _Res(one={}),
            _Res(one=None),
        ]
    )
    monkeypatch.setattr(R, "_geo_names", AsyncMock(return_value={"1": "Greece", "2": "Germany"}))
    out = await R.build_org_section(db)
    assert "Greece (50)" in out and "Germany (20)" in out


@pytest.mark.asyncio
async def test_get_team_id_and_snapshot_helpers():
    tid = uuid.uuid4()
    assert await R._get_team_id(_DB([_Res(first=tid)])) == tid
    # no linkedin team → tier-rank fallback
    assert await R._get_team_id(_DB([_Res(first=None), _Res(first=tid)])) == tid
    assert await R._get_team_id(_DB([_Res(first=None), _Res(first=None)])) is None

    snap = SimpleNamespace(id=1)
    assert await R._latest_snapshot(_DB([_Res(one=snap)]), "c") is snap
    assert await R._final_sent(_DB([_Res(one=42)]), "c") is True
    assert await R._final_sent(_DB([_Res(one=None)]), "c") is False


@pytest.mark.asyncio
async def test_render_report_notebook(monkeypatch, tmp_path):
    text_f = tmp_path / "report.txt"
    text_f.write_text("notebook report body")
    att = tmp_path / "chart.png"
    att.write_bytes(b"PNG")
    fake_nb = SimpleNamespace(
        run_report_notebook=lambda *a, **kw: {
            "manifest": {"text_file": str(text_f), "attachments": [{"file": str(att), "cid": "c", "mime": "image/png"}]},
            "manifest_path": str(tmp_path / "manifest.json"),
            "duration_s": 1,
            "notebook": "linkedin_ads_daily",
        }
    )
    import sys

    monkeypatch.setitem(sys.modules, "app.services.notebook_runner", fake_nb)
    m = _metrics(status="active")
    out = await R._render_report_notebook(campaign_id="c", status="active", end_date=None, is_final=False, metrics=m)
    assert out is not None
    text, html, attachments = out
    assert text == "notebook report body"
    assert html is None
    assert attachments[0]["data"] == b"PNG"

    # missing text file → None (falls back to code path)
    fake_nb.run_report_notebook = lambda *a, **kw: {"manifest": {}, "manifest_path": str(tmp_path / "m.json")}
    assert await R._render_report_notebook(campaign_id="c", status="active", end_date=None, is_final=False, metrics=m) is None

    # notebook import/run blows up → None
    def _boom(*a, **kw):
        raise RuntimeError("nb dead")

    fake_nb.run_report_notebook = _boom
    assert await R._render_report_notebook(campaign_id="c", status="active", end_date=None, is_final=False, metrics=m) is None


@pytest.mark.asyncio
async def test_run_daily_report(monkeypatch):
    settings = R.get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_ADS_CAMPAIGN_ID", "c1", raising=False)
    monkeypatch.setattr(settings, "LINKEDIN_ADS_END_DATE", (date.today() - timedelta(days=1)).isoformat(), raising=False)
    monkeypatch.setattr(settings, "LINKEDIN_ADS_EMAIL_TO", "", raising=False)
    monkeypatch.setattr(settings, "DIGEST_EMAIL_TO", "ops@x.io", raising=False)
    monkeypatch.setattr(settings, "SLACK_ADS_WEBHOOK_URL", "", raising=False)
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", "", raising=False)
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "", raising=False)
    monkeypatch.setattr(settings, "SLACK_ACCESS_TOKEN", "", raising=False)
    monkeypatch.setattr(settings, "SLACK_ADS_CHANNEL_ID", "", raising=False)
    monkeypatch.setattr(settings, "SLACK_CHANNEL_ID", "", raising=False)

    db = _DB([])

    class _SessionMaker:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(R, "async_session_maker", lambda: _SessionMaker())
    monkeypatch.setattr(R, "_get_team_id", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(R, "_final_sent", AsyncMock(return_value=False))
    prev = SimpleNamespace(spend_eur=10.0, clicks=3, engagements=20, captured_at=datetime.now(UTC) - timedelta(days=1))
    monkeypatch.setattr(R, "_latest_snapshot", AsyncMock(return_value=prev))
    metrics = _metrics(status="completed", spend_eur=15.0, clicks=5, engagements=30, ctr=0.5, cpc_eur=3.0, budget_eur=100.0, impressions=1000)
    monkeypatch.setattr(R, "collect_metrics", AsyncMock(return_value=metrics))
    monkeypatch.setattr(R, "_render_report_notebook", AsyncMock(return_value=None))
    monkeypatch.setattr(R, "build_org_section", AsyncMock(return_value="\n\n📣 *Company page (organic)*\n*Followers:* 150"))
    slack = AsyncMock(return_value=(True, None, None))
    monkeypatch.setattr(R, "_post_slack_text", slack)
    email = Mock()
    monkeypatch.setattr(R, "send_email_smtp", email)

    out = await R.run_daily_report()
    assert out["ok"] is True and out["final"] is True
    assert out["metrics"]["spend_eur"] == 15.0
    assert out["posted_to_slack"] is True
    # snapshot persisted with final flag
    assert db.added and db.added[0].raw["final"] is True
    # slack got the report + control blocks
    assert slack.await_args.kwargs["blocks"]
    assert "Final report" in slack.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_run_daily_report_early_exits(monkeypatch):
    settings = R.get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_ADS_CAMPAIGN_ID", "c1", raising=False)
    monkeypatch.setattr(settings, "LINKEDIN_ADS_END_DATE", (date.today() - timedelta(days=1)).isoformat(), raising=False)
    db = _DB([])

    class _SessionMaker:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(R, "async_session_maker", lambda: _SessionMaker())
    # no team
    monkeypatch.setattr(R, "_get_team_id", AsyncMock(return_value=None))
    assert (await R.run_daily_report())["error"] == "no team"
    # final already sent
    monkeypatch.setattr(R, "_get_team_id", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(R, "_final_sent", AsyncMock(return_value=True))
    out = await R.run_daily_report()
    assert out["ok"] and "skipped" in out


@pytest.mark.asyncio
async def test_sidecar_page_text(monkeypatch):
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    calls = []

    class _Sidecar:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            calls.append(("post", url, kw.get("json", {})))
            r = Mock()
            r.raise_for_status = Mock()
            return r

        async def get(self, url, **kw):
            calls.append(("get", url, {}))
            r = Mock()
            r.raise_for_status = Mock()
            r.json = lambda: {"text": "rendered page"}
            return r

    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **kw: _Sidecar())
    out = await R._sidecar_page_text("https://example.com/x")
    assert out == "rendered page"
    kinds = [c[0] for c in calls]
    assert kinds == ["post", "post", "get"]
    assert calls[0][2]["url"] == "https://example.com/x"
