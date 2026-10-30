#!/usr/bin/env python3
"""Meta ads/billing/account health audit via the facebook-browser-sidecar.

Usage: meta_ads_audit.py [quick|full]
  quick - Ads Manager + payment settings (the two surfaces that matter most)
  full  - + billing hub portfolio, Account Quality, WhatsApp onboarding page

Prints a status line per surface with any restriction flags found.
"""
import json
import sys
import time
import urllib.request

SIDECAR = "http://localhost:9226"
AD_ACCOUNT = "657781691826702"
BIZ_ID = "1558125105019725"
WABA_ASSET = "1073707258453499"

FLAGS = {
    "disabled": "ad account disabled",
    "restricted": "restricted",
    "verify-geo": "verify your business location",
    "unusual-activity": "unusual activity",
    "perm-denied": "not allowed to modify",
    "blocked": "temporarily blocked",
    "no-payment": "no payment method",
    "on-premise": "ON_PREMISE",
    "not-verified": "NOT_VERIFIED",
}


def post(path, payload):
    req = urllib.request.Request(
        f"{SIDECAR}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=45).read())


def get(path):
    return json.loads(urllib.request.urlopen(f"{SIDECAR}{path}", timeout=20).read())


def check(name, url):
    try:
        post("/debug/navigate", {"url": url})
        time.sleep(6)
        t = get("/debug/page-text")
        txt = t.get("text", "").lower()
        flags = [k for k, v in FLAGS.items() if v.lower() in txt]
        print(f"{name:28} | {'; '.join(flags) if flags else 'ok'}")
        return flags
    except Exception as e:
        print(f"{name:28} | ERROR: {e}")
        return []


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "quick"
    try:
        v = json.loads(
            urllib.request.urlopen(f"{SIDECAR}/session/validate", timeout=15).read()
        )
        if not v.get("logged_in"):
            print("SIDECAR NOT LOGGED IN — aborting")
            sys.exit(1)
    except Exception:
        print("sidecar validate failed (may be waking); retrying in 8s")
        time.sleep(8)

    check("ads-manager", "https://adsmanager.facebook.com/adsmanager/manage/accounts")
    check(
        "payment-settings",
        f"https://business.facebook.com/ads/manager/billing/payment_methods/?act={AD_ACCOUNT}",
    )
    if mode == "full":
        check(
            "billing-hub",
            f"https://business.facebook.com/latest/billing_hub/payment_methods/?asset_id={WABA_ASSET}&business_id={BIZ_ID}",
        )
        check("account-quality", "https://www.facebook.com/accountquality")
        check(
            "whatsapp-onboarding",
            "https://developers.facebook.com/apps/1936126137016578/whatsapp-business/wa-settings/",
        )


if __name__ == "__main__":
    main()
