#!/usr/bin/env python3
"""Poll TikTok business-verification status via tiktok-browser-sidecar.

Navigates to the Business Suite verify page, extracts the status line
(Under Review / Approved / Disapproved + reason), and Telegram-alerts #ops
on every state change. Run from cron; idempotent — safe to run anytime.

Usage: tt_verify_status.py [--once] [--json]
State: ~/.local/state/tiktok-verify/status
"""
import json
import subprocess
import sys
import time
from pathlib import Path

SIDECAR = "tiktok-browser-sidecar"
API = "http://localhost:9224"
VERIFY_URL = ("https://www.tiktok.com/business-suite/business-registration/"
              "verify?source=onboarding")
STATE_DIR = Path.home() / ".local/state/tiktok-verify"
STATE_FILE = STATE_DIR / "status"
TG_ALERT = (Path.home() / "cu130-slim/.devin/skills/"
            "telegram-alert-ops/scripts/tg_alert.py")


def call(method, path, body=None):
    node_prog = f"""
let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{{
 (async()=>{{
  const body=d?JSON.parse(d):undefined;
  const r=await fetch("{API}{path}",{{method:"{method}",
   headers:{{"Content-Type":"application/json"}},body:body?JSON.stringify(body):undefined}});
  const j=await r.json();console.log(JSON.stringify(j));
 }})().catch(e=>console.log(JSON.stringify({{error:String(e)}})));
}})"""
    payload = json.dumps(body) if body is not None else ""
    out = subprocess.run(
        ["docker", "exec", "-i", SIDECAR, "node", "-e", node_prog],
        input=payload, capture_output=True, text=True, timeout=120)
    line = out.stdout.strip().splitlines()
    if not line:
        return {"error": out.stderr.strip()[:300] or "no output"}
    try:
        return json.loads(line[-1])
    except Exception:
        return {"raw": line[-1][:300]}


def probe():
    """Return (status, detail) parsed from the verify page."""
    call("POST", "/browse", {"url": VERIFY_URL})
    time.sleep(8)
    expr = """JSON.stringify({
url:location.href,
text:(document.querySelector('.verification-verify-page')||document.body).innerText.slice(0,2500)
})"""
    res = call("POST", "/evaluate", {"expression": expr}).get("result", "")
    try:
        page = json.loads(res)
    except Exception:
        return "unknown", f"unparseable page: {res[:200]}"
    text = page.get("text", "")
    low = text.lower()
    if "under review" in low:
        return "under_review", "submission under review"
    if "approved" in low or "verified" in low:
        # distinguish banner 'Disapproved' vs plain approval
        if "disapproved" in low:
            reason = ""
            for line in text.splitlines():
                if "reason" in line.lower():
                    reason = line.strip()
                    break
            return "disapproved", reason or "disapproved (no reason shown)"
        return "approved", "business verified"
    if "disapproved" in low:
        reason = ""
        for line in text.splitlines():
            if "reason" in line.lower():
                reason = line.strip()
                break
        return "disapproved", reason or "disapproved"
    if "verify your business" in low:
        return "unsubmitted", "form present, not yet submitted"
    return "unknown", f"url={page.get('url','?')} text={text[:200]!r}"


def main():
    as_json = "--json" in sys.argv
    status, detail = probe()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    prev = STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""
    now = f"{status}|{detail}"
    STATE_FILE.write_text(now + "\n")

    if as_json:
        print(json.dumps({"status": status, "detail": detail,
                          "prev": prev or None}))
        return 0

    print(f"[*] tiktok-verify: {status} — {detail}")
    if prev and prev.split("|")[0] != status:
        msg = (f"TikTok business verification: {prev.split('|')[0]} → "
               f"{status}. {detail}")
        if status == "approved":
            sev = "P2"
        elif status == "disapproved":
            sev = "P2"
        else:
            sev = "P3"
        subprocess.run(
            ["python3", str(TG_ALERT), sev, msg],
            capture_output=True, timeout=60)
        print(f"[*] alerted: {msg}")
    elif not prev:
        print("[*] first run — baseline recorded, no alert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
