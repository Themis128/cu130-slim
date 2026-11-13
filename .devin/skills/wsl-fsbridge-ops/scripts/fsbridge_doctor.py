#!/usr/bin/env python3
"""fsbridge_doctor.py — diagnose the dd-user-distro-proxy fs-bridge class.

Run inside Ubuntu-24.04: python3 fsbridge_doctor.py [status|probe|casualties]

Failure class (2026-11-13): the distro proxy dies on a vsock flap; from
then on NEW bind-mounts sourced from \\\\wsl.localhost\\<distro>\\...
materialize inside containers as empty dirs. Existing mounts for running
containers keep working until the container restarts. Durable heal is a
full `wsl --shutdown` (scheduled on this host); proxy respawn + container
force-recreate is the interim reprieve.
"""
import os
import subprocess
import sys

BIND_ROOT = "/mnt/wsl/docker-desktop-bind-mounts"


def sh(argv, timeout=15):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return -1, str(e)


def proxy_status():
    rc, out = sh(["pgrep", "-af", "user-distro-proxy"])
    procs = [l for l in out.splitlines() if "pgrep" not in l and l.strip()]
    return procs


def hashed_mounts(distro):
    root = os.path.join(BIND_ROOT, distro)
    if not os.path.isdir(root):
        return 0, 0, []
    entries = sorted(os.listdir(root))
    empty = []
    for h in entries:
        d = os.path.join(root, h)
        try:
            if os.path.isdir(d) and not os.listdir(d):
                empty.append(h)
        except OSError:
            empty.append(h + " (unreadable)")
    return len(entries), len(empty), empty


def containers():
    rc, out = sh(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.ID}}"], 20)
    rows = []
    if rc != 0:
        return rows
    for line in out.splitlines():
        if "|" in line:
            n, s, i = line.split("|", 2)
            rows.append((n.strip(), s.strip(), i.strip()))
    return rows


def mount_sources(cid):
    rc, out = sh(["docker", "inspect", cid, "--format",
                  "{{range .Mounts}}{{.Source}}|{{.Destination}}{{println}}{{end}}"], 20)
    return [l for l in out.splitlines() if l.strip()] if rc == 0 else []


def status():
    procs = proxy_status()
    if procs:
        print(f"proxy: ALIVE ({len(procs)} proc)")
        for p in procs:
            print(f"  {p[:140]}")
    else:
        print("proxy: DEAD - fs bridge down; new wsl.localhost mounts = empty dirs")
        print("  heal: full `wsl --shutdown` (scheduled); interim: DD restart can")

    rc, out = sh(["test", "-S", "/var/run/docker.sock"])
    print(f"docker.sock: {'present' if rc == 0 else 'MISSING'}")

    for d in (os.listdir(BIND_ROOT) if os.path.isdir(BIND_ROOT) else []):
        total, empty, _ = hashed_mounts(d)
        print(f"hashed bind-mounts[{d}]: {total} total, {empty} EMPTY/unreadable")

    bad = casualties(quiet=True)
    if bad:
        print(f"casualties: {len(bad)} restart-looping container(s) on dead mounts")
        for n, s in bad:
            print(f"  {n}: {s}")

    print("DIAGNOSIS:",
          "FS-BRIDGE DEAD - heal via wsl --shutdown; "
          "recreate casualties after" if not procs else
          "fs bridge nominal")


def probe():
    """Definitive test: fresh \\\\wsl.localhost mount into a throwaway container."""
    target = sys.argv[2] if len(sys.argv) > 2 else "/home/tbaltzakis/.bashrc"
    win_src = f"//wsl.localhost/Ubuntu-24.04{target}"
    print(f"probe-mount: {win_src}")
    rc, out = sh(["docker", "run", "--rm", "-v", f"{win_src}:/tmp/probe:ro",
                  "alpine", "sh", "-c", "ls -la /tmp/probe; head -3 /tmp/probe"], 90)
    print(out.strip()[:2000])
    print("DIAGNOSIS:", "mount OK" if rc == 0 else "MOUNT POISONED (rc=%s)" % rc)


def casualties(quiet=False):
    bad = []
    for name, st, cid in containers():
        restarting = "Restarting" in st or "Exited" in st
        if not restarting:
            continue
        for m in mount_sources(cid):
            if "docker-desktop-bind-mounts" in m or "wsl.localhost" in m:
                bad.append((name, st))
                break
    if not quiet:
        if bad:
            for n, s in bad:
                print(f"{n}: {s}")
            print("-- after heal: docker compose up -d --force-recreate <name>")
        else:
            print("no restart-looping containers on hashed/UNC mounts")
    return bad


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"status": status, "probe": probe, "casualties": casualties}.get(cmd, status)()
