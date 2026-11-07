#!/usr/bin/env python3
"""Report granted vs expected TikTok OAuth scopes on the connected account."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

subprocess.run(["docker", "exec", "-i", "social-api", "python", "-"], input="""
import asyncio
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from sqlalchemy import select

# Scopes enabled on the production TikTok app (devportal app/detail,
# verified 2026-11-07). user.info.profile/stats + video.list exist in the
# console scope catalog but are NOT enabled — enabling them requires a
# new app revision + review.
EXPECTED = {
    "user.info.basic", "video.publish", "video.upload",
}
PENDING_CONSOLE = {
    "user.info.profile", "user.info.stats", "video.list",
}
CAPABILITY = {
    "user.info.basic": "avatar/display_name at OAuth",
    "user.info.profile": "bio, is_verified, profile_deep_link, username",
    "user.info.stats": "follower/following/likes/video counts (profile_sync)",
    "video.list": "Display API video list + per-video metrics",
    "video.publish": "DIRECT_POST",
    "video.upload": "MEDIA_UPLOAD inbox drafts",
}

async def m():
    async with async_session_maker() as s:
        accs = (await s.execute(select(SocialAccount).where(
            SocialAccount.platform == "tiktok"))).scalars().all()
        for a in accs:
            granted = set(a.scopes or [])
            missing = EXPECTED - granted
            print(f"account @{a.username} [{a.status}]")
            print(f"  granted: {sorted(granted)}")
            for sc in sorted(missing):
                print(f"  MISSING {sc} — unlocks {CAPABILITY[sc]}")
            pending = PENDING_CONSOLE - granted
            for sc in sorted(pending):
                print(f"  console-pending {sc} — not enabled on prod app")
            if missing:
                print("  fix: .devin/skills/social-oauth-ops/scripts/tiktok-reconnect.py")
            else:
                print("  all scopes granted")

asyncio.run(m())
""", text=True, cwd=repo_root())
