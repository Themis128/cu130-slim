"""Unit tests for the datalake export serializers (pure helpers, no DB)."""

from __future__ import annotations

from datetime import UTC, datetime

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
