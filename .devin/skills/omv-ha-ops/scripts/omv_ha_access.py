#!/usr/bin/env python3
"""omv_ha_access.py — Tailscale access bootstrap + connectivity check for omv-ha.

omv-ha IS on the tailnet (100.95.117.84, verified ping-clean) — the blocker
is SSH access: no matching local key + no Tailscale-SSH enabled check.
This tool: checks tailnet+port-22 reachability, probes `tailscale ssh`
(via omv-main relay), and bootstraps a key onto omv-ha when reachable."""
import json
import subprocess
import sys

OMV_MAIN = "github-omv"
OMV_HA = "100.95.117.84"
OMV_HA_USER = "tbaltzakis"


def sh(cmd, timeout=30):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def check():
    out = {}
    rc, ping = sh(["ping", "-c2", "-W4", OMV_HA])
    out["tailnet_ping"] = "ok" if rc == 0 else "fail"
    # port-22 probe — Tailscale IP direct
    rc, _ = sh(["bash", "-c", f"timeout 5 bash -c '</dev/tcp/{OMV_HA}/22' && echo open || echo closed"])
    out["ssh_port22"] = "open" if "open" in _ else "closed/filtered"
    # tailscale ssh via omv-main relay
    rc, ts = sh(["ssh", "-o", "ConnectTimeout=12", OMV_MAIN,
                 f"timeout 10 tailscale ssh {OMV_HA_USER}@{OMV_HA} 'echo tssh-ok'"])
    out["tailscale_ssh"] = "ok" if "tssh-ok" in ts else ("relay-down" if "timed out" in ts else "not-enabled")
    # keyed ssh direct
    rc, ks = sh(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
                 "-o", "StrictHostKeyChecking=accept-new",
                 f"{OMV_HA_USER}@{OMV_HA}", "echo key-ok"])
    out["keyed_ssh"] = "ok" if "key-ok" in ks else "denied"
    print(json.dumps(out, indent=2))
    return 0


def bootstrap_key():
    """Push the local pubkey to omv-ha's authorized_keys via tailscale ssh."""
    pub = None
    for k in ["id_ed25519_win.pub", "id_rsa_win.pub"]:
        try:
            pub = open(f"/home/tbaltzakis/.ssh/{k}").read().strip()
            break
        except FileNotFoundError:
            continue
    if not pub:
        print("no local pubkey found"); return 1
    cmd = (f"mkdir -p ~/.ssh && echo '{pub}' >> ~/.ssh/authorized_keys "
           f"&& chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && echo key-added")
    rc, out = sh(["ssh", "-o", "ConnectTimeout=12", OMV_MAIN,
                  f"tailscale ssh {OMV_HA_USER}@{OMV_HA} \"{cmd}\""])
    print(out or "key pushed" if rc == 0 else f"push failed: {out[:200]}")
    return rc


if __name__ == "__main__":
    {"check": check, "bootstrap-key": bootstrap_key}[
        sys.argv[1] if len(sys.argv) > 1 else "check"]()
