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
        calls.append(cmd[-1])
        return SimpleNamespace(returncode=rc, stderr=b"")

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
    rc = sync._recreate_services()
    assert rc == 1
    assert len(calls) == len(sync.SERVICES)
    assert "social-api" in calls and "celery-beat" in calls
    assert sum(1 for c in calls if c.startswith("social-worker")) == 4


def test_recreate_services_ok(monkeypatch):
    _stub_recreate(monkeypatch, rc=0)
    assert sync._recreate_services() == 0


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
    monkeypatch.setattr(sync, "_recreate_services", lambda: 0)
    rc = sync.main()
    assert rc == 0
    assert "SLACK_BOT_TOKEN=xoxe-AAA" in env.env.read_text()
    assert not env.pending.exists()


def test_main_flow_failure_sets_marker_and_retries_unchanged(env, monkeypatch):
    _token_file(env.tok, "slack_tokens.json", ttl_s=99999)
    monkeypatch.setattr(sync, "_recreate_services", lambda: 7)
    assert sync.main() == 7  # failure rc propagates
    assert env.pending.exists()
    # second run: token unchanged, but the marker forces a retry
    assert sync.main() == 7
    assert env.pending.exists()


def test_main_flow_marker_cleared_on_success(env, monkeypatch):
    _token_file(env.tok, "slack_tokens.json", ttl_s=99999)
    env.pending.touch()  # stale marker from an earlier failed recreation
    monkeypatch.setattr(sync, "_recreate_services", lambda: 0)
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
    monkeypatch.setattr(sync, "_recreate_services", lambda: 0)
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
