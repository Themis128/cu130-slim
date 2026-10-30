#!/usr/bin/env python3
"""omv_ha_migrate.py — offload light services omv-main → omv-ha via Tailscale.

Prefers `tailscale ssh` (no keys needed if enabled on the node); falls back to
keyed ssh tbaltzakis@100.95.117.84."""
import json
import subprocess
import sys

OMV_MAIN = "github-omv"
OMV_HA_TS = "100.95.117.84"
OMV_HA_USER = "tbaltzakis"
MOVABLE = ["searxng", "tempo", "tempo-proxy"]
DEAD = ["cloudflared-omv"]  # omv-cloudless tunnel — zero connectors, dropped


def run(host_cmd, timeout=45):
    r = subprocess.run(host_cmd, capture_output=True, text=True, timeout=timeout)
    return (r.returncode, r.stdout.strip() + r.stderr.strip())


def ha_ssh(cmd, timeout=60):
    """tailscale ssh via omv-main (the tailnet node); direct ssh fallback."""
    q = cmd.replace("'", "'\\''")
    rc, out = run(["ssh", "-o", "ConnectTimeout=12", OMV_MAIN,
                   f"tailscale ssh {OMV_HA_USER}@{OMV_HA_TS} '{q}'"], timeout)
    if rc != 0:
        rc, out = run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                       f"{OMV_HA_USER}@{OMV_HA_TS}", cmd], timeout)
    return rc, out


def main_ssh(cmd, timeout=45):
    return run(["ssh", "-o", "ConnectTimeout=10", OMV_MAIN, cmd], timeout)


def status():
    out = {"omv_ha_tailscale": "unreachable", "movable": {}, "dead": {}}
    rc, _ = ha_ssh("echo ok")
    out["omv_ha_tailscale"] = "ok" if rc == 0 else "unreachable"
    _, mem = main_ssh("free -h | head -2")
    out["omv_main_mem"] = mem.splitlines()[-1] if mem else "?"
    _, mem2 = ha_ssh("free -h | head -2") if rc == 0 else (1, "?")
    out["omv_ha_mem"] = mem2.splitlines()[-1] if mem2 and rc == 0 else "?"
    for s in MOVABLE + DEAD:
        _, st = main_ssh(f"docker inspect -f '{{{{.State.Status}}}}' {s} 2>/dev/null")
        out["dead" if s in DEAD else "movable"][s] = st or "?"
    print(json.dumps(out, indent=2))


def migrate():
    for svc in MOVABLE:
        rc, out = main_ssh(
            f"docker inspect -f '{{{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}}}' {svc}")
        if rc != 0 or not out:
            print(f"{svc}: no compose dir"); continue
        d = out.splitlines()[-1]
        print(f"{svc}: exporting {d}")
        main_ssh(f"sudo tar czf /tmp/{svc}.tgz -C {d} .")
        main_ssh(f"scp -o ConnectTimeout=15 /tmp/{svc}.tgz {OMV_HA_USER}@{OMV_HA_TS}:/tmp/")
        ha_ssh(f"mkdir -p ~/compose/{svc} && tar xzf /tmp/{svc}.tgz -C ~/compose/{svc}")
        ha_ssh(f"cd ~/compose/{svc} && sudo docker compose up -d")
        main_ssh(f"sudo docker compose -f {d}/docker-compose.yml down" if d else "true")
        print(f"{svc}: migrated")


def drop_dead():
    for s in DEAD:
        rc, _ = main_ssh(f"sudo docker rm -f {s}")
        print(f"{s}: {'removed' if rc == 0 else 'fail'}")


if __name__ == "__main__":
    {"status": status, "migrate": migrate, "drop-dead": drop_dead}[
        sys.argv[1] if len(sys.argv) > 1 else "status"]()
