"""Enable the managed public domain for the configured R2 bucket.

Run inside the social-api container:
    PYTHONPATH=/app python scripts/enable_r2_public.py
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
        get_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/domains/managed"
        r = await client.get(get_url, headers=headers)
        current = r.json()
        if not current.get("success"):
            print("GET_FAILED:", json.dumps(current, indent=2))
            return

        domain = current["result"]["domain"]
        if current["result"]["enabled"]:
            print(f"ALREADY_ENABLED: {domain}")
        else:
            put_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/domains/managed"
            r = await client.put(put_url, headers=headers, json={"enabled": True})
            data = r.json()
            if data.get("success"):
                print(f"ENABLED: {domain}")
            else:
                print("ENABLE_FAILED:", json.dumps(data, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
