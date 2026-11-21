"""Unit tests for swetrix_sync — project map parsing, day bucketing, upsert."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import swetrix_sync


class _ScalarResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    """Minimal AsyncSession stand-in: tracks added rows, returns no existing."""

    def __init__(self):
        self.added: list = []
        self.commits = 0

    async def execute(self, _stmt):
        return _ScalarResult(None)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


def _settings(**over):
    base = {
        "SWETRIX_API_KEY": "swx_test",
        "SWETRIX_API_URL": "https://analytics.example.com",
        "SWETRIX_PROJECTS": '{"cloudless.gr": "pid123456789"}',
        "SWETRIX_TEAM_ID": str(uuid.uuid4()),
        "SWETRIX_TIMEZONE": "Europe/Athens",
        "SWETRIX_LOOKBACK_DAYS": 90,
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestProjectMap:
    def test_empty_when_unset(self):
        with patch.object(swetrix_sync, "settings", _settings(SWETRIX_PROJECTS="")):
            assert swetrix_sync.project_map() == {}

    def test_invalid_json_returns_empty(self):
        with patch.object(swetrix_sync, "settings", _settings(SWETRIX_PROJECTS="{not json")):
            assert swetrix_sync.project_map() == {}

    def test_parses_domain_pid_map(self):
        with patch.object(swetrix_sync, "settings", _settings()):
            assert swetrix_sync.project_map() == {"cloudless.gr": "pid123456789"}

    def test_non_dict_json_returns_empty(self):
        with patch.object(swetrix_sync, "settings", _settings(SWETRIX_PROJECTS='["a"]')):
            assert swetrix_sync.project_map() == {}


class TestDayOf:
    def test_parses_iso_with_offset(self):
        assert swetrix_sync._day_of("2026-11-22T00:00:00+02:00") == date(2026, 11, 22)

    def test_none_and_garbage(self):
        assert swetrix_sync._day_of(None) is None
        assert swetrix_sync._day_of("not-a-date") is None


class TestSyncProject:
    @pytest.mark.asyncio
    async def test_upserts_daily_rows_and_breakdown_meta(self):
        series = [
            {"timestamp": "2026-11-21T00:00:00+02:00", "visitors": 3, "pageviews": 7,
             "session_duration": 12.5, "bounce_rate": 42.0},
            {"timestamp": "2026-11-22T00:00:00+02:00", "visitors": 1, "pageviews": 2,
             "session_duration": 0, "bounce_rate": 100.0},
            {"timestamp": "bad", "visitors": 9},
        ]
        breakdown = {"page": [{"value": "/", "visitors": 1}], "country": [],
                     "referrer": [], "utm_source": []}
        db = _FakeDB()
        with (
            patch.object(swetrix_sync, "_timeseries", new=AsyncMock(return_value=series)),
            patch.object(swetrix_sync, "_breakdown", new=AsyncMock(side_effect=lambda c, p, d: breakdown[d])),
        ):
            result = await swetrix_sync.sync_project(db, "cloudless.gr", "pid1", uuid.uuid4())

        assert result["days"] == 2 and result["error"] is None
        assert len(db.added) == 2 and db.commits == 1
        latest = max(db.added, key=lambda r: r.date)
        assert latest.date == date(2026, 11, 22)
        assert latest.visitors == 1 and latest.pageviews == 2
        assert latest.meta["top_pages"] == [{"value": "/", "visitors": 1}]

    @pytest.mark.asyncio
    async def test_existing_row_is_updated_not_duplicated(self):
        existing = SimpleNamespace(
            swetrix_pid="", visitors=0, pageviews=0, bounce_rate=0.0,
            session_duration_sec=0.0, synced_at=None, meta={}, date=date(2026, 11, 21),
        )

        class _DB(_FakeDB):
            async def execute(self, _stmt):
                return _ScalarResult(existing)

        series = [{"timestamp": "2026-11-21T00:00:00+02:00", "visitors": 5,
                   "pageviews": 9, "session_duration": 3.0, "bounce_rate": 20.0}]
        with (
            patch.object(swetrix_sync, "_timeseries", new=AsyncMock(return_value=series)),
            patch.object(swetrix_sync, "_breakdown", new=AsyncMock(return_value=[])),
        ):
            await swetrix_sync.sync_project(_DB(), "cloudless.gr", "pid1", uuid.uuid4())
        assert existing.visitors == 5 and existing.pageviews == 9
        assert existing.bounce_rate == 20.0


class TestSyncAll:
    @pytest.mark.asyncio
    async def test_no_api_key_short_circuits(self):
        with patch.object(swetrix_sync, "settings", _settings(SWETRIX_API_KEY="")):
            out = await swetrix_sync.sync_all_swetrix(_FakeDB())
        assert out["ok"] is False and "SWETRIX_API_KEY" in out["error"]

    @pytest.mark.asyncio
    async def test_no_projects_short_circuits(self):
        with patch.object(swetrix_sync, "settings", _settings(SWETRIX_PROJECTS="")):
            out = await swetrix_sync.sync_all_swetrix(_FakeDB())
        assert out["ok"] is False and "SWETRIX_PROJECTS" in out["error"]

    @pytest.mark.asyncio
    async def test_project_failure_does_not_abort_sweep(self):
        with patch.object(swetrix_sync, "settings", _settings()):
            with patch.object(
                swetrix_sync, "sync_project",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ):
                out = await swetrix_sync.sync_all_swetrix(_FakeDB())
        assert out["ok"] is False
        assert out["projects"][0]["error"] == "boom"


class TestCeleryTask:
    def test_sync_swetrix_task_runs_service(self, monkeypatch):
        """The beat task must open a session and call sync_all_swetrix."""
        import asyncio
        from contextlib import asynccontextmanager

        from app.worker.tasks import swetrix_sync as task_mod

        db = object()

        @asynccontextmanager
        async def _cm():
            yield db

        sentinel = {"ok": True, "projects": [{"domain": "cloudless.gr"}]}
        mock_sync = AsyncMock(return_value=sentinel)
        monkeypatch.setattr(task_mod, "task_session", lambda: _cm())
        monkeypatch.setattr(task_mod.swetrix_sync, "sync_all_swetrix", mock_sync)
        monkeypatch.setattr(task_mod, "run_async", lambda coro: asyncio.run(coro))

        out = task_mod.sync_swetrix()
        assert out == sentinel
        mock_sync.assert_awaited_once_with(db)
