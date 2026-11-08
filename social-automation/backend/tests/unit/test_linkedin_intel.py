"""Coverage for LinkedIn Ad Library intel: normalization, watch run, digest."""

from unittest.mock import AsyncMock

import pytest

import app.services.linkedin_ad_library as svc
from app.models.linkedin_intel import LinkedInAdIntel, LinkedInIntelWatch


def _element(**kw):
    el = {
        "adUrl": "https://www.linkedin.com/ad-library/detail/123",
        "isRestricted": False,
        "details": {
            "advertiser": {
                "advertiserName": "Amazon Web Services",
                "adPayer": "AWS EMEA SARL",
                "advertiserUrl": "https://aws.amazon.com",
            },
            "adStatistics": {
                "totalImpressions": {"from": 1000, "to": 5000},
                "firstImpressionAt": 1700000000000,
                "latestImpressionAt": 1700100000000,
                "impressionsDistributionByCountry": [{"country": "GR", "percentage": 80}],
            },
            "adTargeting": [
                {"facetName": "locations", "includedSegments": ["GR"], "excludedSegments": []},
                {"facetName": "jobFunctions", "includedSegments": ["IT"], "excludedSegments": ["HR"]},
            ],
        },
    }
    el.update(kw)
    return el


def test_summarize_flattens_element():
    row = svc._summarize(_element(), "cloud migration")
    assert row["ad_url"].endswith("/123")
    assert row["advertiser"] == "Amazon Web Services"
    assert row["ad_payer"] == "AWS EMEA SARL"
    assert row["impressions_from"] == 1000
    assert row["impressions_to"] == 5000
    assert row["first_impression_at"] == 1700000000000
    assert row["countries"] == [{"country": "GR", "percentage": 80}]
    assert row["targeting"]["jobFunctions"]["excluded"] == ["HR"]
    assert row["last_keyword"] == "cloud migration"


def test_summarize_skips_missing_ad_url():
    assert svc._summarize({"details": {}}, "x") is None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0]


class _FakeDb:
    """Queue-driven fake: execute() consumes results in call order."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        return _Result(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_run_watch_upserts_and_detects_endings(monkeypatch):
    watch = LinkedInIntelWatch(kind="keyword", value="cloud migration", country="GR")
    existing = LinkedInAdIntel(ad_url="https://x/1", status="active")
    stale = LinkedInAdIntel(ad_url="https://x/stale", status="active", miss_count=1)
    # execute order: seed(watches exist) → enabled watches → per-ad lookup → active scan
    db = _FakeDb([[watch], [watch], [existing], [stale]])

    async def fake_search_all(**kw):
        return {"ads": [svc._summarize(_element(adUrl="https://x/1"), "cloud migration")]}

    monkeypatch.setattr(svc, "_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(svc, "search_all", fake_search_all)

    summary = await svc.run_watch(db)

    assert summary["new"] == 0
    assert summary["updated"] == 1
    assert summary["ended"] == 1
    assert stale.status == "ended"
    assert existing.status == "active"
    assert existing.miss_count == 0
    assert watch.last_run_at is not None


@pytest.mark.asyncio
async def test_run_watch_seeds_defaults_and_counts_new(monkeypatch):
    watch = LinkedInIntelWatch(kind="keyword", value="cloud migration", country="GR")
    # seed returns 0 rows → seeds defaults (needs flush); then enabled → all watches
    db = _FakeDb([[], [watch], [], []])

    async def fake_search_all(**kw):
        return {"ads": [svc._summarize(_element(adUrl="https://x/new"), "cloud migration")]}

    monkeypatch.setattr(svc, "_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(svc, "search_all", fake_search_all)

    summary = await svc.run_watch(db)
    assert summary["seeded"] == len(svc.DEFAULT_WATCHES)
    assert summary["new"] == 1
    assert len(db.added) == len(svc.DEFAULT_WATCHES) + 1


@pytest.mark.asyncio
async def test_run_watch_no_token(monkeypatch):
    db = _FakeDb([[LinkedInIntelWatch(kind="keyword", value="x", country="GR")]] * 2)
    monkeypatch.setattr(svc, "_token", AsyncMock(return_value=None))
    assert await svc.run_watch(db) == {"skip": "no-linkedin-token"}


@pytest.mark.asyncio
async def test_search_all_paginates_and_sleeps(monkeypatch):
    pages = [
        {"paging": {"total": 2}, "elements": [_element(adUrl="https://x/1")]},
        {"paging": {"total": 2}, "elements": [_element(adUrl="https://x/2")]},
        {"elements": []},
    ]
    calls = []

    class _Resp:
        status_code = 200

        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            calls.append(params)
            return _Resp(pages[len(calls) - 1])

    sleeps = []
    monkeypatch.setattr(svc.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(svc.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(svc, "_token", AsyncMock(return_value="tok"))

    result = await svc.search_all(keyword="cloud", pages=5)
    assert len(result["ads"]) == 2
    assert len(calls) == 3
    assert sleeps == [svc.PAGE_DELAY_S, svc.PAGE_DELAY_S]
    assert calls[0]["keyword"] == "cloud"
    assert calls[0]["q"] == "criteria"


@pytest.mark.asyncio
async def test_search_page_error_status(monkeypatch):
    class _Resp:
        status_code = 426
        text = "NONEXISTENT_VERSION"

    class _Client:
        async def get(self, *a, **k):
            return _Resp()

    out = await svc.search_page(_Client(), "tok", keyword="x")
    assert out["error"] == 426


def test_format_digest():
    from app.worker.tasks.linkedin_intel import _format_digest

    text = _format_digest({"watches": 7, "new": 3, "updated": 20, "ended": 1, "errors": ["e1"]})
    assert "new=3" in text and "ended=1" in text and "err: e1" in text


def test_task_wrapper(monkeypatch):
    import app.worker.tasks.linkedin_intel as T

    monkeypatch.setattr(T, "run_async", lambda c: (c.close(), {"ok": 1})[1])
    assert T.run_linkedin_ad_library_watch() == {"ok": 1}
