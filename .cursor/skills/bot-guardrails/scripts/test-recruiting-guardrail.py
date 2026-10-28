#!/usr/bin/env python3
"""Verify recruiting keywords live in the personal auto-reply system prompt.\nRecruiting detection is prompt-driven (not a deterministic function).\nUsage: test-recruiting-guardrail.py"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

TEST_PY = r"""

import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import engine

ACCOUNT_ID = "de7234c0-0209-4243-a08f-ce7b96f1ebc7"  # personal Messenger
KEYWORDS = ["job", "position", "role", "hiring", "recruiter", "cv", "resume", "interview"]

async def main():
    async with AsyncSession(engine) as db:
        row = (await db.execute(text(
            "SELECT metadata FROM social_accounts WHERE id = :id"),
            {"id": ACCOUNT_ID})).fetchone()
    if not row or not row[0]:
        print("  [FAIL] account metadata not found")
        exit(1)
    cfg = (row[0] or {}).get("personal_auto_reply", {})
    prompt = (cfg.get("system_prompt") or "").lower()
    if not cfg.get("enabled"):
        print("  [WARN] personal auto-reply disabled")
    missing = [k for k in KEYWORDS if k not in prompt]
    if missing:
        print(f"  [FAIL] system prompt missing recruiting keywords: {missing}")
        exit(1)
    print("  [PASS] system prompt contains all recruiting keywords")
    print(f"  prompt excerpt: {prompt[:120]}")

asyncio.run(main())

"""

print("=== Recruiting Guardrail Test ===\n")
sys.exit(
    subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", TEST_PY],
        cwd=repo_root(),
    ).returncode
)
