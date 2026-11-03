#!/usr/bin/env python3
"""cdp_gate_runner — one watcher that syncs every human gate front.

When the debug Chrome (:9222) comes up, fires each armed browser gate
ONCE, drops creds to the human-gate-capture watcher, and reports to
Telegram. Gate arm-state derives from the REAL pending state (watcher
files / account status), not just a local flag — so a gate that's
already wired via another path isn't re-fired.

Usage:
  cdp_gate_runner.py          # watch loop (~24h)
  cdp_gate_runner.py --once   # fire armed gates now if CDP is up
  cdp_gate_runner.py --status # every front's real state
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path("/home/tbaltzakis/cu130-slim")
SK = BASE / ".devin/skills"
CDP_VER = "http://127.0.0.1:9222/json/version"
STATE = Path("/tmp/cdp_gate_runner_state.json")
TG = SK / "telegram-alert-ops/scripts/tg_alert.py"
ENSURE = SK / "consent-root-ops/scripts/cdp_ensure.py"
PHONE = SK / "phone-automation-ops/scripts/phone_ctl.py"
DROPS = Path("/mnt/c/Users/tbaltzakis/Downloads")

def cdp_up():
    try:
        urllib.request.urlopen(CDP_VER, timeout=3)
        return True
    except Exception:
        return False

def tg(msg):
    subprocess.run(["python3", str(TG), "ops", msg], capture_output=True)

def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}

def save(s):
    STATE.write_text(json.dumps(s))

def run(script, *args, timeout=300):
    if not Path(script).exists():
        return f"missing: {Path(script).name}"
    r = subprocess.run(["python3", str(script), *args],
                       capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()[-400:] or "no output"

def _db_status(kind):
    """Ask the live DB whether the platform has an active account — the
    same source of truth the human-gate-capture watcher writes."""
    plat = {"meta": "whatsapp", "bluesky": "bluesky",
            "viber": "viber"}.get(kind)
    if plat is None:
        return ""  # meta_card has no account row — marker-only probe
    try:
        code = (
            "import asyncio,sys;sys.path.insert(0,'/app')\n"
            "from app.worker._async import get_session_factory\n"
            "from sqlalchemy import select\n"
            "from app.models.social_account import SocialAccount\n"
            "async def g():\n"
            " async with get_session_factory()() as d:\n"
            f"  print((await d.execute(select(SocialAccount.status).where(SocialAccount.platform=='{plat}'))).scalars().all())\n"
            "asyncio.run(g())\n")
        r = subprocess.run(
            ["docker", "exec", "-i", "social-api", "python", "-"],
            input=code, capture_output=True, text=True, timeout=30).stdout
        return r
    except Exception:
        return ""

def gate_pending(kind):
    """Is this cred still needed? Syncs with the watcher — an active
    account in the DB means wired regardless of which path got it there."""
    st = _db_status(kind)
    if "active" in st:
        return False
    return not Path(f"/tmp/gate_{kind}_wired").exists()

# (name, script, args, pending-probe)
GATES = [
    ("li_restriction",
     SK / "linkedin-browser-ops/scripts/li_restriction_check.py", [],
     lambda: True),                          # always re-read — it can lift anytime
    ("meta_token",
     SK / "human-gate-bridges/scripts/meta_token_harvest.py", [],
     lambda: gate_pending("meta")),
    ("bsky_apppw",
     SK / "gate-cdp-runner/scripts/bsky_apppw_gen.py", [],
     lambda: gate_pending("bluesky")),
    ("viber_webadmin",
     SK / "gate-cdp-runner/scripts/viber_webadmin.py", [],
     lambda: gate_pending("viber")),
    ("meta_payment_fill",
     SK / "meta-ads-ops/scripts/meta_payment_helper.py", ["fill"],
     lambda: gate_pending("meta_card")),
]

def phone_front():
    """Launch the wa-greeting daemon if the phone's attached + not done."""
    marker = Path("/tmp/wa_greeting_done")
    if marker.exists():
        return "done"
    st = subprocess.run(["python3", str(PHONE), "devices"],
                        capture_output=True, text=True, timeout=20).stdout
    if "RFCTA0AEKZP" not in st or "device" not in st:
        return "phone detached"
    if not Path("/tmp/wa_greeting_pid").exists():
        p = subprocess.Popen(["python3", str(PHONE), "wa-greeting"],
                             stdout=open("/tmp/wa_greeting.log", "a"), stderr=subprocess.STDOUT)
        Path("/tmp/wa_greeting_pid").write_text(str(p.pid))
        return f"daemon launched pid={p.pid}"
    return "daemon running"

def status():
    print("cdp:", "up" if cdp_up() else "down")
    for name, *_p, pend in GATES:
        print(f"{name:18} {'pending' if pend() else 'wired/done'}")
    print("wa_greeting:", phone_front.__doc__ and "see --run" or "")
    # real account states
    for kind in ("bluesky", "viber", "meta", "meta_card"):
        print(f"  {kind:12} {'WIRED' if not gate_pending(kind) else 'pending'}")

def sweep_once(state):
    if not cdp_up():
        # try to bring it up once
        if not state.get("_ensure_tried"):
            print("[runner] CDP down — trying cdp_ensure")
            run(ENSURE)
            state["_ensure_tried"] = True
            save(state)
        return False
    for name, script, args, pend in GATES:
        if not pend():
            state[name] = "skip:wired"
            continue
        last = state.get(name, "")
        if last.startswith("done") or last == "skip:wired":
            continue
        print(f"[runner] {name} …")
        out = run(script, *args)
        ok = out and "error" not in out.lower()[:40] and "missing" not in out
        state[name] = ("done: " if ok else "retry: ") + out[:200]
        save(state)
        tg(f"🔑 gate `{name}`: {out[:120]}")
        # auto-unfreeze on restriction lift
        if name == "li_restriction" and "lifted" in out.lower():
            run(BASE / "social-automation/backend/scripts/linkedin_tool.py",
                "unrestrict-all", timeout=60)
            tg("✅ LinkedIn restriction lifted — accounts unrestricting")
    print("[runner] phone:", phone_front())
    return True

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "watch"
    if mode == "--status":
        status()
        return
    state = load()
    if mode == "--once":
        sweep_once(state)
        print(json.dumps({k: v for k, v in state.items() if not k.startswith("_")}, indent=1)[:1200])
        return
    for _ in range(720):
        try:
            sweep_once(state)
        except Exception as e:
            print(f"[runner] sweep error: {e}")
        state = load()
        if all(str(v).startswith(("done", "skip")) for k, v in state.items() if k in [n for n, *_ in GATES]):
            print("[runner] all gates resolved — exiting")
            return
        time.sleep(120)

if __name__ == "__main__":
    main()
