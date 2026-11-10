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
    prev=""
    for a in "$@"; do
      if [ "$prev" = "-f" ] && [[ "$a" == *com.docker.compose.service* ]]; then echo "fake-svc"; exit 0; fi
      if [ "$prev" = "-f" ] && [[ "$a" == *Health.Status* ]]; then echo "${FAKE_HEALTH:-healthy}"; exit 0; fi
      if [ "$prev" = "-f" ] && [ "$a" = '{{.State.Status}}' ]; then
        if [ -n "${FAKE_CSTATE_FILE:-}" ] && [ -f "$FAKE_CSTATE_FILE" ]; then cat "$FAKE_CSTATE_FILE"; else echo "${FAKE_CSTATE:-running}"; fi
        exit 0
      fi
      if [ "$prev" = "-f" ] && [ "$a" = '{{.State.Error}}' ]; then printf '%s' "${FAKE_STATE_ERROR:-}"; exit 0; fi
      if [ "$prev" = "-f" ] && [[ "$a" == *Mounts* ]]; then printf '%s' "${FAKE_MOUNTS:-}"; exit 0; fi
      if [ "$prev" = "-f" ] && [[ "$a" == *Config.Image* ]]; then echo "fake-img"; exit 0; fi
      prev="$a"
    done
    for a in "$@"; do last="$a"; done
    if [ "$last" = "${FAKE_TARGET:-social-api}" ]; then echo "${FAKE_STATUS:-running false}"; else echo "running false"; fi;;
  logs) printf '%s\n' "${FAKE_LOGS:-}";;
  update|restart) exit 0;;
  start)
    [ -n "${FAKE_CSTATE_FILE:-}" ] && echo running > "$FAKE_CSTATE_FILE"
    exit 0;;
  compose) exit 0;;
  exec) exit 1;;
  run)
    # container_uid_gid fallback: docker run --rm --entrypoint id IMG -u|-g
    if [[ "$*" == *entrypoint*id* ]]; then
      case "$*" in *-u) echo "${FAKE_UID:-1000}";; *) echo "${FAKE_GID:-1000}";; esac
    fi
    exit 0;;
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
    # Port probes must not hit the real localhost — answer 200 by default;
    # tests override with FAKE_CURL_CODE.
    curl = bindir / "curl"
    curl.write_text('#!/usr/bin/env bash\necho "curl $*" >> "$FAKE_CALLS"\n'
                    'case "$*" in *8787/status*) echo "${FAKE_STACKOPS_JSON:-}";; '
                    '*) echo "${FAKE_CURL_CODE:-200}";; esac\n')
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    # tailscale fake — `tailscale ip -4` prints FAKE_TS_IP (default: none)
    ts = bindir / "tailscale"
    ts.write_text('#!/usr/bin/env bash\n[ -n "${FAKE_TS_IP:-}" ] && echo "$FAKE_TS_IP" || exit 1\n')
    ts.chmod(ts.stat().st_mode | stat.S_IEXEC)
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


def test_mount_broken_exited_container_is_recreated_not_restarted(env):
    repo, e, tmp = env
    mount_err = ('failed to create shim task: OCI runtime create failed: '
                 'error mounting "/run/desktop/mnt/host/wsl/..." to rootfs '
                 'at "/entrypoint.sh": not a directory')
    run(repo, e, FAKE_STATUS="exited false", FAKE_LOGS=mount_err, WD_NOW=1000)
    c = calls(tmp)
    assert "start social-api" not in c and "restart social-api" not in c
    assert any("compose up -d --no-deps --force-recreate fake-svc" in x for x in c)
    assert "force-recreated" in log(repo)


def test_exited_container_without_mount_error_still_restarts(env):
    repo, e, tmp = env
    run(repo, e, FAKE_STATUS="exited false", FAKE_LOGS="some app error", WD_NOW=1000)
    assert "start social-api" in calls(tmp)


def test_dead_port_bindings_restart_stack_ops(env):
    repo, e, tmp = env
    run(repo, e, FAKE_TARGET="stack-ops", FAKE_STATUS="healthy",
        FAKE_CURL_CODE="000", WD_NOW=1000)
    assert "start stack-ops" in calls(tmp) or "restart stack-ops" in calls(tmp)
    assert "port-bindings" in log(repo)


def test_live_ports_do_not_restart_stack_ops(env):
    repo, e, tmp = env
    run(repo, e, FAKE_TARGET="stack-ops", FAKE_STATUS="healthy", WD_NOW=1000)
    # inspect calls legitimately name stack-ops — only start/restart counts
    assert not any(
        c.startswith(("start stack-ops", "restart stack-ops", "update "))
        for c in calls(tmp)
    )


