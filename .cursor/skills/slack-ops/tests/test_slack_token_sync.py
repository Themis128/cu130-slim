"""Unit tests for slack_token_sync.py — pure-logic coverage with stubbed I/O.

Run: python3 -m pytest .devin/skills/slack-ops/tests/ -q
No network, no docker: subprocess.run and urllib are stubbed.
"""

from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import slack_token_sync as sync  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated token dir / .env / compose dir / pending marker."""
    tok = tmp_path / "tok"
    tok.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("SLACK_BOT_TOKEN=xoxe-OLD\nOTHER=1\n")
    compose = tmp_path / "compose"
    compose.mkdir()
    monkeypatch.setattr(sync, "TOKEN_DIR", tok)
    monkeypatch.setattr(sync, "ENV_FILE", env_file)
    monkeypatch.setattr(sync, "COMPOSE_DIR", compose)
    monkeypatch.setattr(sync, "PENDING_MARKER", tmp_path / "pending")
    return SimpleNamespace(tok=tok, env=env_file, compose=compose, pending=tmp_path / "pending")


def _token_file(tok_dir: Path, name: str, *, scope="chat:write", ttl_s=43200, access="xoxe-AAA"):
    exp = int((time.time() + ttl_s) * 1000)
    p = tok_dir / name
    p.write_text(json.dumps({"scope": scope, "access_token": access, "refresh_token": "r-1", "expires_at": exp}))
    return p


def _stub_recreate(monkeypatch, rc=0):
    calls = []

    def fake_run(cmd, **kwargs):
        # distinguish "compose stop n8n" from "compose up ... n8n" and
        # "docker inspect n8n" probes — all end with the service name
        calls.append("stop" if "stop" in cmd else ("inspect" if "inspect" in cmd else cmd[-1]))
        return SimpleNamespace(returncode=rc, stderr=b"", stdout=b"true")

    monkeypatch.setattr(sync.subprocess, "run", fake_run)
    return calls


def test_token_file_selection_scope_and_order(env, monkeypatch):
    high = _token_file(env.tok, "a_slack_tokens.json", ttl_s=9000)
    _token_file(env.tok, "b_slack_tokens.json", scope="identity", ttl_s=9999)  # wrong scope
    (env.tok / "c_slack_tokens.json").write_text("not json")  # unreadable → skipped
    low = _token_file(env.tok, "d_slack_tokens.json", ttl_s=100)
    files = sync._slack_token_files()
    assert [p.name for _, p in files] == [high.name, low.name]
    assert files[0][0] > files[1][0]


def test_write_env_token_replaces(env):
    assert sync._write_env_token("xoxe-NEW") is True
    assert "SLACK_BOT_TOKEN=xoxe-NEW" in env.env.read_text()
    assert "OTHER=1" in env.env.read_text()  # untouched lines survive


def test_write_env_token_unchanged_and_append(env):
    assert sync._write_env_token("xoxe-OLD") is False  # same value → no write
    env.env.write_text("OTHER=1\n")  # no token line at all → appended
    assert sync._write_env_token("xoxe-NEW") is True
    assert env.env.read_text().endswith("SLACK_BOT_TOKEN=xoxe-NEW\n")


def test_recreate_services_hits_every_consumer_and_aggregates_rc(monkeypatch):
    calls = _stub_recreate(monkeypatch, rc=1)
    monkeypatch.setattr(sync, "_n8n_busy", lambda: False)
    rc, pending = sync._recreate_services(list(sync.SERVICES))
    assert rc == 1
    # 6 compose up calls (one per non-n8n consumer) + 1 failed "compose stop
    # n8n" — the failing stop pends n8n before its recreate is attempted
    assert len(calls) == len(sync.SERVICES)
    assert "social-api" in calls and "celery-beat" in calls
    assert sum(1 for c in calls if c.startswith("social-worker")) == 4
    # every compose call failed → every service stays pending for retry
    # (the failing "stop" for n8n also pends it, like the failing "up")
    assert pending == list(sync.SERVICES)


def test_recreate_stops_n8n_gracefully_before_recreate(monkeypatch):
    """Drain-check is a snapshot — a graceful stop before the recreate closes
    the window where a new execution could start after the last probe."""
    calls = _stub_recreate(monkeypatch, rc=0)
    monkeypatch.setattr(sync, "_n8n_busy", lambda: False)
    rc, pending = sync._recreate_services(["n8n"])
    assert rc == 0 and pending == []
    assert calls == ["stop", "n8n"]  # stop precedes the up --force-recreate


def test_recreate_pends_n8n_when_stop_fails(monkeypatch):
    """A failed graceful stop must not proceed to the force-recreate."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append("stop" if "stop" in cmd else cmd[-1])
        return SimpleNamespace(returncode=9 if "stop" in cmd else 0, stderr=b"boom", stdout=b"")

    monkeypatch.setattr(sync.subprocess, "run", fake_run)
    monkeypatch.setattr(sync, "_n8n_busy", lambda: False)
    rc, pending = sync._recreate_services(["n8n"])
    assert pending == ["n8n"]
    assert calls == ["stop"]  # never reached the force-recreate


