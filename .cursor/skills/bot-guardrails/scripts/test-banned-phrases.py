#!/usr/bin/env python3
"""Test post-LLM banned-phrase validation (brand_compliance).\nUsage: test-banned-phrases.py"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

TEST_PY = r"""

import asyncio
from app.services.brand_compliance import score_brand_compliance

voice = {"banned_phrases": ["cheap", "guaranteed results", "free money"],
         "preferred_phrases": [], "tone_dimensions": {}}
brand = {"name": "test"}

async def main():
    # banned phrase present
    bad = await score_brand_compliance("We offer cheap guaranteed results!", brand, voice)
    good = await score_brand_compliance("We help teams ship faster.", brand, voice)
    ok1 = bad.get("banned_found") or bad.get("issues")
    ok2 = not (good.get("banned_found") or [])
    print(f"  [{'PASS' if ok1 else 'FAIL'}] banned phrase detected: {bad.get('banned_found')}")
    print(f"  [{'PASS' if ok2 else 'FAIL'}] clean content passed")
    exit(0 if (ok1 and ok2) else 1)

asyncio.run(main())

"""

print("=== Banned Phrases Test ===\n")
sys.exit(
    subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", TEST_PY],
        cwd=repo_root(),
    ).returncode
)
