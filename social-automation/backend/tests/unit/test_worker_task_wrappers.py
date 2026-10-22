"""Worker-task wrapper coverage — enabled gates, run_async dispatch, error paths.

The service layers are tested by their own suites; here we only prove the
task shell calls them (or stays silent) correctly.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock


class _Ctx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


def _factory(db):
    return lambda: (lambda: _Ctx(db))


class _ExecDB:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *a, **k):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self._rows))


# ── company_brain.write_company_brain_journal ────────────────────────


def test_brain_journal_disabled_returns():
    from app.worker.tasks import company_brain as T

    T.company_brain.company_brain_enabled = lambda: False
    T.write_company_brain_journal()  # silent return


def test_brain_journal_enabled_writes_per_team(monkeypatch):
    from app.services import company_brain as svc
    from app.worker.tasks import company_brain as T

    monkeypatch.setattr(svc, "company_brain_enabled", lambda: True)
    written = []
    monkeypatch.setattr(
        svc,
        "write_daily_journal",
        AsyncMock(side_effect=lambda db, team: written.append(team.name) or "j"),
    )
    db = _ExecDB([SimpleNamespace(name="t1"), SimpleNamespace(name="t2")])
    monkeypatch.setattr(T, "get_session_factory", _factory(db))
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    T.write_company_brain_journal()
    assert written == ["t1", "t2"]


def test_brain_journal_failure_logged(monkeypatch):
    from app.services import company_brain as svc
    from app.worker.tasks import company_brain as T

    monkeypatch.setattr(svc, "company_brain_enabled", lambda: True)
    monkeypatch.setattr(T, "run_async", lambda coro: (_ for _ in ()).throw(RuntimeError("x")))
    T.write_company_brain_journal()  # warned, no raise


# ── email_drip.run_email_drip_sweep ──────────────────────────────────


def test_email_drip_disabled_returns_none():
    from app.worker.tasks import email_drip as T

    T.email_drip.email_drip_enabled = lambda: False
    assert T.run_email_drip_sweep() is None


def test_email_drip_enabled_runs_sweep(monkeypatch):
    from app.services import email_drip as svc
    from app.worker.tasks import email_drip as T

    monkeypatch.setattr(svc, "email_drip_enabled", lambda: True)
    seen = {}

    async def _sweep(dry_run=False):
        seen["dry_run"] = dry_run
        return {"sent": 3}

    monkeypatch.setattr(svc, "run_email_drip_sweep", _sweep)
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    assert T.run_email_drip_sweep(dry_run=True) == {"sent": 3}
    assert seen["dry_run"] is True


def test_email_drip_failure_returns_none(monkeypatch):
    from app.services import email_drip as svc
    from app.worker.tasks import email_drip as T

    monkeypatch.setattr(svc, "email_drip_enabled", lambda: True)
    monkeypatch.setattr(svc, "run_email_drip_sweep", lambda **kw: _boom())
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))

    async def _boom():
        raise RuntimeError("drip down")

    assert T.run_email_drip_sweep() is None


# ── nextcloud_sync.sync_nextcloud_surfaces ───────────────────────────


def test_nextcloud_sync_disabled_returns():
    from app.worker.tasks import nextcloud_sync as T

    T.nextcloud_sync.nextcloud_sync_enabled = lambda: False
    T.sync_nextcloud_surfaces()


def test_nextcloud_sync_enabled_calls_sync_all(monkeypatch):
    from app.services import nextcloud_sync as svc
    from app.worker.tasks import nextcloud_sync as T

    monkeypatch.setattr(svc, "nextcloud_sync_enabled", lambda: True)
    seen = {}

    async def _sync(factory):
        seen["factory"] = factory

    monkeypatch.setattr(svc, "sync_all", _sync)
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    T.sync_nextcloud_surfaces()
    assert "factory" in seen


def test_nextcloud_sync_failure_logged(monkeypatch):
    from app.services import nextcloud_sync as svc
    from app.worker.tasks import nextcloud_sync as T

    monkeypatch.setattr(svc, "nextcloud_sync_enabled", lambda: True)
    monkeypatch.setattr(svc, "sync_all", lambda f: _boom())
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))

    async def _boom():
        raise RuntimeError("sync down")

    T.sync_nextcloud_surfaces()


# ── crm_hygiene.run_crm_hygiene ──────────────────────────────────────


def test_crm_hygiene_disabled_returns():
    from app.worker.tasks import crm_hygiene as T

    T.crm_hygiene.crm_hygiene_enabled = lambda: False
    T.run_crm_hygiene()


def test_crm_hygiene_enabled_and_failure(monkeypatch):
    from app.services import crm_hygiene as svc
    from app.worker.tasks import crm_hygiene as T

    monkeypatch.setattr(svc, "crm_hygiene_enabled", lambda: True)
    ran = []

    async def _sweep():
        ran.append(1)

    monkeypatch.setattr(svc, "run_crm_hygiene", _sweep)
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    T.run_crm_hygiene()
    assert ran == [1]

    async def _boom():
        raise RuntimeError("hygiene down")

    monkeypatch.setattr(svc, "run_crm_hygiene", _boom)
    T.run_crm_hygiene()  # warned, no raise


# ── promo_scan.run_promo_scan ────────────────────────────────────────


def test_promo_scan_runs_scan_with_db(monkeypatch):
    from app.services import promo_scan as svc
    from app.worker.tasks import promo_scan as T

    db = object()
    monkeypatch.setattr(T, "_worker_db", lambda: _Ctx(db))
    seen = {}

    async def _scan(d, send=True):
        seen["db"] = d
        seen["send"] = send
        return {"found": 2}

    monkeypatch.setattr(svc, "run_promo_scan", _scan)
    monkeypatch.setattr(T, "run_async", lambda coro: asyncio.run(coro))
    assert T.run_promo_scan(send=False) == {"found": 2}
    assert seen == {"db": db, "send": False}
