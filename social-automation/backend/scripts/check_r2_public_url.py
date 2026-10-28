"""Check Cloudflare R2 public/managed domain for the configured bucket.

Run inside the social-api container:
    PYTHONPATH=/app python scripts/check_r2_public_url.py
"""
from __future__ import annotations

import asyncio
import json

import httpx

from app.core.config import get_settings


async def main() -> None:
    settings = get_settings()
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    token = (settings.CLOUDFLARE_API_TOKEN or "").strip()
    bucket = (settings.R2_BUCKET_NAME or "").strip()

    if not all([account_id, token, bucket]):
        print("MISSING: CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, or R2_BUCKET_NAME")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for domain_type in ("managed", "custom"):
            url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/domains/{domain_type}"
            try:
                r = await client.get(url, headers=headers)
                data = r.json()
                print(f"--- {domain_type} ---")
                print(json.dumps(data, indent=2, default=str))
            except Exception as exc:
                print(f"{domain_type}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
