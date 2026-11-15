"""Coverage: threads_reauth task — script-missing, timeout, rc branches, _alert."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from app.worker.tasks import threads_reauth as T


def _proc(rc, out="done"):
    return SimpleNamespace(returncode=rc, stdout=out, stderr="")


def _script(tmp_path, monkeypatch):
    script = tmp_path / "threads_reauth.py"
    script.write_text("pass")
    monkeypatch.setattr(T, "_SCRIPT", script)
    # neutralize run_async — the alert coroutine is covered separately
    monkeypatch.setattr(T, "run_async", lambda coro: coro.close())


def test_missing_script(monkeypatch):
    monkeypatch.setattr(T, "_SCRIPT", T.Path("/nonexistent/threads_reauth.py"))
    assert T.run_threads_reauth.run() == {"status": "missing-script"}


def test_timeout(monkeypatch, tmp_path):
    _script(tmp_path, monkeypatch)

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="python", timeout=600)

    monkeypatch.setattr(T.subprocess, "run", _timeout)
    assert T.run_threads_reauth.run() == {"status": "timeout"}


def test_rc_zero_and_two_no_alert(monkeypatch, tmp_path):
    _script(tmp_path, monkeypatch)
    for rc in (0, 2):
        monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: _proc(rc, "tail-out"))
        out = T.run_threads_reauth.run()
        assert out["status"] == ("ok" if rc == 0 else "rc2")
        assert out["tail"] == "tail-out"


def test_rc3_and_other_alert(monkeypatch, tmp_path):
    _script(tmp_path, monkeypatch)
    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: _proc(3, "gate"))
    assert T.run_threads_reauth.run()["status"] == "rc3"

    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: _proc(1, "boom"))
    out = T.run_threads_reauth.run()
    assert out["status"] == "rc1" and out["tail"] == "boom"


@pytest.mark.asyncio
async def test_alert_success_and_failure(monkeypatch):
    posted = []

    class _Slack:
        @staticmethod
        async def post_alert_to_slack(text):
            posted.append(text)

    import app.services.slack_notifications as sn

    monkeypatch.setattr(sn, "post_alert_to_slack", _Slack.post_alert_to_slack)
    await T._alert("hello")
    assert posted == ["hello"]

    def _boom(text):
        raise RuntimeError("slack down")

    monkeypatch.setattr(sn, "post_alert_to_slack", _boom)
    await T._alert("again")  # swallowed, logged
