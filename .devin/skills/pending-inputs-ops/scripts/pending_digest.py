#!/usr/bin/env python3
"""pending_digest.py — email the pending click-actions to the owner so
nothing needs a command. Generates fresh OAuth URLs + the status table,
sends via the campaign/email service. Run inside social-api or via cron.

    pending_digest.py               # send now
    pending_digest.py --dry         # print, don't send
"""
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OWNER = "tbaltzakis@cloudless.gr"


def main() -> int:
    sys.path.insert(0, str(ROOT / "offer-survey-ops/scripts"))
    urls = subprocess.run(
        [sys.executable, str(ROOT / "pending-inputs-ops/scripts/reauth_urls.py")],
        capture_output=True, text=True).stdout.strip()
    body = (
        "Pending inputs — click once each:\n\n"
        "1. X re-auth (twitter expired):\n"
        f"{urls}\n\n"
        "2. LinkedIn org re-auth (Community Management scopes):\n"
        "   same flow — approve once, done\n\n"
        "3. WhatsApp verify — wa_verify_daemon retries hourly,\n"
        "   SMS code polls itself via ADB/mail\n\n"
        "4. Connections.csv — drop in Downloads, the rest is automatic\n\n"
        "Run pending_status.py anytime for live state."
    )
    if "--dry" in sys.argv:
        print(body)
        return 0
    # Slack is the proven notify surface (same channel as digests)
    payload = (body.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n"))
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python", "-c",
         f"import asyncio;from app.services.slack_notifications import post_alert_to_slack;"
         f"asyncio.run(post_alert_to_slack(\"{payload}\"))"],
        capture_output=True, text=True)
    print("slack:", r.returncode == 0 and "sent" or (r.stderr or r.stdout).strip()[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
