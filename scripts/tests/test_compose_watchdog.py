"""Behavioural tests for scripts/compose-watchdog.sh (safe-autonomy rules).

The script runs against a fake `docker` on PATH that records every call,
so no Docker daemon is needed (runs in CI)."""
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "compose-watchdog.sh"

FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE_CALLS"
case "$1" in
  info) [ "${FAKE_DOCKER_DOWN:-0}" = 1 ] && exit 1; exit 0;;
  desktop) exit 0;;
  inspect)
    for a in "$@"; do last="$a"; done
    if [ "$last" = "${FAKE_TARGET:-social-api}" ]; then echo "${FAKE_STATUS:-running false}"; else echo "running false"; fi;;
  logs) printf '%s\n' "${FAKE_LOGS:-}";;
  update|start|restart) exit 0;;
  *) exit 1;;
esac
"""


@pytest.fixture()
def env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / "compose-watchdog.sh")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    e = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "FAKE_CALLS": str(tmp_path / "calls"),
        "WD_STATE_DIR": str(tmp_path / "state"),
        "SLACK_ALERTS_WEBHOOK_URL": "",
    }
    return repo, e, tmp_path


def run(repo, e, **extra):
    env = {**e, **{k: str(v) for k, v in extra.items()}}
    subprocess.run(["bash", str(repo / "scripts" / "compose-watchdog.sh")],
                   env=env, check=True, timeout=60)


def calls(tmp):
    p = tmp / "calls"
    return p.read_text().splitlines() if p.exists() else []


def log(repo):
    return (repo / "logs" / "watchdog.log").read_text()


def test_fresh_deploy_lock_skips_everything(env):
    repo, e, tmp = env
    (repo / ".deploy.lock").write_text("test-agent deploying")
    run(repo, e, FAKE_STATUS="restarting false")
    assert calls(tmp) == []
    assert "deploy lock present" in log(repo)


def test_stale_deploy_lock_is_ignored(env):
    repo, e, tmp = env
    lock = repo / ".deploy.lock"
    lock.write_text("old")
    old = time.time() - 7200
    os.utime(lock, (old, old))
    run(repo, e, FAKE_STATUS="restarting false")
    assert "start social-api" in calls(tmp)
    assert "STALE" in log(repo)


def test_migration_crash_disables_restart_and_never_restarts(env):
    repo, e, tmp = env
    logs = "alembic.util.exc.CommandError: Can't locate revision identified by 'abc123'"
    for t in (1000, 5000):
        run(repo, e, FAKE_STATUS="restarting false", FAKE_LOGS=logs, WD_NOW=t)
    c = calls(tmp)
    assert c.count("update --restart=no social-api") == 1
    assert "start social-api" not in c and "restart social-api" not in c
    assert (Path(e["WD_STATE_DIR"]) / "migration-social-api").exists()


def test_restart_cap_three_per_hour_with_backoff(env):
    repo, e, tmp = env
    # t=0 #1; t=30 backoff(60s); t=100 #2; t=200 backoff(300s); t=500 #3; t=2000 cap
    for t in (100000, 100030, 100100, 100200, 100500, 102000):
        run(repo, e, FAKE_STATUS="exited false", WD_NOW=t)
    assert calls(tmp).count("start social-api") == 3
    text = log(repo)
    assert text.count("backoff") == 2
    assert "hit the restart cap" in text


def test_restart_window_rolls_after_an_hour(env):
    repo, e, tmp = env
    for t in (100000, 100100, 100500, 104000):
        run(repo, e, FAKE_STATUS="exited false", WD_NOW=t)
    assert calls(tmp).count("start social-api") == 4


def test_docker_desktop_restart_capped_twice_per_day(env):
    repo, e, tmp = env
    for t in (1700000000, 1700002000, 1700004000, 1700006000):
        run(repo, e, FAKE_DOCKER_DOWN=1, WD_NOW=t)
    assert sum(1 for c in calls(tmp) if c.startswith("desktop restart")) == 2
    assert "cap reached" in log(repo)


def test_state_not_in_tmp():
    text = SCRIPT.read_text()
    assert '"/tmp/wd-' not in text and "LKF=\"/tmp" not in text
