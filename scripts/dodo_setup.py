#!/usr/bin/env python3
"""One-shot Dodo Payments setup for SocialAuto billing.

Reads DODO_PAYMENTS_API_KEY + DODO_ENVIRONMENT + FRONTEND_URL from .env,
creates the three subscription products, registers the billing webhook
endpoint, fetches its signing secret, and updates .env in place.

Usage:
    python3 scripts/dodo_setup.py            # create products + webhook, write .env
    python3 scripts/dodo_setup.py --dry-run  # list what would be created
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / ".env"

PRODUCTS = [
    ("DODO_PRODUCT_PRO", "SocialAuto Pro", 1000,
     "Pro plan: 500 posts/mo, 5,000 AI calls/mo, 15 social accounts"),
    ("DODO_PRODUCT_BUSINESS", "SocialAuto Business", 5000,
     "Business plan: 2,000 posts/mo, 20,000 AI calls/mo, 50 social accounts"),
    ("DODO_PRODUCT_ENTERPRISE", "SocialAuto Enterprise", 15000,
     "Enterprise plan: unlimited posts, AI calls, and social accounts"),
]

WEBHOOK_EVENTS = [
    "subscription.active",
    "subscription.updated",
    "subscription.renewed",
    "subscription.on_hold",
    "subscription.failed",
    "subscription.cancelled",
    "subscription.expired",
    "subscription.paused",
    "payment.succeeded",
    "payment.failed",
]


def env_get(key: str) -> str:
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def env_set(key: str, value: str) -> None:
    text = ENV_FILE.read_text()
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    if pattern.search(text):
        text = pattern.sub(f"{key}={value}", text)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"
    ENV_FILE.write_text(text)


def api(method: str, path: str, base: str, key: str, body: dict | None = None) -> dict:
    # Dodo's API is behind Cloudflare bot protection — non-browser TLS
    # signatures get error 1010. Route through the WARP proxy.
    cmd = [
        "curl", "-s", "--socks5-hostname", "127.0.0.1:1080",
        "-X", method, f"{base}{path}",
        "-H", f"Authorization: Bearer {key}",
        "-H", "Content-Type: application/json",
        "-H", "Accept: application/json",
        "--max-time", "30",
        "-w", "\n%{http_code}",
    ]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=40).stdout
    *resp_text, code = out.rsplit("\n", 1)
    text = resp_text[0] if resp_text else ""
    if not code.startswith("2"):
        raise SystemExit(f"Dodo {method} {path} -> {code}: {text[:300]}")
    return json.loads(text) if text else {}


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    key = env_get("DODO_PAYMENTS_API_KEY")
    env = env_get("DODO_ENVIRONMENT") or "test_mode"
    base = "https://live.dodopayments.com" if env == "live_mode" else "https://test.dodopayments.com"
    frontend = (env_get("FRONTEND_URL") or "https://social.cloudless.gr").rstrip("/")
    webhook_url = f"{frontend}/api/v1/billing/dodo-webhook"

    if dry_run:
        print(f"[dry-run] base={base} webhook={webhook_url}")
        for _, name, cents, _ in PRODUCTS:
            print(f"[dry-run] product {name} — ${cents / 100:.0f}/mo recurring")
        return

    if not key:
        raise SystemExit("DODO_PAYMENTS_API_KEY is empty in .env — create an API key in the Dodo dashboard first.")

    print(f"Using {env} ({base})")

    for env_key, name, cents, desc in PRODUCTS:
        if env_get(env_key):
            print(f"= {env_key} already set, skipping {name}")
            continue
        product = api("POST", "/products", base, key, {
            "name": name,
            "description": desc,
            "tax_category": "saas",
            "price": {
                "type": "recurring_price",
                "price": cents,
                "currency": "USD",
                "payment_frequency_count": 1,
                "payment_frequency_interval": "Month",
                "subscription_period_count": 1,
                "subscription_period_interval": "Month",
            },
        })
        product_id = product.get("product_id") or product.get("id")
        env_set(env_key, product_id)
        print(f"+ {env_key}={product_id}  ({name})")

    webhook = api("POST", "/webhooks", base, key, {
        "url": webhook_url,
        "description": "SocialAuto billing events",
        "filter_types": WEBHOOK_EVENTS,
        "metadata": {"source": "socialauto"},
    })
    webhook_id = webhook.get("id") or webhook.get("webhook_id")
    print(f"+ webhook endpoint {webhook_id} -> {webhook_url}")

    secret = api("GET", f"/webhooks/{webhook_id}/secret", base, key)
    signing_key = secret.get("key") or secret.get("secret") or secret.get("signing_key") or ""
    if signing_key:
        env_set("DODO_WEBHOOK_SECRET", signing_key)
        print("+ DODO_WEBHOOK_SECRET written to .env")

    env_set("BILLING_PROVIDER", "dodo")
    print("\nDone. BILLING_PROVIDER=dodo set. Restart social-api:")
    print("  docker compose restart social-api social-worker-default celery-beat")


if __name__ == "__main__":
    main()
