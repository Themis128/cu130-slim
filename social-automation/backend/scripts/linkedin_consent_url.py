"""linkedin_consent_url.py — mint a fresh signed LinkedIn consent URL.

Replaces hand-built authorize URLs. Requests the FULL scope union
(LINKEDIN_SCOPES + LINKEDIN_EXTRA_SCOPES) so every consent lands all
approved scopes — LinkedIn grants REPLACE the scope set, never union.

IMPORTANT: every consent click REVOKES the previous token pair — click
once, stop at "connected successfully", never re-click an old link.

Run inside social-api (scripts bind-mount is :ro — cp to /tmp):

    docker compose cp social-automation/backend/scripts/linkedin_consent_url.py \
      social-api:/tmp/cu.py
    docker compose exec -T -w /app social-api env PYTHONPATH=/app \
      python /tmp/cu.py
"""

from __future__ import annotations

import asyncio

from app.api.auth import _linkedin_scopes, linkedin_client
from app.core.config import get_settings
from app.core.security import sign_oauth_state

TEAM_ID = "10024d25-2ea4-40ea-86c6-ba89f515a2eb"


async def build() -> str:
    s = get_settings()
    url = await linkedin_client.get_authorization_url(
        s.LINKEDIN_REDIRECT_URI,
        state=sign_oauth_state({"t": TEAM_ID}),
        scope=_linkedin_scopes(),
    )
    print(url)
    return url


if __name__ == "__main__":
    asyncio.run(build())
