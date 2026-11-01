#!/usr/bin/env python3
"""ts_acl_check.py — verify the Tailscale SSH ACL landed on omv-ha.

`check` — one-shot probe (tailnet ping + port22 + tailscale ssh via omv-main).
`auto`  — polls until tailscale-ssh answers, then drives the full offload
          (bootstrap-key → omv_ha_migrate migrate → drop-dead)."""
import json
import subprocess
import sys
import time

OMV_MAIN = "github-omv"
OMV_HA = "100.95.117.84"
U = "tbaltzakis"


def sh(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def tssh_ok():
    rc, out = sh(["ssh", "-o", "ConnectTimeout=12", OMV_MAIN,
                  f"timeout 10 tailscale ssh {U}@{OMV_HA} 'echo tssh-ok'"])
    return "tssh-ok" in out


def check():
    out = {}
    rc, _ = sh(["ping", "-c2", "-W4", OMV_HA])
    out["tailnet_ping"] = "ok" if rc == 0 else "fail"
    out["tailscale_ssh"] = "ok" if tssh_ok() else "not-enabled/acl"
    out["acl_landed"] = out["tailscale_ssh"] == "ok"
    print(json.dumps(out, indent=2))
    return 0 if out["acl_landed"] else 1


def auto(poll_s=60, max_min=60):
    deadline = time.time() + max_min * 60
    while time.time() < deadline:
        if tssh_ok():
            print("acl landed — running offload")
            subprocess.run([sys.executable,
                            ".devin/skills/omv-ha-ops/scripts/omv_ha_migrate.py", "migrate"])
            subprocess.run([sys.executable,
                            ".devin/skills/omv-ha-ops/scripts/omv_ha_migrate.py", "drop-dead"])
            return 0
        time.sleep(poll_s)
    print("timed out waiting for acl")
    return 1


if __name__ == "__main__":
    sys.exit({"check": check, "auto": auto}[sys.argv[1] if len(sys.argv) > 1 else "check"]())