def test_eacces_crashloop_chowns_mount_and_recreates(env, tmp_path):
    """EACCES on a bind mount → chown host dir to container uid + recreate."""
    repo, e, tmp = env
    host_dir = tmp_path / "n8n-data"
    host_dir.mkdir()
    run(repo, e,
        FAKE_TARGET="n8n",
        FAKE_STATUS="restarting false",
        FAKE_LOGS="Error: EACCES: permission denied, open '/home/node/.n8n/config'",
        FAKE_MOUNTS="/home/node/.n8n|" + str(host_dir) + "\n",
        FAKE_UID="1000", FAKE_GID="1000")
    c = calls(tmp)
    assert any("chown" in x for x in c), c
    assert any("compose up -d --no-deps --force-recreate fake-svc" in x for x in c), c
    assert "eacces" in log(repo)


def test_eacces_cap_stops_after_two_attempts(env, tmp_path):
    repo, e, tmp = env
    host_dir = tmp_path / "n8n-data"
    host_dir.mkdir()
    kw = dict(FAKE_TARGET="n8n", FAKE_STATUS="restarting false",
              FAKE_LOGS="EACCES: permission denied, open '/home/node/.n8n/config'",
              FAKE_MOUNTS="/home/node/.n8n|" + str(host_dir) + "\n")
    run(repo, e, **kw)
    run(repo, e, **kw)
    run(repo, e, **kw)  # third run must hit the cap — no third chown
    chowns = [x for x in calls(tmp) if x.startswith("run ") and " chown " in x]
    assert len(chowns) == 2, chowns


def test_dead_published_port_recreates_owner(env, tmp_path):
    """Port probe: dead localhost port on a running container → recreate."""
    repo, e, tmp = env
    run(repo, e, WD_PORT_PROBES="n8n:5678", FAKE_CURL_CODE="000")
    c = calls(tmp)
    assert any("compose up -d --no-deps --force-recreate fake-svc" in x for x in c), c


def test_live_published_port_does_not_recreate(env, tmp_path):
    repo, e, tmp = env
    run(repo, e, WD_PORT_PROBES="n8n:5678", FAKE_CURL_CODE="200")
    c = calls(tmp)
    assert not any("compose up" in x for x in c), c


def test_port_probe_skips_stopped_container(env, tmp_path):
    repo, e, tmp = env
    run(repo, e, WD_PORT_PROBES="n8n:5678", FAKE_CURL_CODE="000", FAKE_CSTATE="exited")
    # exited n8n goes through the stuck-container path instead — but the
    # port probe itself must not fire a second recreate for it
    c = calls(tmp)
    portfix = [x for x in (tmp / "state").glob("portfix-*")]
    assert portfix == [], "portfix marker written for a non-running container"


def test_extra_service_exited_is_restarted(env, tmp_path):
    """n8n is monitored even though it is not in CORE."""
    repo, e, tmp = env
    run(repo, e, FAKE_TARGET="n8n", FAKE_STATUS="exited false")
    c = calls(tmp)
    assert any(x.startswith("start n8n") or x.startswith("restart n8n") for x in c), c


def test_stackops_stopped_container_is_not_restarted(env, tmp_path):
    """A managed container asleep via stack-ops idle-sleep must not be
    woken by the stuck-container loop."""
    repo, e, tmp = env
    so = '{"facebook-browser-sidecar": {"state": "stopped"}}'
    run(repo, e, FAKE_TARGET="facebook-browser-sidecar", FAKE_STATUS="exited false",
        FAKE_STACKOPS_JSON=so)
    c = calls(tmp)
    assert not any("facebook-browser-sidecar" in x and x.split()[0] in ("start", "restart")
                   for x in c), c
    assert "asleep via stack-ops" in log(repo)


def test_stackops_unmanaged_exited_container_still_restarts(env, tmp_path):
    """Same exited state but NOT in the stack-ops stopped set -> restart."""
    repo, e, tmp = env
    so = '{"tiktok-browser-sidecar": {"state": "stopped"}}'
    run(repo, e, FAKE_TARGET="facebook-browser-sidecar", FAKE_STATUS="exited false",
        FAKE_STACKOPS_JSON=so)
    c = calls(tmp)
    assert any(x.startswith("start facebook-browser-sidecar")
               or x.startswith("restart facebook-browser-sidecar") for x in c), c


def test_port_probe_skips_stackops_managed_service(env, tmp_path):
    """A managed service's published port must not be probed — the probe
    itself counts as a wake under stack-ops."""
    repo, e, tmp = env
    so = '{"env-manager-backend": {"state": "running"}}'
    run(repo, e, WD_PORT_PROBES="env-manager-backend:8085", FAKE_CURL_CODE="000",
        FAKE_STACKOPS_JSON=so)
    c = calls(tmp)
    assert not any("compose up" in x for x in c), c
    assert not list((tmp / "state").glob("portfix-env-manager-backend"))