def test_recreate_services_ok(monkeypatch):
    _stub_recreate(monkeypatch, rc=0)
    monkeypatch.setattr(sync, "_n8n_busy", lambda: False)
    rc, pending = sync._recreate_services(list(sync.SERVICES))
    assert rc == 0 and pending == []


def test_recreate_defers_n8n_while_executions_run(monkeypatch):
    """A forced n8n recreate mid-execution would kill it — skip and pend it."""
    calls = _stub_recreate(monkeypatch, rc=0)
    monkeypatch.setattr(sync, "_n8n_busy", lambda: True)
    rc, pending = sync._recreate_services(list(sync.SERVICES))
    assert rc == 0
    assert "n8n" not in calls
    assert pending == ["n8n"]


def test_n8n_busy_waits_then_drains(monkeypatch):
    monkeypatch.setattr(sync, "N8N_BUSY_POLL_S", 0)  # clamped to 1s, not an infinite spin
    monkeypatch.setattr(sync, "N8N_BUSY_WAIT_S", 60)
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    runs = iter([2, 1, 0])
    monkeypatch.setattr(sync, "_n8n_running_executions", lambda: next(runs))
    assert sync._n8n_busy() is False


def test_n8n_busy_still_busy_after_wait(monkeypatch):
    monkeypatch.setattr(sync, "N8N_BUSY_WAIT_S", 0)
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    monkeypatch.setattr(sync, "_n8n_running_executions", lambda: 3)
    assert sync._n8n_busy() is True


def test_n8n_probe_failure_with_running_container_is_busy(monkeypatch, capsys):
    """API unreachable but the container is up ⇒ executions may still run."""
    monkeypatch.setattr(sync, "_env_lookup", lambda n: "key")
    monkeypatch.setattr(
        sync.urllib.request, "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(OSError("conn refused")),
    )
    monkeypatch.setattr(sync, "_n8n_container_running", lambda: True)
    assert sync._n8n_busy() is True
    assert "state unknown" in capsys.readouterr().err


def test_n8n_probe_failure_with_stopped_container_is_idle(monkeypatch):
    """API unreachable AND container stopped ⇒ nothing left to kill."""
    monkeypatch.setattr(sync, "_env_lookup", lambda n: "key")
    monkeypatch.setattr(
        sync.urllib.request, "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(OSError("conn refused")),
    )
    monkeypatch.setattr(sync, "_n8n_container_running", lambda: False)
    assert sync._n8n_busy() is False


def test_n8n_probe_failure_with_unreadable_state_is_busy(monkeypatch):
    """Docker inspect itself failing ⇒ can't rule out running executions."""
    monkeypatch.setattr(sync, "_env_lookup", lambda n: None)  # no API key at all
    monkeypatch.setattr(sync, "_n8n_container_running", lambda: None)
    assert sync._n8n_busy() is True


