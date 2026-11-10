"""Unit tests for app/worker/tasks/instagram_reauth.py — the Celery wrapper
around scripts/instagram_reauth.py. Covers missing-script, timeout, and the
rc→alert mapping (3 = human gate, other non-zero = failure, 0/2 = quiet)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import app.worker.tasks.instagram_reauth as T


class _Proc:
    def __init__(self, rc, out="done"):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


@pytest.fixture
def alerts(monkeypatch):
    sent = []

    def _fake_run_async(coro):
        # close without awaiting — the alert text is already composed
        if hasattr(coro, "close"):
            coro.close()
        sent.append(True)

    monkeypatch.setattr(T, "run_async", _fake_run_async)
    return sent


def test_missing_script(monkeypatch):
    monkeypatch.setattr(T, "_SCRIPT", Path("/nonexistent/instagram_reauth.py"))
    assert T.run_instagram_reauth() == {"status": "missing-script"}


def test_timeout(monkeypatch, tmp_path):
    script = tmp_path / "instagram_reauth.py"
    script.write_text("# stub")
    monkeypatch.setattr(T, "_SCRIPT", script)

    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="python", timeout=600)

    monkeypatch.setattr(subprocess, "run", _timeout)
    assert T.run_instagram_reauth() == {"status": "timeout"}


def test_rc_zero_and_two_quiet(monkeypatch, tmp_path, alerts):
    script = tmp_path / "instagram_reauth.py"
    script.write_text("# stub")
    monkeypatch.setattr(T, "_SCRIPT", script)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Proc(0, "reauthed"))
    assert T.run_instagram_reauth()["status"] == "ok"
    assert alerts == []

    # rc=2 = throttled/skipped — quiet, no alert
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Proc(2))
    assert T.run_instagram_reauth()["status"] == "rc2"
    assert alerts == []


def test_rc_three_alerts_human_gate(monkeypatch, tmp_path, alerts):
    script = tmp_path / "instagram_reauth.py"
    script.write_text("# stub")
    monkeypatch.setattr(T, "_SCRIPT", script)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Proc(3, "login wall"))
    assert T.run_instagram_reauth()["status"] == "rc3"
    assert len(alerts) == 1


def test_rc_other_alerts_failure(monkeypatch, tmp_path, alerts):
    script = tmp_path / "instagram_reauth.py"
    script.write_text("# stub")
    monkeypatch.setattr(T, "_SCRIPT", script)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Proc(4, "flow died"))
    assert T.run_instagram_reauth()["status"] == "rc4"
    assert len(alerts) == 1
