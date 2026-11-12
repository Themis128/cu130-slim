#!/usr/bin/env python3
"""meta_billing_watch.py — watch the Meta billing account until a payment
method exists, then say so once.

The cloudless.gr billing account (2332156284283266 / business
1558125105019725) has ZERO payment methods — adding a card is the one
human billing action left on the WhatsApp spend path. This watcher
CDP-drives the logged-in Meta session to the billing_hub payment_methods
page, scrapes the DOM for card rows vs the "Add payment method" CTA, and:

  - payment method APPEARS  -> TG #ops "billing ready" (once)
  - still absent            -> TG #ops reminder at most once per 24h
  - no Meta session in any debug Chrome -> logs only (can't see state)

    meta_billing_watch.py          # one probe (cron lane)
    meta_billing_watch.py --json   # machine-readable probe result

CDP endpoints tried in order: localhost:9222 (Windows debug Chrome),
172.22.144.1:9229 (WSL relay -> chrome-debug-profile :9330).
State: ~/.local/state/meta-billing-watch/state.json
Log:   ~/logs/meta-billing-watch.log
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

CDP_CANDIDATES = ["http://localhost:9222", "http://172.22.144.1:9229"]
BILLING_URL = ("https://business.facebook.com/billing_hub/payment_methods"
               "?business_id=1558125105019725")
STATE_DIR = os.path.expanduser("~/.local/state/meta-billing-watch")
STATE = os.path.join(STATE_DIR, "state.json")
LOG = os.path.expanduser("~/logs/meta-billing-watch.log")
TG_ALERT = ("/home/tbaltzakis/cu130-slim/.devin/skills/"
            "telegram-alert-ops/scripts/tg_alert.py")
REMINDER_SECS = 24 * 3600

PROBE_JS = r"""
(() => {
  const text = document.body ? document.body.innerText : '';
  const hasAdd = /add payment method|add card|add funds/i.test(text);
  // card rows usually show last4 / brand / expiry — count likely rows
  const last4 = (text.match(/\b(?:visa|mastercard|master card|amex|american express)\b[\s\S]{0,80}?\b\d{4}\b/gi) || []).length;
  const exp = (text.match(/\b\d{2}\s*\/\s*\d{2,4}\b/g) || []).length;
  const pending = /pending|verification|review/i.test(text);
  return JSON.stringify({
    url: location.href,
    hasAdd, last4, exp, pending,
    snippet: text.slice(0, 400)
  });
})()
"""


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def tg(text):
    try:
        subprocess.run(["python3", TG_ALERT, "ops", text], timeout=30)
    except Exception as e:
        log(f"tg alert failed: {e}")


def _cdp_base():
    for base in CDP_CANDIDATES:
        try:
            tabs = json.loads(urllib.request.urlopen(
                base + "/json", timeout=5).read())
            if tabs:
                return base, tabs
        except Exception:
            continue
    return None, []


def probe():
    """Returns dict with status: ok|no-session|no-meta-tab|error."""
    base, tabs = _cdp_base()
    if not base:
        return {"status": "no-session", "detail": "no CDP endpoint reachable"}
    tab = next((t for t in tabs
                if t.get("type") == "page"
                and ("facebook.com" in t.get("url", "")
                     or "business.facebook" in t.get("url", ""))), None)
    if not tab:
        return {"status": "no-meta-tab",
                "detail": f"CDP up at {base} but no facebook tab",
                "tabs": [t.get("url", "")[:80] for t in tabs[:5]]}
    try:
        import websocket
        ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=20)
        ws.send(json.dumps({"id": 1, "method": "Page.navigate",
                            "params": {"url": BILLING_URL}}))
        ws.recv()
        time.sleep(9)
        ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                            "params": {"expression": PROBE_JS,
                                       "returnByValue": True}}))
        # drain until id=2 response
        result = None
        for _ in range(30):
            msg = json.loads(ws.recv())
            if msg.get("id") == 2:
                result = msg.get("result", {}).get("result", {}).get("value")
                break
        ws.close()
        if result is None:
            return {"status": "error", "detail": "eval returned nothing"}
        data = json.loads(result)
        data["status"] = "ok"
        data["via"] = base
        return data
    except Exception as e:
        return {"status": "error", "detail": str(e)[:200]}


def _load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def _save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1)


def looks_paid(data):
    """Heuristic: card-brand+last4 rows and no 'add' CTA => method exists."""
    return bool(data.get("last4")) and not data.get("hasAdd")


def main():
    as_json = "--json" in sys.argv
    res = probe()
    if as_json:
        print(json.dumps(res, indent=1))
        return 0

    st = _load_state()
    now = time.time()

    if res["status"] != "ok":
        log(f"probe {res['status']}: {res.get('detail', '')}")
        return 0  # can't see billing state — stay quiet

    paid = looks_paid(res)
    log(f"billing probe: paid={paid} hasAdd={res.get('hasAdd')} "
        f"last4={res.get('last4')} exp={res.get('exp')}")

    if paid and not st.get("paid_alerted"):
        tg("✅ Meta billing payment method detected on cloudless.gr — "
           "WhatsApp spend is unblocked. Verify in Billing & payments.")
        st["paid_alerted"] = True
        st["paid_at"] = now
        _save_state(st)
        return 0

    if not paid and now - st.get("last_reminder", 0) > REMINDER_SECS:
        tg("💳 Meta billing still has NO payment method — WhatsApp Cloud "
           "API sends will fail until a card is added at business."
           "facebook.com → Billing & payments. (daily reminder, "
           "auto-silences when detected)")
        st["last_reminder"] = now
        _save_state(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