def test_n8n_container_running_parses_docker_inspect(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["docker", "inspect"] and cmd[2] == "n8n"
        return SimpleNamespace(returncode=0, stdout=b"true\n", stderr=b"")

    monkeypatch.setattr(sync.subprocess, "run", fake_run)
    assert sync._n8n_container_running() is True

    monkeypatch.setattr(
        sync.subprocess, "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout=b"", stderr=b"no such container"),
    )
    assert sync._n8n_container_running() is None


def test_n8n_probe_counts_running(monkeypatch):
    monkeypatch.setattr(sync, "_env_lookup", lambda n: "key")

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"data": [{"id": 1}, {"id": 2}]}'

    monkeypatch.setattr(sync.urllib.request, "urlopen", lambda req, timeout: FakeResp())
    assert sync._n8n_running_executions() == 2


def test_pending_services_json_and_bare_marker(env):
    assert sync._pending_services() == []  # no marker → nothing pending
    env.pending.write_text('["n8n", "bogus-svc"]')
    assert sync._pending_services() == ["n8n"]  # unknown names dropped
    env.pending.write_text("")  # bare marker from an older run
    assert sync._pending_services() == list(sync.SERVICES)


def test_main_defers_n8n_and_retries_only_it(env, monkeypatch):
    _token_file(env.tok, "slack_tokens.json", ttl_s=99999)
    monkeypatch.setattr(sync, "_n8n_busy", lambda: True)
    calls = _stub_recreate(monkeypatch, rc=0)
    assert sync.main() == 0
    assert "n8n" not in calls
    assert json.loads(env.pending.read_text()) == ["n8n"]
    # next run: n8n idle → only it is recreated (graceful stop first), marker clears
    monkeypatch.setattr(sync, "_n8n_busy", lambda: False)
    calls.clear()
    assert sync.main() == 0
    assert calls == ["stop", "n8n"]
    assert not env.pending.exists()


def test_install_cron_idempotent(monkeypatch):
    written = []
    state = {"crontab": "0 0 * * * something\n"}

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["crontab", "-l"]:
            return SimpleNamespace(returncode=0, stdout=state["crontab"])
        if cmd[:2] == ["crontab", "-"]:
            state["crontab"] = kwargs["input"]
            written.append(kwargs["input"])
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(sync.subprocess, "run", fake_run)
    assert sync._install_cron() == 0
    assert len(written) == 1 and "slack_token_sync.py" in written[0]
    assert sync._install_cron() == 0  # second call: already installed, no write
    assert len(written) == 1


def test_main_flow_changed_token_recreates_and_clears_marker(env, monkeypatch):
    _token_file(env.tok, "slack_tokens.json", ttl_s=99999)  # fresh → no refresh
    monkeypatch.setattr(sync, "_recreate_services", lambda targets: (0, []))
    rc = sync.main()
    assert rc == 0
    assert "SLACK_BOT_TOKEN=xoxe-AAA" in env.env.read_text()
    assert not env.pending.exists()


def test_main_flow_failure_sets_marker_and_retries_unchanged(env, monkeypatch):
    _token_file(env.tok, "slack_tokens.json", ttl_s=99999)
    monkeypatch.setattr(sync, "_recreate_services", lambda targets: (7, ["n8n"]))
    assert sync.main() == 7  # failure rc propagates
    assert json.loads(env.pending.read_text()) == ["n8n"]
    # second run: token unchanged, but the marker forces a retry
    assert sync.main() == 7
    assert env.pending.exists()


def test_main_flow_marker_cleared_on_success(env, monkeypatch):
    _token_file(env.tok, "slack_tokens.json", ttl_s=99999)
    env.pending.touch()  # stale bare marker → full consumer set is pending
    monkeypatch.setattr(sync, "_recreate_services", lambda targets: (0, []))
    assert sync.main() == 0
    assert not env.pending.exists()


