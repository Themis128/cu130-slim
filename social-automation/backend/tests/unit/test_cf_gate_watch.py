"""Coverage for ``app.worker.tasks.cf_gate_watch`` (CF paid-workers gate chain)."""

from unittest.mock import MagicMock

from app.worker.tasks import cf_gate_watch as task


class _Proc:
    def __init__(self, rc=0, out="ok"):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def test_run_script_missing(monkeypatch):
    monkeypatch.setattr(task, "SCRIPTS", task.Path("/nonexistent"))
    ok, out = task._run("nope.py")
    assert not ok and "missing" in out


def test_run_script_invokes(monkeypatch, tmp_path):
    (tmp_path / "s.py").write_text("pass")
    monkeypatch.setattr(task, "SCRIPTS", tmp_path)
    calls = []
    monkeypatch.setattr(task.subprocess, "run", lambda *a, **k: (calls.append((a, k)) or _Proc()))
    ok, _ = task._run("s.py", "--x")
    assert ok and calls[0][0][0][1].endswith("s.py")


def test_run_script_nonzero(monkeypatch, tmp_path):
    (tmp_path / "s.py").write_text("pass")
    monkeypatch.setattr(task, "SCRIPTS", tmp_path)
    monkeypatch.setattr(task.subprocess, "run", lambda *a, **k: _Proc(rc=1, out="boom"))
    ok, out = task._run("s.py")
    assert not ok and "boom" in out


def test_token_active_empty():
    assert task._token_active("") is False


def test_token_active_true(monkeypatch):
    import io
    import json

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **k: _Resp(json.dumps({"result": {"status": "active"}}).encode()))
    assert task._token_active("tok") is True


def test_token_active_error(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert task._token_active("tok") is False


def test_run_blocked_on_token(monkeypatch):
    monkeypatch.setattr(task, "_token_active", lambda t: False)
    monkeypatch.setattr(task, "_run", lambda *a, **k: (False, "mint fail"))
    esc = MagicMock()
    monkeypatch.setattr(task, "_escalate", esc)
    out = task.run()
    assert out["status"] == "blocked" and out["gate"] == "token"
    esc.assert_called_once()


def test_run_all_ok(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setattr(task, "_token_active", lambda t: True)
    monkeypatch.setattr(task, "_run", lambda *a, **k: (True, ""))
    esc = MagicMock()
    monkeypatch.setattr(task, "_escalate", esc)
    out = task.run()
    assert out["status"] == "complete"
    assert all(r["ok"] for r in out["results"].values())


def test_run_partial_on_first_step(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setattr(task, "_token_active", lambda t: True)
    monkeypatch.setattr(task, "_run",
                        lambda s, *a, **k: (False, "x") if s == "cf_email_destination.py" else (True, ""))
    monkeypatch.setattr(task, "_escalate", MagicMock())
    out = task.run()
    assert out["status"] == "partial" and out["failed"] == "destination"


def test_run_logpush_failure_tolerated(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setattr(task, "_token_active", lambda t: True)
    monkeypatch.setattr(task, "_run",
                        lambda s, *a, **k: (False, "x") if s == "cf_logpush_setup.py" else (True, ""))
    monkeypatch.setattr(task, "_escalate", MagicMock())
    out = task.run()
    assert out["status"] == "partial" and "logpush" in out["results"]


def test_run_automint_recovers(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    states = iter([False, True])
    monkeypatch.setattr(task, "_token_active", lambda t: next(states, True))
    monkeypatch.setattr(task, "_run", lambda s, *a, **k: (True, ""))
    monkeypatch.setattr(task, "_escalate", MagicMock())
    assert task.run()["status"] == "complete"


def test_escalate_swallows(monkeypatch):
    monkeypatch.setattr("app.services.slack_notifications.send_telegram_alert",
                        MagicMock(side_effect=RuntimeError("x")))
    task._escalate("msg")  # must not raise
