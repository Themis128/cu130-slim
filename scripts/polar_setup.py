#!/usr/bin/env python3
"""One-shot Polar.sh setup for SocialAuto billing.

Reads POLAR_ACCESS_TOKEN + POLAR_ENVIRONMENT + FRONTEND_URL from .env,
creates the three subscription products, registers the billing webhook
endpoint, captures its signing secret, and updates .env in place.

Usage:
    python3 scripts/polar_setup.py            # create products + webhook, write .env
    python3 scripts/polar_setup.py --dry-run  # list what would be created
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
    ("POLAR_PRODUCT_PRO", "SocialAuto Pro", 1000,
     "Pro plan: 500 posts/mo, 5,000 AI calls/mo, 15 social accounts"),
    ("POLAR_PRODUCT_BUSINESS", "SocialAuto Business", 5000,
     "Business plan: 2,000 posts/mo, 20,000 AI calls/mo, 50 social accounts"),
    ("POLAR_PRODUCT_ENTERPRISE", "SocialAuto Enterprise", 15000,
     "Enterprise plan: unlimited posts, AI calls, and social accounts"),
]

WEBHOOK_EVENTS = [
    "subscription.created",
    "subscription.updated",
    "subscription.active",
    "subscription.canceled",
    "subscription.uncanceled",
    "subscription.resumed",
    "subscription.revoked",
    "subscription.past_due",
    "order.paid",
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
    # Polar API is Cloudflare-fronted; route through the WARP proxy so
    # datacenter TLS signatures don't trip bot protection.
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
        raise SystemExit(f"Polar {method} {path} -> {code}: {text[:300]}")
    return json.loads(text) if text else {}


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    key = env_get("POLAR_ACCESS_TOKEN")
    env = env_get("POLAR_ENVIRONMENT") or "sandbox"
    base = (
        "https://api.polar.sh/v1"
        if env.strip().lower() == "production"
        else "https://sandbox-api.polar.sh/v1"
    )
    frontend = (env_get("FRONTEND_URL") or "https://social.cloudless.gr").rstrip("/")
    webhook_url = f"{frontend}/api/v1/billing/polar-webhook"

    if dry_run:
        print(f"[dry-run] base={base} webhook={webhook_url}")
        for _, name, cents, _ in PRODUCTS:
            print(f"[dry-run] product {name} — ${cents / 100:.0f}/mo recurring")
        return

    if not key:
        raise SystemExit(
            "POLAR_ACCESS_TOKEN is empty in .env — create an Organization "
            "Access Token in the Polar dashboard (Settings → Developers) first."
        )

    print(f"Using {env} ({base})")

    for env_key, name, cents, desc in PRODUCTS:
        if env_get(env_key):
            print(f"= {env_key} already set, skipping {name}")
            continue
        product = api("POST", "/products/", base, key, {
            "name": name,
            "description": desc,
            "recurring_interval": "month",
            "recurring_interval_count": 1,
            "prices": [{
                "amount_type": "fixed",
                "price_amount": cents,
                "price_currency": "usd",
            }],
            "metadata": {"source": "socialauto"},
        })
        product_id = product.get("id")
        env_set(env_key, product_id)
        print(f"+ {env_key}={product_id}  ({name})")

    webhook = api("POST", "/webhooks/endpoints", base, key, {
        "url": webhook_url,
        "format": "raw",
        "events": WEBHOOK_EVENTS,
    })
    webhook_id = webhook.get("id")
    print(f"+ webhook endpoint {webhook_id} -> {webhook_url}")

    # Polar returns the signing secret on endpoint creation.
    secret = webhook.get("secret") or ""
    if secret:
        env_set("POLAR_WEBHOOK_SECRET", secret)
        print("+ POLAR_WEBHOOK_SECRET written to .env")
    else:
        print("! webhook secret not in response — copy it from the dashboard "
              "and set POLAR_WEBHOOK_SECRET manually")

    env_set("BILLING_PROVIDER", "polar")
    print("\nDone. BILLING_PROVIDER=polar set. Restart social-api:")
    print("  docker compose restart social-api social-worker-default celery-beat")


if __name__ == "__main__":
    main()