def test_rotated_token_file_is_0600(env, monkeypatch):
    """The refreshed token file must never remain group/world-readable."""
    path = _token_file(env.tok, "slack_tokens.json", ttl_s=100)  # stale → refresh branch
    path.chmod(0o666)
    resp = {
        "ok": True,
        "access_token": "xoxe-ROTATED",
        "refresh_token": "r-2",
        "expires_in": 43200,
    }
    monkeypatch.setattr(sync, "_refresh", lambda tok: resp)
    monkeypatch.setattr(sync, "_recreate_services", lambda targets: (0, []))
    assert sync.main() == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["access_token"] == "xoxe-ROTATED"
    assert "SLACK_BOT_TOKEN=xoxe-ROTATED" in env.env.read_text()


def test_refresh_uses_slack_oauth(monkeypatch):
    tok = {"refresh_token": "r-1", "access_token": "xoxe-OLD"}
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True, "access_token": "xoxe-NEW", "refresh_token": "r-2", "expires_in": 43200}).encode()

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode()
        return FakeResp()

    monkeypatch.setattr(sync.urllib.request, "urlopen", fake_urlopen)
    out = sync._refresh(tok)
    assert out["access_token"] == "xoxe-NEW"
    assert sync.TOKEN_ENDPOINT in captured["url"]
    assert "grant_type=refresh_token" in captured["body"]


def test_refresh_error_raises(monkeypatch):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": false, "error": "invalid_refresh_token"}'

    monkeypatch.setattr(sync.urllib.request, "urlopen", lambda req, timeout: FakeResp())
    with pytest.raises(RuntimeError, match="invalid_refresh_token"):
        sync._refresh({"refresh_token": "r-bad"})


def test_main_no_token_files_returns_1(env, monkeypatch):
    assert sync.main() == 1  # empty token dir


def test_main_entrypoint_dispatch(monkeypatch):
    """The __main__ block routes --install-cron to _install_cron (runpy re-executes
    the script in a fresh namespace, so the assertion goes through stubbed subprocess)."""
    import runpy

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd[:2]))
        if cmd[:2] == ["crontab", "-l"]:
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=0, stdout="", stderr=b"")

    monkeypatch.setattr(sync.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["slack_token_sync.py", "--install-cron"])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCRIPTS / "slack_token_sync.py"), run_name="__main__")
    assert e.value.code == 0
    assert ["crontab", "-"] in calls  # the install path was taken


def test_script_run_no_args_exits_1_on_empty_token_dir(tmp_path, monkeypatch):
    """Running the script as __main__ without --install-cron exercises sys.exit(main()).

    Empty SLACK_TOKEN_DIR → main() returns 1 → SystemExit(1). Real state is never
    touched: token dir/.env/compose dir are overridden via env, and the PATH stub
    makes any docker call a no-op. The child runs under `coverage run
    --parallel-mode` (cwd = repo root) so its lines merge into the pytest-cov
    report via the standard parallel-data combine.
    """
    import os
    import subprocess

    repo_root = Path(__file__).resolve().parents[4]
    empty = tmp_path / "tok"
    empty.mkdir()
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text("#!/bin/sh\nexit 0\n")
    (stub / "docker").chmod(0o755)
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("SLACK_", "COV_"))},
        "SLACK_TOKEN_DIR": str(empty),
        "SOCIALAUTO_ENV": str(tmp_path / "env"),
        "COMPOSE_DIR": str(tmp_path),
        "SLACK_SYNC_PENDING": str(tmp_path / "pending"),
        "PATH": f"{stub}:{os.environ['PATH']}",
    }
    script = SCRIPTS / "slack_token_sync.py"
    r = subprocess.run(
        [
            sys.executable, "-m", "coverage", "run", "--parallel-mode",
            "--source", str(SCRIPTS), str(script),
        ],
        env=env, capture_output=True, text=True, timeout=60, cwd=repo_root,
    )
    assert r.returncode == 1
    assert "no Slack-scoped token file found" in r.stderr
