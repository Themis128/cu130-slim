#!/usr/bin/env python3
"""Show current guardrail configuration.\nUsage: show-config.py"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

TEST_PY = r"""

from app.services.messenger_chatbot import _PRICING_RESPONSES, _is_pricing_question
import app.services.messenger_chatbot as mc

print("== Pricing keywords (module constants) ==")
for name in dir(mc):
    if "KEYWORD" in name.upper():
        print(f"  {name} = {getattr(mc, name)}")
print("\n== Pricing responses ==")
for lang, resp in _PRICING_RESPONSES.items():
    print(f"  {lang}: {resp[:100]}")
print("\n== Detection functions ==")
for fn in ("_is_pricing_question", "_is_greek_message", "is_frustrated_message"):
    print(f"  {fn}: {'present' if hasattr(mc, fn) else 'MISSING'}")

"""

print("=== Guardrail Config ===\n")
sys.exit(
    subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", TEST_PY],
        cwd=repo_root(),
    ).returncode
)
