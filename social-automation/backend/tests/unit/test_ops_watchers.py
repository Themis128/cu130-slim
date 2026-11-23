"""ops_watchers registry: model contract, celery gate skip, API status."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from celery.exceptions import Ignore

from app.models.ops_watcher import (
    OpsWatcher,
    WatcherExecutor,
    WatcherKind,
    WatcherManagedBy,
    WatcherTier,
)
from app.worker import watchdog_gate as WG


def _watcher(**kw) -> OpsWatcher:
    w = OpsWatcher(
        name="t-" + uuid.uuid4().hex[:8],
        title="t",
        tier=WatcherTier.PLATFORM,
        kind=WatcherKind.WATCH,
        executor=WatcherExecutor.CELERY,
        managed_by=WatcherManagedBy.PLATFORM,
        command="app.worker.tasks.x.run",
    )
    for k, v in kw.items():
        setattr(w, k, v)
    return w


def test_model_defaults():
    w = _watcher()
    assert w.enabled is True
    assert w.status == "unknown"
    assert w.consecutive_failures == 0


# ── watchdog gate ────────────────────────────────────────────────────────


def test_gate_skips_disabled_registered(monkeypatch):
    monkeypatch.setattr(WG, "_load_enabled", lambda: {"app.worker.tasks.x.run": False})
    task = SimpleNamespace(name="app.worker.tasks.x.run")
    with pytest.raises(Ignore):
        WG.gate(task=task)


def test_gate_allows_unregistered(monkeypatch):
    monkeypatch.setattr(WG, "_load_enabled", lambda: {})
    WG.gate(task=SimpleNamespace(name="app.worker.tasks.unknown.run"))  # no raise


def test_gate_allows_enabled(monkeypatch):
    monkeypatch.setattr(WG, "_load_enabled", lambda: {"app.worker.tasks.x.run": True})
    WG.gate(task=SimpleNamespace(name="app.worker.tasks.x.run"))


def test_gate_fails_open_on_db_error(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(WG, "_load_enabled", _boom)
    WG.gate(task=SimpleNamespace(name="anything"))  # must not raise


# ── API status projection ────────────────────────────────────────────────


def test_out_marks_stale_platform_watcher():
    from app.api.ops_watchers import _out

    w = _watcher(last_finished_at=datetime.now(UTC) - timedelta(hours=10), interval_seconds=60, status="ok")
    assert _out(w)["status"] == "stale"


def test_out_disabled_wins():
    from app.api.ops_watchers import _out

    w = _watcher(enabled=False, status="ok")
    assert _out(w)["status"] == "disabled"


def test_out_fresh_is_ok():
    from app.api.ops_watchers import _out

    w = _watcher(last_finished_at=datetime.now(UTC) - timedelta(minutes=5), interval_seconds=300, status="ok")
    assert _out(w)["status"] == "ok"
