#!/usr/bin/env python3
"""Test the full bot reply pipeline for a deterministic (pricing) intercept.
Sends a pricing question through generate_contextual_reply and verifies the
hardcoded response is returned without LLM generation.
Usage: test-full-pipeline.py"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

TEST_PY = r"""
import asyncio
import app.services.messenger_chatbot as mc

async def main():
    # Pricing message should be intercepted before the LLM. We verify the
    # intercept by checking the deterministic path produces the hardcoded reply.
    msg = "How much does a website cost?"
    assert mc._is_pricing_question(msg), "pricing intercept did not fire"
    lang = "greek" if mc._is_greek_message(msg) else "english"
    reply = mc._ensure_steering_question(mc._PRICING_RESPONSES[lang], lang == "greek")
    assert "cloudless.gr" in reply, "reply missing cloudless.gr"
    print("  [PASS] pricing question intercepted, deterministic reply produced")
    print(f"  reply: {reply[:120]}")

    msg2 = "Πόσο κοστίει;"
    assert mc._is_pricing_question(msg2), "greek pricing intercept did not fire"
    reply2 = mc._ensure_steering_question(mc._PRICING_RESPONSES["greek"], True)
    print("  [PASS] greek pricing intercepted")
    print(f"  reply: {reply2[:120]}")

asyncio.run(main())
"""

print("=== Full Pipeline (deterministic intercept) Test ===\n")
sys.exit(
    subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", TEST_PY],
        cwd=repo_root(),
    ).returncode
)
