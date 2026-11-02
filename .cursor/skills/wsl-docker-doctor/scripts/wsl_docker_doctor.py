#!/usr/bin/env python3
"""wsl_docker_doctor.py — diagnose + repair Docker Desktop WSL integration.

    wsl_docker_doctor.py status      # full diagnosis, no changes
    wsl_docker_doctor.py fix         # repair ladder L1-L3 (L4 needs --yes)
    wsl_docker_doctor.py fix --yes   # allow L4 wsl --shutdown (kills sessions)
    wsl_docker_doctor.py alembic     # mounted-vs-DB migration drift check
    wsl_docker_doctor.py containers  # fix hash-prefixed container names

Failure classes this detects (learned 2026-11-02):
- proxy-dead: in-session `docker info` works but fresh
  `wsl.exe --exec docker info` fails — the docker-desktop-user-distro
  proxy injects mounts per-session; when it dies, NEW sessions get no
  docker.sock while old sessions look fine. (The watchdog probes this.)
- vsock-degraded: wsl.exe/powershell.exe calls fail with
  UtilAcceptVsock / WSAETIMEDOUT — flaky both directions, each call a
  coin toss. Durable fix is `wsl --shutdown` (L4).
- alembic-drift: bind-mounted alembic/versions (from a stale checkout)
  lacks migrations the DB is already stamped with -> api crash-loop
  "Can't locate revision <id>".
- name-race: compose recreate leaves <12-hex>_<svc> containers when the
  old name's removal is still in progress.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DISTRO = os.environ.get("WSL_DISTRO", "Ubuntu-26.04")
ENV_FILE = Path(__file__).resolve().parents[4] / ".env"
TG = Path(__file__).resolve().parents[1] / "telegram-alert-ops/scripts/tg_alert.py"


def run(cmd, timeout=40, check_vs=True):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr).strip()
        vs = bool(re.search(r"UtilAcceptVsock|WSAETIMEDOUT", out))
        return r.returncode, out, vs
    except subprocess.TimeoutExpired:
        return 124, "timeout", False
    except FileNotFoundError as e:
        return 127, str(e), False


def alert(text):
    if TG.exists():
        run([sys.executable, str(TG), "ops", text], check_vs=False)


def docker_in_session():
    rc, out, _ = run(["docker", "info", "--format", "{{.Name}}"], timeout=15)
    return rc == 0 and "docker-desktop" in out


def docker_fresh_exec():
    """The watchdog's probe — a NEW session via wsl.exe --exec."""
    rc, out, vs = run(["wsl.exe", "-d", DISTRO, "--exec", "docker", "info",
                       "--format", "{{.Name}}|{{.OperatingSystem}}"], timeout=20)
    return rc == 0 and "Docker Desktop" in out, out, vs


def proxy_alive():
    rc, out, _ = run(["pgrep", "-af", "docker-desktop-user-distro"], timeout=10)
    return rc == 0 and "proxy" in out, out


def interop_ok():
    rc, _, vs = run(["powershell.exe", "-NoProfile", "-Command", "echo ok"], timeout=15)
    return rc == 0, vs


def status():
    ins = docker_in_session()
    fresh, fout, fvs = docker_fresh_exec()
    proxy, pinfo = proxy_alive()
    inter, ivs = interop_ok()
    print(f"in-session docker info      : {'OK' if ins else 'FAIL'}")
    print(f"fresh wsl --exec docker info: {'OK' if fresh else 'FAIL'} {fout[:80]}")
    print(f"user-distro proxy           : {'alive' if proxy else 'DEAD'} {pinfo.splitlines()[-1][:80] if pinfo else ''}")
    print(f"win interop (powershell.exe): {'OK' if inter else 'FAIL'}")
    vs = fvs or ivs
    print(f"vsock errors observed       : {'YES — degraded' if vs else 'no'}")
    state = Path("/mnt/c/Users/tbaltzakis/AppData/Local/DockerWslIntegrationWatchdog/last-state.txt")
    if state.exists():
        print(f"watchdog last-state         : {state.read_text().strip()}")
    # classify
    if ins and fresh:
        print("\nDIAGNOSIS: healthy")
    elif ins and not fresh and fvs:
        print("\nDIAGNOSIS: vsock-degraded — fresh exec fails on vsock, not the proxy "
              "(alive). Coin-toss; retry or `fix` (L2/L4).")
    elif ins and not fresh:
        print("\nDIAGNOSIS: proxy-dead — new sessions get no docker.sock (old sessions look fine). "
              "Run `fix` (L1 respawn proxy / L2 terminate).")
    elif not ins and fresh:
        print("\nDIAGNOSIS: stale in-session socket only — relaunch your shell.")
    else:
        print(f"\nDIAGNOSIS: {'vsock-degraded (coin-toss calls)' if vs else 'integration fully down'} "
              "— run `fix`")
    return 0 if (ins and fresh) else 1


