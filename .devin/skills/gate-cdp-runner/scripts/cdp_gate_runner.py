#!/usr/bin/env python3
"""cdp_gate_runner — fires every armed browser-gate script when :9222 is live.

The remaining browser-side human gates (bsky app password, viber webadmin
token, LinkedIn restriction check, Meta payment fill) all need ONE thing:
a debug Chrome on the user's real session. This daemon polls :9222 and the
moment it's up, runs the armed gates in order and reports to Telegram.

Usage:
  cdp_gate_runner.py          # watch loop (checks every 120s, runs once)
  cdp_gate_runner.py --once   # fire gates now if CDP is up
  cdp_gate_runner.py --status # which gates are armed vs done
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path("/home/tbaltzakis/cu130-slim")
STATE = Path("/tmp/cdp_gate_runner_state.json")
CDP = "http://127.0.0.1:9222/json/version"
TG = str(BASE / ".devin/skills/telegram-alert-ops/scripts/tg_alert.py")

GATES = [
    # (name, script, args, description)
    ("li_restriction", BASE / ".devin/skills/linkedin-browser-ops/scripts/li_restriction_check.py", [],
     "read LinkedIn restriction banner"),
    ("bsky_apppw", BASE / ".devin/skills/gate-cdp-runner/scripts/bsky_apppw_gen.py", [],
     "create a Bluesky app password in bsky.app settings"),
    ("viber_webadmin", BASE / ".devin/skills/gate-cdp-runner/scripts/viber_webadmin.py", [],
     "extract Viber bot token from webadmin.viber.com"),
    ("meta_payment_fill", BASE / ".devin/skills/meta-ads-ops/scripts/meta_payment_helper.py", ["fill"],
     "prefill Meta billing fields (confirm stays human)"),
]

def cdp_up():
    try:
        urllib.request.urlopen(CDP, timeout=3)
        return True
    except Exception:
        return False

def tg(msg):
    subprocess.run(["python3", TG, "ops", msg], capture_output=True)

def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}

def run_gate(name, script, args):
    if not Path(script).exists():
        return f"{name}: script missing ({script.name})"
    r = subprocess.run(["python3", str(script), *args], capture_output=True, text=True, timeout=300)
    return (r.stdout + r.stderr).strip()[-300:] or f"{name}: no output"

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "watch"
    state = load()
    if mode == "--status":
        for n, s, _, d in GATES:
            print(f"{n:18} {'done' if state.get(n) else 'armed'}  ({d})")
        return
    for _ in range(720):  # ~24h at 120s
        if not cdp_up():
            if mode == "--once":
                print("cdp: down"); return
            time.sleep(120); continue
        for name, script, args, _ in GATES:
            if state.get(name):
                continue
            print(f"[cdp-gate] {name} …")
            out = run_gate(name, script, args)
            state[name] = out[:200]
            STATE.write_text(json.dumps(state))
            tg(f"🔑 CDP gate `{name}` ran: {out[:120]}")
            print(out[:200])
        if all(state.get(n) for n, *_ in GATES) or mode == "--once":
            return
        time.sleep(120)

if __name__ == "__main__":
    main()
