#!/usr/bin/env python3
"""Print the decrypted LinkedIn access token for the first linkedin account.

Used by the linkedin-ads-mcp wrapper to inject LINKEDIN_ACCESS_TOKEN into the
MCP server env. Mirrors app.services.linkedin_ads._token account selection.
Prints ONLY the token to stdout — never log it.
"""

import asyncio

from sqlalchemy import select

from app.core.security import decrypt_token
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount


async def main() -> None:
    async with async_session_maker() as db:
        acc = (
            await db.execute(
                select(SocialAccount).where(SocialAccount.platform == "linkedin")
            )
        ).scalars().first()
    if acc is None:
        raise SystemExit("no linkedin social_account row")
    print(decrypt_token(acc.access_token_enc))


if __name__ == "__main__":
    asyncio.run(main())
