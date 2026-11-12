#!/usr/bin/env python3
"""docker_engine_guard.py — verify the Docker daemon topology on this host.

The stack lives in the Docker Desktop (docker-desktop) engine. Three ways
`docker` can silently hit the WRONG engine or nothing at all:

  1. native-docker-active — WSL docker.service/docker.socket steal
     /run/docker.sock via socket activation and answer `docker info`
     with an empty engine (seen 2026-11-12: recovery lanes pulled the
     whole image set into a daemon that hosts nothing).
  2. proxy-dead — dd-user-distro-proxy.service down: bare `docker` gets
     "cannot connect" even though docker.exe works.
  3. wrong-engine — `docker info` answers but Name != docker-desktop
     (e.g. a stale socket, a different context).

Checks (all read-only):
  - docker info via bare `docker` → must report docker-desktop
  - docker info via docker.exe → engine reachable
  - systemctl is-enabled/is-active docker.service docker.socket → disabled
  - systemctl is-active dd-user-distro-proxy.service → active
  - guest-services distro-services socket exists

Usage:
  docker_engine_guard.py            # report, exit 1 on any FAIL
  docker_engine_guard.py --alert    # TG #ops alert on findings (6h dedupe)
  docker_engine_guard.py --json     # machine-readable
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
STATE = HOME / ".local/state/docker-engine-guard/state.json"
LOG = HOME / "logs/docker-engine-guard.log"
TG = "/home/tbaltzakis/cu130-slim/.devin/skills/telegram-alert-ops/scripts/tg_alert.py"
DOCKER_EXE = "/mnt/c/Users/tbaltzakis/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"
DISTRO_SOCK = "/mnt/wsl/docker-desktop/shared-sockets/guest-services/distro-services/ubuntu-24-04.sock"
DEDUPE_S = 6 * 3600


def sh(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")


def docker_name(binary):
    rc, out = sh([binary, "info", "--format", "{{.Name}}"], timeout=15)
    return out.splitlines()[0].strip() if rc == 0 and out else ""


def systemd_unit_state(unit):
    rc, out = sh(["systemctl", "is-active", unit], timeout=10)
    active = out.strip()
    rc2, out2 = sh(["systemctl", "is-enabled", unit], timeout=10)
    return active, out2.strip()


def check():
    findings = []

    name_bare = docker_name("docker")
    if name_bare == "docker-desktop":
        findings.append(("ok", "bare-docker", "docker -> docker-desktop"))
    elif name_bare:
        findings.append(("FAIL", "wrong-engine",
                         f"bare docker answers but engine is '{name_bare}', not docker-desktop"))
    else:
        findings.append(("FAIL", "proxy-dead",
                         "bare `docker info` unreachable — check dd-user-distro-proxy.service"))

    name_exe = docker_name(DOCKER_EXE) if Path(DOCKER_EXE).exists() else ""
    if name_exe == "docker-desktop":
        findings.append(("ok", "docker-exe", "docker.exe -> docker-desktop"))
    else:
        findings.append(("FAIL", "engine-down",
                         "docker.exe cannot reach the Docker Desktop engine"))

    for unit in ("docker.service", "docker.socket"):
        active, enabled = systemd_unit_state(unit)
        if enabled in ("disabled", "masked") and active != "active":
            findings.append(("ok", f"{unit}-disabled", f"{unit} {enabled}/{active}"))
        else:
            findings.append(("FAIL", "native-docker-active",
                             f"{unit} is {enabled}/{active} — it can steal /run/docker.sock "
                             f"(fix: systemctl disable --now {unit})"))

    pactive, _ = systemd_unit_state("dd-user-distro-proxy.service")
    if pactive == "active":
        findings.append(("ok", "proxy-unit", "dd-user-distro-proxy active"))
    else:
        findings.append(("FAIL", "proxy-unit-down",
                         f"dd-user-distro-proxy.service is {pactive} — bare docker stays dead"))

    if Path(DISTRO_SOCK).exists():
        findings.append(("ok", "distro-services", "ubuntu-24-04.sock present"))
    else:
        findings.append(("WARN", "distro-services-missing",
                         "guest-services distro socket absent — bind-mount containers "
                         "will exit 127 until Docker Desktop re-registers"))
    return findings


def alert(findings):
    bad = [f for f in findings if f[0] == "FAIL"]
    if not bad:
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    last = 0
    if STATE.exists():
        try:
            last = json.loads(STATE.read_text()).get("last_alert", 0)
        except Exception:
            pass
    if time.time() - last < DEDUPE_S:
        return
    text = "docker-engine-guard: " + "; ".join(f"{k}: {d}" for _, k, d in bad)
    log(f"ALERT {text}")
    try:
        subprocess.run(["python3", TG, "ops", text], timeout=30,
                       capture_output=True)
    except Exception:
        pass
    STATE.write_text(json.dumps({"last_alert": time.time()}))


def main():
    findings = check()
    fails = [f for f in findings if f[0] == "FAIL"]
    warns = [f for f in findings if f[0] == "WARN"]
    for sev, key, detail in findings:
        print(f"{sev:4} {key:28} {detail}")
    if "--json" in sys.argv:
        print(json.dumps([{"sev": s, "key": k, "detail": d} for s, k, d in findings]))
    if "--alert" in sys.argv:
        alert(findings)
    log(f"run: {len(fails)} FAIL {len(warns)} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
