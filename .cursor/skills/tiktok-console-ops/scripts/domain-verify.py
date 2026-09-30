#!/usr/bin/env python3
"""Domain verification flow (official Content Posting media-transfer guide):
  1) Playwright: capture/add domain token for cloudless.gr
  2) Cloudflare: add TXT tiktok-domain-verification=...
  3) Playwright: click Verify (CLICK_VERIFY=1)"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import NODE_WORK, OUT_DIR, ensure_playwright, run_mjs  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent

ensure_playwright()
import shutil
shutil.copy(SCRIPT_DIR / "lib/domain-verify.mjs", NODE_WORK / "domain-verify.mjs")

print("== Pass 1: capture domain token ==")
if run_mjs("domain-verify.mjs", extra_env={"CLICK_VERIFY": "0"}) != 0:
    sys.exit(1)

token_file = OUT_DIR / "domain-verification-token.json"
if not token_file.exists():
    print("No token file written", file=sys.stderr)
    sys.exit(1)
token = json.loads(token_file.read_text()).get("token") or ""
if not token:
    print("No tiktok-domain-verification token found — open console manually "
          "or fix app URL properties UI", file=sys.stderr)
    print(json.dumps(json.loads(token_file.read_text()), indent=2)[:2000])
    sys.exit(2)

print("== Pass 2: add Cloudflare TXT ==")
r = subprocess.run([sys.executable, str(SCRIPT_DIR / "dns-tiktok-txt.py"),
                    "add", token])
if r.returncode != 0:
    sys.exit(r.returncode)

print("== Pass 3: click Verify ==")
if run_mjs("domain-verify.mjs", extra_env={"CLICK_VERIFY": "1"}) != 0:
    sys.exit(1)

print("== DNS list ==")
subprocess.run([sys.executable, str(SCRIPT_DIR / "dns-tiktok-txt.py"), "list"])
print(f"Done. Review {OUT_DIR}/domain-verify-result.json if present.")
