#!/usr/bin/env python3
"""healthcheck_loop_audit.py — detect the 'start_period < boot time'
autoheal restart-amplification loop across compose services.

Failure class (2026-11-13): celery workers boot ~90-120s but had
start_period=30s + 3x30s retries -> marked unhealthy at ~120s exactly
when ready -> autoheal killed them -> in-flight publish tasks died
mid-flight every few minutes.

For every running container with a healthcheck, this compares:
  grace  = StartPeriod + interval*retries   (unhealthy verdict delay)
  boot   = time from container start to first healthy check
and flags services where unhealthy verdicts fired inside the boot
window (grace too short) or where autoheal restarted them repeatedly.

    healthcheck_loop_audit.py [--json] [--fix-hint]

Read-only. Run on host or inside WSL with docker access.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    as_json = "--json" in sys.argv
    ids = sh("docker", "ps", "-q").split()
    rows = []
    for cid in ids:
        fmt = ("{{.Name}}|{{.State.StartedAt}}|"
               "{{json .Config.Healthcheck}}|{{json .State.Health}}|"
               "{{.RestartCount}}|{{index .Config.Labels \"autoheal\"}}")
        line = sh("docker", "inspect", "-f", fmt, cid).strip()
        if "|" not in line:
            continue
        name, started, hc, health, restarts, autoheal = line.split("|", 5)
        if hc in ("", "null"):
            continue
        try:
            hc = json.loads(hc)
            health = json.loads(health)
        except Exception:
            continue
        interval = hc.get("Interval", 0) / 1e9
        timeout = hc.get("Timeout", 0) / 1e9
        retries = hc.get("Retries", 3)
        sp = hc.get("StartPeriod", 0) / 1e9
        grace = sp + interval * retries
        log = health.get("Log", [])
        first_ok = next((l for l in log if l.get("ExitCode") == 0), None)
        boot_s = None
        if first_ok and started:
            try:
                boot_s = (ts(first_ok["Start"]) - ts(started)).total_seconds()
            except Exception:
                pass
        # Health.Log only retains recent checks, so "first ok" is not a
        # reliable cold-boot measure. Risk = the unhealthy verdict can
        # fire before a slow-booting app is ready: grace < 120s on an
        # autoheal-managed service, or observed unhealthy/restarts.
        risk = ((autoheal == "true" and grace < 120)
                or health.get("Status") == "unhealthy"
                or int(restarts) > 3)
        rows.append({
            "name": name.lstrip("/"), "autoheal": autoheal == "true",
            "grace_s": round(grace), "boot_s": round(boot_s) if boot_s else None,
            "status": health.get("Status"), "restarts": restarts,
            "risk": bool(risk),
        })
    risky = [r for r in rows if r["risk"] or int(r["restarts"]) > 3]
    if as_json:
        print(json.dumps({"services": rows, "at_risk": risky}, indent=2))
    else:
        print(f"{'service':38} {'grace':>6} {'boot':>6} {'status':>10} "
              f"{'rst':>4} {'autoheal':>8} flag")
        for r in sorted(rows, key=lambda x: x["name"]):
            flag = "RISK" if r in risky else ""
            print(f"{r['name']:38} {r['grace_s']:>6} "
                  f"{str(r['boot_s']):>6} {r['status']:>10} "
                  f"{r['restarts']:>4} {str(r['autoheal']):>8} {flag}")
        if risky:
            print("\nRISK = boot time approaches/exceeds the unhealthy "
                  "verdict delay -> raise healthcheck start_period "
                  "(see PR #1830 pattern) or autoheal will kill the "
                  "container right as it becomes ready.")
    return 1 if risky else 0


if __name__ == "__main__":
    sys.exit(main())