def test_tailnet_ip_drift_updates_env_and_recreates(env, tmp_path):
    """Container stuck Created on 'ports are not available' + tailscale IP
    drift → .env INFISICAL_TAILNET_IP updated + recreate."""
    repo, e, tmp = env
    (repo / ".env").write_text("INFISICAL_TAILNET_IP=100.83.120.93\n")
    err = ("ports are not available: exposing port TCP "
           "100.83.120.93:8090 -> 127.0.0.1:0: /forwards/expose returned 500")
    run(repo, e, FAKE_TARGET="infisical", FAKE_STATUS="created false",
        FAKE_STATE_ERROR=err, FAKE_TS_IP="100.100.186.88")
    assert "INFISICAL_TAILNET_IP=100.100.186.88" in (repo / ".env").read_text()
    c = calls(tmp)
    assert any("compose up -d --no-deps --force-recreate fake-svc" in x for x in c), c
    assert "tailnet-ip" in log(repo)


def test_tailnet_ip_no_drift_no_action(env, tmp_path):
    """Same IP → no .env rewrite, no recreate."""
    repo, e, tmp = env
    (repo / ".env").write_text("INFISICAL_TAILNET_IP=100.83.120.93\n")
    err = "ports are not available: exposing port TCP 100.83.120.93:8090"
    run(repo, e, FAKE_TARGET="infisical", FAKE_STATUS="created false",
        FAKE_STATE_ERROR=err, FAKE_TS_IP="100.83.120.93")
    assert "INFISICAL_TAILNET_IP=100.83.120.93" in (repo / ".env").read_text()
    assert not any("compose up" in x for x in calls(tmp))


def test_tailnet_ip_ignores_non_port_errors(env, tmp_path):
    repo, e, tmp = env
    run(repo, e, FAKE_TARGET="infisical", FAKE_STATUS="created false",
        FAKE_STATE_ERROR="some other error", FAKE_TS_IP="100.100.186.88")
    assert not any("compose up" in x for x in calls(tmp))


def _old_migration_marker(e, tmp_path):
    sd = Path(e["WD_STATE_DIR"])
    sd.mkdir(parents=True, exist_ok=True)
    m = sd / "migration-social-api"
    m.write_text("1")
    old = time.time() - 2000
    os.utime(m, (old, old))
    return m


def test_migration_marker_self_recovers_when_fixed(env, tmp_path):
    """Parked container probe-starts, boots clean -> restart policy restored."""
    repo, e, tmp = env
    m = _old_migration_marker(e, tmp_path)
    cfile = tmp_path / "cstate"
    cfile.write_text("exited")
    run(repo, e, WD_MIGRATION_PROBE_SECS=0, WD_MIGRATION_PROBE_WAIT=0,
        FAKE_CSTATE_FILE=str(cfile), FAKE_STATUS="exited false", FAKE_LOGS="")
    assert not m.exists()
    assert "update --restart=unless-stopped social-api" in calls(tmp)
    assert "migration recovered" in log(repo)


def test_migration_marker_stays_parked_while_broken(env, tmp_path):
    """Probe-start still hits the migration error -> stays parked, throttled."""
    repo, e, tmp = env
    m = _old_migration_marker(e, tmp_path)
    logs = "alembic.util.exc.CommandError: Can't locate revision identified by 'abc123'"
    run(repo, e, WD_MIGRATION_PROBE_SECS=0, WD_MIGRATION_PROBE_WAIT=0,
        FAKE_CSTATE="exited", FAKE_STATUS="exited false", FAKE_LOGS=logs)
    assert m.exists()
    assert "update --restart=unless-stopped" not in "\n".join(calls(tmp))
    assert "staying parked" in log(repo)


def test_migration_marker_running_but_still_erroring_stays_parked(env, tmp_path):
    """Boot succeeds but fresh logs still show the migration error -> parked."""
    repo, e, tmp = env
    m = _old_migration_marker(e, tmp_path)
    cfile = tmp_path / "cstate"
    cfile.write_text("exited")
    logs = "alembic FAILED: Multiple head revisions"
    run(repo, e, WD_MIGRATION_PROBE_SECS=0, WD_MIGRATION_PROBE_WAIT=0,
        FAKE_CSTATE_FILE=str(cfile), FAKE_STATUS="exited false", FAKE_LOGS=logs)
    assert m.exists()
    assert "update --restart=unless-stopped" not in "\n".join(calls(tmp))


def test_migration_marker_young_is_not_probed(env, tmp_path):
    """Fresh marker inside the probe window -> no boot attempt at all."""
    repo, e, tmp = env
    sd = Path(e["WD_STATE_DIR"])
    sd.mkdir(parents=True, exist_ok=True)
    m = sd / "migration-social-api"
    m.write_text("1")
    run(repo, e, FAKE_STATUS="running false")
    assert m.exists()
    assert "probe-starting" not in log(repo)
    assert "start social-api" not in calls(tmp)
