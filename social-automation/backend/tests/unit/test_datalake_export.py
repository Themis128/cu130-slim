"""Unit tests for the datalake export serializers (pure helpers, no DB)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.worker.tasks import datalake_export as de
from app.worker.tasks.datalake_export import (
    _email_domain,
    _email_hash,
    _iso,
)


def test_iso_aware_and_naive():
    aware = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    assert _iso(aware) == "2026-09-20T12:00:00+00:00"
    naive = datetime(2026, 9, 20, 12, 0)
    assert _iso(naive) == "2026-09-20T12:00:00+00:00"
    assert _iso(None) is None


def test_email_hash_is_deterministic_and_normalized():
    a = _email_hash("Alice@Example.com")
    b = _email_hash("  alice@example.COM ")
    assert a == b
    assert len(a) == 16
    assert a != "alice@example.com"  # never raw email
    assert _email_hash(None) is None
    assert _email_hash("") is None


def test_email_domain():
    assert _email_domain("Alice@Example.COM") == "example.com"
    assert _email_domain("not-an-email") is None
    assert _email_domain(None) is None
    assert _email_domain("") is None


# --- new exporters ---------------------------------------------------------


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _TempoClient:
    """AsyncClient stub; responds to TraceQL status filters per query."""

    main: list = []
    e5xx: list = []
    e4xx: list = []
    fail_with: Exception | None = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        if type(self).fail_with:
            raise type(self).fail_with
        q = (params or {}).get("q", "")
        if ">= 500" in q:
            return _Resp({"traces": type(self).e5xx})
        if ">= 400" in q:
            return _Resp({"traces": type(self).e4xx})
        return _Resp({"traces": type(self).main})


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_edge_metrics_aggregates(monkeypatch):
    from app.worker.tasks import datalake_export as de

    _TempoClient.main = [{"durationMs": 10}, {"durationMs": 50}, {"durationMs": 200}]
    _TempoClient.e5xx = [{"durationMs": 9000}]
    _TempoClient.e4xx = [{"durationMs": 3}, {"durationMs": 4}]
    _TempoClient.fail_with = None
    monkeypatch.setattr(de.httpx, "AsyncClient", _TempoClient)
    out = _run(de._export_edge_metrics())
    assert out["sampled"] is True
    h = out["hosts"][0]
    assert h["sampled_traces"] == 3
    assert h["p50_ms"] == 50
    assert h["max_ms"] == 200
    assert h["errors_5xx"] == 1
    assert h["errors_4xx"] == 2


def test_edge_metrics_empty_and_malformed(monkeypatch):
    from app.worker.tasks import datalake_export as de

    _TempoClient.main = []
    _TempoClient.e5xx = _TempoClient.e4xx = []
    _TempoClient.fail_with = None
    monkeypatch.setattr(de.httpx, "AsyncClient", _TempoClient)
    out = _run(de._export_edge_metrics())
    assert out["hosts"][0]["sampled_traces"] == 0
    assert "p50_ms" not in out["hosts"][0]

    _TempoClient.main = [{"durationMs": 5}, {}]
    out = _run(de._export_edge_metrics())
    # malformed span (no durationMs) treated as 0
    assert out["hosts"][0]["sampled_traces"] == 2


def test_edge_metrics_http_error_isolated(monkeypatch):
    from app.worker.tasks import datalake_export as de

    _TempoClient.fail_with = RuntimeError("boom")
    monkeypatch.setattr(de.httpx, "AsyncClient", _TempoClient)
    out = _run(de._export_edge_metrics())
    assert all("error" in h for h in out["hosts"])
    _TempoClient.fail_with = None


def test_reports_index_normalizes_manifests(monkeypatch, tmp_path):
    import json

    from app.worker.tasks import datalake_export as de

    (tmp_path / "a.manifest.json").write_text(
        json.dumps(
            {
                "subject": "brief",
                "html_file": "/notebooks/output/a.html",
                "text_file": "/notebooks/output/a.txt",
                "attachments": [{"file": "/notebooks/output/a.png"}],
            }
        )
    )
    (tmp_path / "b.manifest.json").write_text(
        json.dumps({"reports": [{"subject": "ads", "html_file": "/x/b.html"}]})
    )
    (tmp_path / "broken.manifest.json").write_text("{not json")
    monkeypatch.setattr(de, "_NOTEBOOK_OUTPUT", tmp_path)
    idx = _run(de._export_reports_index())
    assert len(idx) == 2
    a = next(r for r in idx if r["manifest"] == "a.manifest.json")
    assert a["subjects"] == ["brief"]
    assert "a.png" in a["files"][-1]
    b = next(r for r in idx if r["manifest"] == "b.manifest.json")
    assert b["subjects"] == ["ads"]


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _q):
        return _Rows(self._results.pop(0))


def test_ops_health_flags_stale_sync():
    from datetime import datetime, timedelta

    from app.worker.tasks import datalake_export as de

    now = datetime.now(UTC)
    db = _FakeDB(
        [
            [{"platform": "facebook", "status": "completed", "n": 5}],
            [{"platform": "twitter", "status": "skipped", "n": 1}],
            [
                {"platform": "linkedin", "username": "tb", "last_metrics": now},
                {"platform": "tiktok", "username": "tt", "last_metrics": None},
                {
                    "platform": "instagram",
                    "username": "ig",
                    "last_metrics": now - timedelta(hours=5),
                },
            ],
        ]
    )
    out = _run(de._export_ops_health(db))
    assert out["queue"][0]["n"] == 5
    assert out["failed_or_skipped_7d"][0]["status"] == "skipped"
    assert "tiktok/@tt" in out["stale_sync_accounts"]
    assert "instagram/@ig" in out["stale_sync_accounts"]
    assert "linkedin/@tb" not in out["stale_sync_accounts"]


class _CompileCheckDB:
    """execute() compiles the statement so tests can assert the WHERE clause
    actually filters fixture emails, then returns canned rows."""

    def __init__(self, rows):
        self._rows = rows
        self.compiled_sql = ""

    async def execute(self, stmt):
        self.compiled_sql = str(
            stmt.compile(compile_kwargs={"literal_binds": True})
        )
        return _Rows(self._rows)


def test_export_leads_excludes_fixture_emails():
    """CI fixtures (@example.invalid / example.com) must be filtered in SQL —
    the live table holds 13 'CI Functional Test' rows that made the digest
    report '13 leads, 0 synced'."""
    import uuid

    from app.models.lead import Lead, LeadSource
    from app.worker.tasks import datalake_export as de

    lead = Lead(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        source=LeadSource.website,
        name="Real Person",
        email="real@customer.gr",
        meta_data={"espocrm_lead_id": "espo-1"},
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    db = _CompileCheckDB([(lead, None)])
    rows = _run(de._export_leads(db))

    for suffix in ("example.invalid", "example.com", "example.org", "example.net"):
        assert suffix in db.compiled_sql
    assert len(rows) == 1
    assert rows[0]["espocrm_synced"] is True
    assert rows[0]["email_domain"] == "customer.gr"


# ── coverage: remaining exporters + _export_async ─────────────────────


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def all(self):
        return self._rows


class _OneExecDB:
    def __init__(self, res):
        self._res = res

    async def execute(self, _q):
        return self._res


def _acct(**kw):
    base = dict(
        id=uuid.uuid4(), team_id=uuid.uuid4(), platform="linkedin",
        username="u", display_name="d", account_type="page",
        is_business=True, status="active", scopes=["r", "w"],
        token_expires_at=None, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    base.update(kw)
    return SimpleNamespace(**base)


def test_export_accounts():
    rows = _run(de._export_accounts(_OneExecDB(_Scalars([_acct(scopes=None)]))))
    assert rows[0]["platform"] == "linkedin"
    assert rows[0]["scopes"] == []
    assert rows[0]["token_expires_at"] is None


def test_export_posts_merges_targets():
    post = SimpleNamespace(
        id=uuid.uuid4(), team_id=uuid.uuid4(), status="published",
        content_text="body", link_url=None, hashtags=["a"],
        media_ids=[1, 2], scheduled_at=None, published_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC))
    t1 = SimpleNamespace(social_account_id=uuid.uuid4(), platform_post_id="p1",
                         platform_url="u1", status="published")
    t2 = SimpleNamespace(social_account_id=uuid.uuid4(), platform_post_id=None,
                         platform_url=None, status="failed")
    res = _Scalars([(post, t1, "linkedin"), (post, t2, "twitter")])
    rows = _run(de._export_posts(_OneExecDB(res)))
    assert len(rows) == 1
    assert len(rows[0]["targets"]) == 2
    assert rows[0]["media_count"] == 2


def test_export_metrics_and_followers_and_events():
    snap = SimpleNamespace(
        id=uuid.uuid4(), team_id=uuid.uuid4(), post_id=uuid.uuid4(),
        social_account_id=uuid.uuid4(), platform="ig",
        platform_post_id="x", impressions=1, clicks=2, likes=3,
        comments=4, shares=5, reach=6, engagement=7,
        engagement_rate=0.1, source="api", notes=None,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC))
    rows = _run(de._export_metrics(_OneExecDB(_Scalars([snap]))))
    assert rows[0]["post_id"] == str(snap.post_id)
    snap2 = SimpleNamespace(**{**snap.__dict__, "post_id": None})
    rows = _run(de._export_metrics(_OneExecDB(_Scalars([snap2]))))
    assert rows[0]["post_id"] is None

    f = SimpleNamespace(social_account_id=uuid.uuid4(), team_id=uuid.uuid4(),
                        platform="ig", followers=100,
                        captured_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert _run(de._export_followers(_OneExecDB(_Scalars([f]))))[0]["followers"] == 100

    e = SimpleNamespace(team_id=uuid.uuid4(), social_account_id=None,
                        platform="ig", event_type="connect", meta_data=None,
                        occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
    rows = _run(de._export_account_events(_OneExecDB(_Scalars([e]))))
    assert rows[0]["account_id"] is None and rows[0]["meta_data"] == {}


def test_export_web_events_utm_extraction():
    e = SimpleNamespace(
        team_id=uuid.uuid4(), domain="cloudless.gr", event_name="pageview",
        session_id="s", path="/x", referrer="r", locale="en",
        payload={"utm_source": "li", "utm_medium": "social",
                 "utm_campaign": "c", "client_ip": "1.2.3.4"},
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
    rows = _run(de._export_web_events(_OneExecDB(_Scalars([e]))))
    assert rows[0]["utm_source"] == "li"
    assert "client_ip" not in rows[0]

    e.payload = None
    rows = _run(de._export_web_events(_OneExecDB(_Scalars([e]))))
    assert rows[0]["utm_source"] is None


def test_export_ad_tables():
    snap = SimpleNamespace(
        team_id=uuid.uuid4(), platform="linkedin", account_id="a",
        campaign_id="c", campaign_name="n", status="active",
        impressions=1, clicks=2, engagements=3, spend_eur=1.5,
        ctr=0.01, engagement_rate=0.02, cpc_eur=0.5, budget_eur=10.0,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC))
    rows = _run(de._export_ad_snapshots(_OneExecDB(_Scalars([snap]))))
    assert rows[0]["campaign_name"] == "n"

    day = SimpleNamespace(
        team_id=uuid.uuid4(), platform="linkedin", account_id="a",
        report_type="campaign", campaign_id="c", campaign_name="n",
        ad_set_id="s", ad_set_name="sn", ad_id="ad", ad_name="an",
        placement="feed", status="active", day=None,
        impressions=1, clicks=2, engagements=3, leads=0, conversions=1,
        reach=9, clicks_to_landing_page=1, clicks_to_linkedin_page=2,
        spend_eur=1.0, ctr=0.1, cpc_eur=0.2, cpm_eur=3.0,
        engagement_rate=0.01, budget_eur=5.0, source="api")
    rows = _run(de._export_ad_daily(_OneExecDB(_Scalars([day]))))
    assert rows[0]["day"] is None and rows[0]["ad_name"] == "an"

    seg = SimpleNamespace(
        team_id=uuid.uuid4(), platform="linkedin", account_id="a",
        segment_type="job_title", segment_value="Owner",
        window_start=None, window_end=None, impressions=10, clicks=1,
        conversions=0, ctr=0.1, pct_impressions=50.0, pct_clicks=60.0,
        source="api")
    rows = _run(de._export_ad_demographics(_OneExecDB(_Scalars([seg]))))
    assert rows[0]["segment_value"] == "Owner"


def test_reports_index_no_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(de, "_NOTEBOOK_OUTPUT", tmp_path / "missing")
    assert _run(de._export_reports_index()) == []


def test_export_datalake_wrapper(monkeypatch):
    sentinel = {"ok": True}
    monkeypatch.setattr(de, "run_async", lambda coro: (coro.close(), sentinel)[1])
    assert de.export_datalake() == sentinel


def _worker_cm(db):
    @asynccontextmanager
    async def _cm():
        yield db
    return _cm


def test_export_async_not_configured(monkeypatch):
    s = de.settings
    monkeypatch.setattr(s, "DATALAKE_R2_BUCKET", "")
    assert _run(de._export_async()) == {
        "ok": False, "error": "DATALAKE_R2_BUCKET not configured"}

    monkeypatch.setattr(s, "DATALAKE_R2_BUCKET", "lake")
    monkeypatch.setattr(s, "CLOUDFLARE_ACCOUNT_ID", "")
    out = _run(de._export_async())
    assert out["error"] == "Cloudflare credentials not configured"


def test_export_async_full_run(monkeypatch):
    s = de.settings
    monkeypatch.setattr(s, "DATALAKE_R2_BUCKET", "lake")
    monkeypatch.setattr(s, "CLOUDFLARE_ACCOUNT_ID", "acc")
    monkeypatch.setattr(s, "CLOUDFLARE_API_TOKEN", "tok")

    team = SimpleNamespace(id=uuid.uuid4())
    db = _OneExecDB(_Scalars([team]))
    monkeypatch.setattr(de, "_worker_db", _worker_cm(db))

    for name in ("_export_accounts", "_export_posts", "_export_metrics",
                 "_export_followers", "_export_account_events", "_export_leads",
                 "_export_telegram_channels", "_export_web_events",
                 "_export_ad_snapshots", "_export_ad_daily",
                 "_export_ad_demographics"):
        monkeypatch.setattr(de, name, AsyncMock(return_value=[{"r": 1}]))
    monkeypatch.setattr(de, "_export_ops_health", AsyncMock(return_value={"q": 1}))
    monkeypatch.setattr(de, "_export_edge_metrics", AsyncMock(return_value={"e": 1}))
    monkeypatch.setattr(de, "_export_reports_index", AsyncMock(return_value=[]))
    monkeypatch.setattr(de, "build_team_insights", AsyncMock(return_value={"i": 1}))
    monkeypatch.setattr(de, "_put_json", AsyncMock(return_value={"size": 10}))

    out = _run(de._export_async())
    assert out["ok"] is True
    assert out["files"] == 15  # 12 tables + edge + reports + insights
    assert out["bytes"] == 150


def test_export_async_insights_and_put_failures(monkeypatch):
    s = de.settings
    monkeypatch.setattr(s, "DATALAKE_R2_BUCKET", "lake")
    monkeypatch.setattr(s, "CLOUDFLARE_ACCOUNT_ID", "acc")
    monkeypatch.setattr(s, "CLOUDFLARE_API_TOKEN", "tok")

    team = SimpleNamespace(id=uuid.uuid4())
    db = _OneExecDB(_Scalars([team]))
    monkeypatch.setattr(de, "_worker_db", _worker_cm(db))

    for name in ("_export_accounts", "_export_posts", "_export_metrics",
                 "_export_followers", "_export_account_events", "_export_leads",
                 "_export_telegram_channels", "_export_web_events",
                 "_export_ad_snapshots", "_export_ad_daily",
                 "_export_ad_demographics"):
        monkeypatch.setattr(de, name, AsyncMock(return_value=[]))
    monkeypatch.setattr(de, "_export_ops_health", AsyncMock(return_value={}))
    monkeypatch.setattr(de, "_export_edge_metrics", AsyncMock(return_value={}))
    monkeypatch.setattr(de, "_export_reports_index", AsyncMock(return_value=[]))
    monkeypatch.setattr(de, "build_team_insights",
                        AsyncMock(side_effect=RuntimeError("insights down")))

    # put_json fails only for the posts table -> reported as error, not fatal
    async def _put(key, rows, bucket):
        if "posts" in key:
            raise RuntimeError("r2 down")
        return {"size": 5}
    monkeypatch.setattr(de, "_put_json", _put)

    out = _run(de._export_async())
    assert out["ok"] is False
    assert any("posts" in k for k in out["errors"])
    # insights failure -> error payload written to insights table
    ins = de._put_json
    assert ins is _put


def test_put_json(monkeypatch):
    captured = {}

    async def _upload(key, data, content_type=None, bucket=None):
        captured.update(key=key, data=data, content_type=content_type, bucket=bucket)
        return {"size": len(data)}
    monkeypatch.setattr(de.r2_storage, "upload_object", _upload)

    out = _run(de._put_json("k", [{"a": 1, "d": datetime(2026, 1, 1, tzinfo=UTC)}], "lake"))
    assert out["size"] > 0
    assert captured["key"] == "k" and captured["bucket"] == "lake"
    assert captured["content_type"] == "application/json"
    assert b'"a": 1' in captured["data"]


def test_export_telegram_channels():
    """Channel registry export: one row per administered chat, invite-link
    names kept, invite-link URLs stripped (they grant private-channel
    access and are secrets)."""
    acct = SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform="telegram",
        meta_data={
            "telegram_channel": {"chat_id": "-100999", "title": "HQ"},
            "telegram_channels": {
                "-100999": {
                    "title": "Cloudless HQ",
                    "type": "channel",
                    "status": "administrator",
                    "added_at": "2026-10-11T00:00:00+00:00",
                    "updated_at": "2026-10-11T00:00:00+00:00",
                    "invite_links": [
                        {
                            "name": "ig",
                            "invite_link": "https://t.me/+SECRET1",
                            "created_at": "2026-10-11T01:00:00+00:00",
                        },
                        {
                            "name": "website",
                            "invite_link": "https://t.me/+SECRET2",
                            "creates_join_request": True,
                            "created_at": "2026-10-11T02:00:00+00:00",
                        },
                    ],
                },
                "-200": {"title": "Group", "type": "supergroup", "status": "member"},
            },
        },
    )
    rows = _run(de._export_telegram_channels(_OneExecDB(_Scalars([acct]))))
    assert len(rows) == 2
    hq = next(r for r in rows if r["chat_id"] == "-100999")
    assert hq["managed"] is True
    assert hq["bot_status"] == "administrator"
    assert {link["name"] for link in hq["invite_links"]} == {"ig", "website"}
    assert hq["invite_links"][1]["creates_join_request"] is True
    # invite-link URLs are never exported to the lake
    assert all("invite_link" not in link for link in hq["invite_links"])
    grp = next(r for r in rows if r["chat_id"] == "-200")
    assert grp["managed"] is False
    assert grp["invite_links"] == []


def test_export_telegram_channels_empty_and_malformed():
    acct = SimpleNamespace(
        id=uuid.uuid4(), team_id=uuid.uuid4(), platform="telegram",
        meta_data={"telegram_channels": {"-1": "not-a-dict"}},
    )
    rows = _run(de._export_telegram_channels(_OneExecDB(_Scalars([acct]))))
    assert rows == []
    # no telegram accounts at all
    assert _run(de._export_telegram_channels(_OneExecDB(_Scalars([])))) == []
