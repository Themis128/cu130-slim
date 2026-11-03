#!/usr/bin/env python3
"""espo_check.py — EspoCRM daemon/state diagnostic on omv k3s.

The v10 failure class: the espocrm-daemon sidecar runs docker-daemon.sh →
daemon.php → calls config:set — but a stale PVC holds 35-line stub files
that don't have v10's CLI. Detects the stub + reports the reseed path."""
import json
import subprocess
import sys


def sh(cmd):
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "github-omv", cmd],
                       capture_output=True, text=True, timeout=45)
    return r.stdout.strip()


def main() -> int:
    pod = sh("sudo k3s kubectl get pods -n espocrm -l app=espocrm -o jsonpath='{.items[0].metadata.name}'")
    out = {"pod": pod}
    statuses = sh(f"sudo k3s kubectl get pod -n espocrm {pod} -o jsonpath='{{.status.containerStatuses[*]}}'")
    out["containers"] = statuses[:400]
    # stub check — real v10 files are thousands of lines
    sizes = sh(f"sudo k3s kubectl exec -n espocrm {pod} -c espocrm -- wc -l /var/www/html/daemon.php /var/www/html/command.php 2>/dev/null")
    out["stub_check"] = sizes
    stub = any(int(l.split()[0]) < 100 for l in sizes.splitlines() if l.split() and l.split()[0].isdigit())  # noqa: E741
    out["stale_volume"] = stub
    out["verdict"] = "stale PVC app-files — reseed the volume" if stub else "volume files look current"
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
