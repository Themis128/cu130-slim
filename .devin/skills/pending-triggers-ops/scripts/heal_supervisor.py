#!/usr/bin/env python3
"""heal_supervisor.py — one service for every auto-healer + watcher.

Design: each registered watch runs as a persistent subprocess with
restart-on-crash (backoff), not a spawn-per-cycle — the watches are daemons
themselves (they loop internally). /status reports all three tiers:

  host   — this process's watchers (cdp, vetting, wa, fb)
  beat   — celery-beat platform heals (polled from the API — reported, not run)
  node   — omv auto-remediate (last-run state via ssh — reported, not run)

One board for the whole self-healing surface."""
import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST_WATCHES = {
    "cdp_watch": "/app/.devin/skills/cdp-watch-ops/scripts/cdp_watch.py",
    "li_vetting": "/app/.devin/skills/li-vetting-watch/scripts/vetting_watch.py",
    "wa_code_watch": "/app/.devin/skills/wa-verify-ops/scripts/wa_code_watch.py",
    "fb_reauth": "/app/.devin/skills/fb-reauth-ops/scripts/fb_reauth.py",
}
BEAT_HEALS = ["heal_consents", "dmr_health", "engagement_ingest",
              "log_intel", "media_backfill"]
STATE = {k: {"status": "starting", "restarts": 0, "last": 0}
         for k in HOST_WATCHES}
BEAT_STATE: dict = {}
NODE_STATE: dict = {}


def _spawn(name, script):
    """Persistent subprocess + restart-on-crash with backoff."""
    backoff = 5
    while True:
        STATE[name]["status"] = "running"
        STATE[name]["last"] = int(time.time())
        try:
            p = subprocess.Popen([sys.executable, script])
            p.wait()
            STATE[name]["status"] = f"exited({p.returncode})"
            STATE[name]["restarts"] += 1
        except Exception:
            STATE[name]["status"] = "spawn-fail"
        time.sleep(backoff)
        backoff = min(backoff * 2, 300)


def _beat_tier():
    """Report the celery-beat heals' last-run state via the API."""
    while True:
        try:
            r = urllib.request.urlopen("http://localhost:8083/health", timeout=6)
            BEAT_STATE["api"] = "up" if r.status == 200 else f"{r.status}"
        except Exception:
            BEAT_STATE["api"] = "down"
        for h in BEAT_HEALS:
            BEAT_STATE.setdefault(h, "scheduled")
        time.sleep(120)


def _node_tier():
    """Report omv auto-remediate + every cloudflared tunnel; restarts a down tunnel's daemon."""
    while True:
        try:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "github-omv",
                                "cat /var/lib/nas-auto-remediate/state.json 2>/dev/null | head -c 400;"
                                "sudo cloudflared tunnel list 2>/dev/null"],
                               capture_output=True, text=True, timeout=30)
            NODE_STATE["omv"] = "reachable" if "state.json" not in r.stderr else "degraded"
            for line in r.stdout.splitlines():
                if "cloudless" in line:
                    name = line.split()[1]
                    conns = line.rsplit(None, 1)[-1] if line.split() else ""
                    up = "x" in conns and not conns.startswith("0")
                    NODE_STATE[f"tunnel-{name}"] = "up" if up else "down"
                    if not up:
                        svc = {"cloudless": "cloudflared", "omv-cloudless": "cloudflared-omv",
                               "social-cloudless": "cloudflared"}.get(name, "cloudflared")
                        subprocess.run(["ssh", "-o", "ConnectTimeout=10", "github-omv",
                                        f"sudo systemctl restart {svc}"],
                                       capture_output=True, timeout=30)
                        NODE_STATE[f"tunnel-{name}"] = "restarting"
        except Exception:
            NODE_STATE["omv"] = "unreachable"
        time.sleep(120)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/status":
            self.send_response(404)
            self.end_headers()
            return
        out = {
            "host": STATE,
            "beat": BEAT_STATE,
            "node": NODE_STATE,
            "ts": int(time.time()),
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())


def main() -> int:
    for name, script in HOST_WATCHES.items():
        threading.Thread(target=_spawn, args=(name, script), daemon=True).start()
    threading.Thread(target=_beat_tier, daemon=True).start()
    threading.Thread(target=_node_tier, daemon=True).start()

    HTTPServer(("0.0.0.0", 9099), H).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
