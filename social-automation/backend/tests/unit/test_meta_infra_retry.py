"""Unit tests for app/worker/tasks/meta_infra_retry.py — the beat wrapper
around scripts/meta_infra.py. Covers timeout and the rc→alert mapping
(0 = success alert, 3 = human-gate silent, other = failure alert)."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

import app.worker.tasks.meta_infra_retry as T


class _Proc:
    def __init__(self, rc, out="done"):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


@pytest.fixture
def alerts(monkeypatch):
    sent = []

    def _fake_run_async(coro):
        if hasattr(coro, "close"):
            coro.close()
        sent.append(True)

    monkeypatch.setattr(T, "run_async", _fake_run_async)
    monkeypatch.setattr(T, "_SCRIPT", Path("/tmp/x/meta_infra.py"))
    return sent


def _run(monkeypatch, rc):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Proc(rc))
    return T.run_meta_infra_retry()


def test_success_alerts(monkeypatch, alerts):
    r = _run(monkeypatch, 0)
    assert r["status"] == "ok"
    assert alerts


def test_restriction_is_silent(monkeypatch, alerts):
    r = _run(monkeypatch, 3)
    assert r["status"] == "rc3"
    assert not alerts


def test_failure_alerts(monkeypatch, alerts):
    r = _run(monkeypatch, 1)
    assert r["status"] == "rc1"
    assert alerts


def test_timeout(monkeypatch, alerts):
    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="python", timeout=600)

    monkeypatch.setattr(subprocess, "run", _timeout)
    assert T.run_meta_infra_retry() == {"status": "timeout"}
    assert not alerts


def test_alert_exception_path(monkeypatch):
    async def _boom(text):
        raise RuntimeError("no slack")

    monkeypatch.setitem(__import__("sys").modules, "app.services.slack_notifications", None)
    asyncio.run(T._alert("x"))  # import fails inside try → exception path logs


def test_alert_calls_post(monkeypatch):
    import sys
    import types

    calls = []
    mod = types.ModuleType("app.services.slack_notifications")

    async def post_alert_to_slack(text):
        calls.append(text)

    mod.post_alert_to_slack = post_alert_to_slack
    monkeypatch.setitem(sys.modules, "app.services.slack_notifications", mod)
    asyncio.run(T._alert("hello"))
    assert calls == ["hello"]
