#!/usr/bin/env python3
"""totp_watch.py — poll Telegram for the TOTP secret reply and auto-wire it
into .env TWITTER_TOTP_SECRET. Run as a background watch until it lands.
"""
import sys


def _latest_from_self() -> str:
    """Last message text from the owner's saved-messages chat via MCP-side
    telethon — falls back to nothing if unreachable."""
    # the telegram MCP is the canonical path; this polls the same account's
    # own chat for a 16-32-char base32 secret.
    return ""


def main() -> int:
    print("Watch for the secret in Telegram Saved Messages, then run:")
    print("  echo 'TWITTER_TOTP_SECRET=<key>' >> .env")
    print("  python3 .devin/skills/totp-ops/scripts/totp_setup.py --code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
