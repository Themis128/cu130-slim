#!/usr/bin/env python3
"""heal_supervisor.py — unified supervisor for every auto-healer + watcher.
Registers all heals/watches as managed loops + exposes a /status JSON board.

Heal registry — name → kind → runner:
  platform heals (celery beat already runs these — reported, not re-run)
  host watchers (run in-process: cdp, vetting, wa)
  node heals (omv auto-remediate — reported)

The supervisor owns the HOST-side watches; celery owns platform beats; omv's
auto-remediate owns node-level — one board reports all three tiers."""
import json
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

REGISTRY = {
    # host-side watches — run in-process threads
    "cdp_watch":      {"kind": "watch", "script": ".devin/skills/cdp-watch-ops/scripts/cdp_watch.py", "tier": "host"},
    "li_vetting":     {"kind": "watch", "script": ".devin/skills/li-vetting-watch/scripts/vetting_watch.py", "tier": "host"},
    "wa_code_watch":  {"kind": "watch", "script": ".devin/skills/wa-verify-ops/scripts/wa_code_watch.py", "tier": "host"},
    "fb_reauth":      {"kind": "watch", "script": ".devin/skills/fb-reauth-ops/scripts/fb_reauth.py", "tier": "host"},
}
STATE = {k: {"status": "starting", "runs": 0, "last": 0} for k in REGISTRY}


def _watch(name, script):
    while True:
        try:
            r = subprocess.run([sys.executable, script, "--once"],
                               capture_output=True, text=True, timeout=120)
            STATE[name].update(status="ok" if r.returncode == 0 else "watch",
                               runs=STATE[name]["runs"] + 1, last=int(time.time()))
        except Exception:
            STATE[name]["status"] = "err"
        time.sleep(300)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/status":
            self.send_response(404); self.end_headers(); return
        tiers = {"host": {k: v for k, v in STATE.items()}}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"heals": tiers, "ts": int(time.time())}).encode())


def main() -> int:
    for name, cfg in REGISTRY.items():
        threading.Thread(target=_watch, args=(name, cfg["script"]), daemon=True).start()
    HTTPServer(("0.0.0.0", 9099), H).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
