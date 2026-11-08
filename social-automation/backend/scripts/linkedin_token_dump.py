#!/usr/bin/env python3
"""Print the decrypted LinkedIn access token for the first linkedin account.

Used by the linkedin-ads-mcp wrapper to inject LINKEDIN_ACCESS_TOKEN into the
MCP server env. Mirrors app.services.linkedin_ads._token account selection
(first social_accounts row with platform='linkedin').

Standalone fast path: imports only asyncpg + cryptography (~2s) instead of the
full app stack (~20s), so MCP clients don't blow their init timeout. Prints
ONLY the token to stdout — never log it.
"""

import asyncio
import base64
import os

import asyncpg
from cryptography.fernet import Fernet


async def main() -> None:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        enc = await conn.fetchval(
            "SELECT access_token_enc FROM social_accounts "
            "WHERE platform = 'linkedin' LIMIT 1"
        )
    finally:
        await conn.close()
    if enc is None:
        raise SystemExit("no linkedin social_account row")
    raw = os.environ["ENCRYPTION_KEY"].encode()
    if len(raw) != 32:
        raise SystemExit("ENCRYPTION_KEY must be exactly 32 bytes")
    print(Fernet(base64.urlsafe_b64encode(raw)).decrypt(bytes(enc)).decode())


if __name__ == "__main__":
    asyncio.run(main())
