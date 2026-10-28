"""Build daily digest markdown inside social-api for Slack MCP posting."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path


async def main() -> None:
    import httpx

    email = os.environ["SOCIAL_ADMIN_EMAIL"]
    password = os.environ["SOCIAL_ADMIN_PASSWORD"]
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=60.0) as client:
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": email, "password": password},
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        prev = await client.get(
            "/api/v1/ops/daily-digest/preview",
            params={"days": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        prev.raise_for_status()
        data = prev.json()

    reports = data.get("reports") or []
    pick = next(
        (r for r in reports if (r.get("overview") or {}).get("connected_accounts", 0) > 0),
        None,
    )
    if not pick and reports:
        pick = reports[0]

    md = (pick or {}).get("markdown") or "No digest available"
    for label in (
        "SocialAuto daily report",
        "Analytics",
        "Top posts (by engagement)",
        "Issues:",
    ):
        md = md.replace(f"*{label}*", f"**{label}**")

    Path("/tmp/socialauto-digest-slack.md").write_text(md)
    print(
        json.dumps(
            {
                "team": (pick or {}).get("team_name"),
                "chars": len(md),
                "issues": len((pick or {}).get("issues") or []),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