def fix(allow_shutdown):
    if docker_fresh_exec()[0]:
        print("fresh-exec probe already healthy — nothing to do")
        return 0
    # L1: respawn the user-distro proxy if binary exists but process is dead
    ok, _ = proxy_alive()
    if not ok:
        rc, out, _ = run(["ls", "/run/docker-desktop/docker-desktop-user-distro"], timeout=10)
        if rc == 0:
            run(["sh", "-c", "sudo -n nohup /run/docker-desktop/docker-desktop-user-distro proxy "
                 f"--distro-name {DISTRO} --docker-desktop-root /mnt/wsl/docker-desktop "
                 "'C:\\Program Files\\Docker\\Docker\\resources' >/tmp/dd-proxy.log 2>&1 &"],
                timeout=10)
            time.sleep(8)
            if docker_fresh_exec()[0]:
                print("L1 respawned user-distro proxy — healthy")
                return 0
    # L2: terminate + relaunch distro (the watchdog's repair)
    alert(f"wsl-docker-doctor: terminating {DISTRO} to restore docker-desktop integration")
    rc, out, _ = run(["wsl.exe", "--terminate", DISTRO], timeout=60)
    print(f"wsl --terminate rc={rc} {out[:100]}")
    for _ in range(18):
        time.sleep(5)
        if docker_fresh_exec()[0]:
            alert(f"wsl-docker-doctor: {DISTRO} integration restored")
            print("L2 terminate+relaunch restored integration")
            return 0
    # L3: docker desktop restart (backend/proxy death)
    print("L2 exhausted — L3 docker desktop restart")
    alert("wsl-docker-doctor: restarting Docker Desktop (backend/proxy death)")
    run(["docker.exe", "desktop", "restart"], timeout=180)
    time.sleep(30)
    if docker_fresh_exec()[0]:
        print("L3 restored integration")
        return 0
    # L4: wsl --shutdown — kills ALL WSL sessions
    if not allow_shutdown:
        print("L4 available: `wsl --shutdown` (kills all WSL sessions + docker-desktop VM). "
              "Re-run `fix --yes` to allow, or schedule it manually.")
        return 2
    alert("wsl-docker-doctor: wsl --shutdown (all WSL sessions die)")
    run(["wsl.exe", "--shutdown"], timeout=60)
    time.sleep(30)
    print("healthy" if docker_fresh_exec()[0] else "still down after shutdown — needs a manual look")
    return 0


def alembic():
    # DB stamp vs bind-mounted versions dir drift (the 2026-11-02 crash class)
    rc, img, _ = run(["docker", "inspect", "social-api", "--format",
                      "{{range .Mounts}}{{println .Source .Destination}}{{end}}"], timeout=15)
    vers = None
    for ln in img.splitlines():
        if ln.strip().endswith("/app/alembic"):
            vers = Path(ln.split()[0]) / "versions"
    if not vers or not vers.exists():
        print("no /app/alembic bind mount found — check docker inspect social-api")
        return 1
    rc, out, _ = run(["docker", "exec", "social-postgres", "psql", "-U", "social_user",
                      "-d", "social_automation", "-tc", "select version_num from alembic_version"])
    db_rev = out.strip()
    have = {p.name.split("_")[0] for p in vers.glob("*.py")}
    missing = sorted(r for r in [db_rev] if r and r.split("_")[0] not in have)
    print(f"db stamp: {db_rev} | mounted dir: {vers}")
    print("MISSING migration files (copy from a master checkout):" if missing else "no drift")
    for m in missing:
        print("  ", m)
    return 1 if missing else 0


def containers():
    rc, out, _ = run(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=15)
    bad = [n for n in out.splitlines() if re.match(r"^[0-9a-f]{12}_", n)]
    for n in bad:
        canon = n.split("_", 1)[1]
        r = run(["docker", "rename", n, canon], timeout=10)
        print(f"{'renamed' if r[0] == 0 else 'FAILED'} {n} -> {canon}")
    print(f"{len(bad)} hash-prefixed containers") if not bad else None
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    yes = "--yes" in sys.argv
    sys.exit({"status": status, "fix": lambda: fix(yes), "alembic": alembic,
              "containers": containers}.get(cmd, lambda: print(__doc__) or 2)())
