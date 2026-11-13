#!/usr/bin/env python3
"""invoice_pay — Cloudflare invoice payment automation.

Two lanes (researched 2026-11-13, developers.cloudflare.com/api):

1. API lane — POST /accounts/{id}/pay-bad-debt (discovers all debt,
   handles dedupe) or /pay-invoice {invoice_id}. Requires an API token
   with Billing Write — the stock CLOUDFLARE_API_TOKEN/CF_API_TOKEN do
   NOT have it (verified: billing endpoints return auth error). Set
   CLOUDFLARE_BILLING_TOKEN in .env once a Billing:Edit token exists.
   If Stripe SCA is required the API returns a client_secret for
   frontend confirmation — this script reports it, never fakes it.

2. CDP lane — drives the operator's logged-in dash.cloudflare.com
   session (same pattern as obs_cancel.py) to the Billing → Pay now
   flow. Works without a new token but cannot complete a 3DS/SCA
   challenge — that stays a human step.

Usage:
    invoice_pay.py check            # report unpaid invoices (API or CDP)
    invoice_pay.py pay --yes        # actually attempt payment
    invoice_pay.py pay              # dry-run: show exactly what would run
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

API = "https://api.cloudflare.com/client/v4"
CDP = "http://localhost:9222"
ACC = "fb7dc7b69b662480cd5961a4d1913c78"
BILLING_URL = f"https://dash.cloudflare.com/{ACC}/billing"


def _env(name: str) -> str:
    v = os.environ.get(name)
    if v:
        return v
    for line in (Path.home() / "cu130-slim" / ".env").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(body or {}).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"success": False, "errors": [{"message": f"HTTP {e.code}"}]}


def unpaid_via_api() -> dict:
    tok = _env("CLOUDFLARE_BILLING_TOKEN") or _env("CLOUDFLARE_API_TOKEN")
    d = _api("GET", f"/accounts/{ACC}/billing/unpaid-invoice", tok)
    if d.get("success"):
        return {"lane": "api", "invoices": (d.get("result") or {}).get("invoices") or []}
    errs = "; ".join(e.get("message", "?") for e in d.get("errors", []))
    return {"lane": "api", "error": errs, "needs_billing_token": "Billing" in errs or "uthentication" in errs}


def _cdp_tab() -> dict | None:
    try:
        tabs = json.loads(urllib.request.urlopen(CDP + "/json", timeout=5).read())
    except Exception:
        return None
    return next((t for t in tabs if "dash.cloudflare.com" in t.get("url", "")), None)


def _cdp_eval(expr: str) -> str:
    tab = _cdp_tab()
    if not tab:
        return ""
    import websocket  # websocket-client
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=20)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    out = json.loads(ws.recv())["result"]["result"].get("value")
    ws.close()
    return out or ""


def unpaid_via_cdp() -> dict:
    txt = _cdp_eval("document.body.innerText.slice(0,8000)")
    if not txt:
        return {"lane": "cdp", "error": "no dash.cloudflare.com tab on :9222"}
    unpaid = [ln.strip() for ln in txt.splitlines()
              if "unpaid" in ln.lower() or "pay now" in ln.lower() or "outstanding" in ln.lower()]
    return {"lane": "cdp", "signals": unpaid[:10], "url": _cdp_eval("location.href")}


def pay_via_api(invoice_id: str | None) -> dict:
    tok = _env("CLOUDFLARE_BILLING_TOKEN")
    if not tok:
        return {"ok": False, "reason": "CLOUDFLARE_BILLING_TOKEN not set — create a "
                "dash.cloudflare.com API token with Account → Billing → Edit"}
    path = (f"/accounts/{ACC}/pay-invoice" if invoice_id
            else f"/accounts/{ACC}/pay-bad-debt")
    body = {"invoice_id": invoice_id} if invoice_id else {}
    d = _api("POST", path, tok, body)
    if d.get("success"):
        return {"ok": True, "result": d.get("result")}
    errs = "; ".join(e.get("message", "?") for e in d.get("errors", []))
    sca = (d.get("result") or {}).get("client_secret")
    return {"ok": False, "reason": errs, "sca_client_secret": sca,
            "note": "SCA/3DS challenge needs the cardholder — finish in dashboard" if sca else ""}


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    yes = "--yes" in sys.argv

    if cmd == "check":
        r = unpaid_via_api()
        if r.get("error"):
            # 10404 = endpoint not routed for this token/version; auth
            # errors mean the billing scope is missing. Either way the
            # CDP dashboard lane is the reliable read.
            r = unpaid_via_cdp()
            r["api_note"] = "api lane unavailable"
        print(json.dumps(r, indent=2))
        return 0

    if cmd == "pay":
        r = unpaid_via_api()
        invoices = r.get("invoices") or []
        inv_id = None
        if invoices:
            inv_id = next((i.get("invoice_id") or i.get("id") for i in invoices
                           if (i.get("amount_to_pay") or i.get("amount"))), None)
        if not yes:
            print("DRY-RUN — would attempt:",
                  f"pay-invoice {inv_id}" if inv_id else "pay-bad-debt (all debt)")
            print("Re-run with --yes to actually charge the default payment method.")
            return 0
        print(json.dumps(pay_via_api(inv_id), indent=2))
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
