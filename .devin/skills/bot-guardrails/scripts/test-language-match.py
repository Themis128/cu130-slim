#!/usr/bin/env python3
"""Test Greek/English language detection guardrail.\nUsage: test-language-match.py"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

TEST_PY = r"""

from app.services.messenger_chatbot import _is_greek_message

cases = [
    ("Πόσο κάνει;", True),
    ("Γεια σου", True),
    ("Τιμή παρακαλώ", True),
    ("How much?", False),
    ("Hello there", False),
    ("Price please", False),
    ("Πόσο does it cost?", True),  # mixed — Greek chars present
]
passed = failed = 0
for msg, expected in cases:
    got = _is_greek_message(msg)
    ok = got == expected
    passed += ok
    failed += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {msg[:40]:40s} greek={got}")
print(f"\nResults: {passed} passed, {failed} failed")
exit(1 if failed else 0)

"""

print("=== Language Match Test ===\n")
sys.exit(
    subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python3", "-c", TEST_PY],
        cwd=repo_root(),
    ).returncode
)
