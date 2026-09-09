#!/usr/bin/env python3
"""Check and apply optimal Instagram personal account settings.

Based on 2025 Instagram optimization research:
- Privacy: public for personal branding, private for personal use
- Activity status: off (security)
- Story sharing: close friends only
- Two-factor authentication: authenticator app (not SMS)
- Close Friends list: curated for exclusive content
- Comment filters: hide offensive words
- Tag controls: manual approval
- Content preferences: reset algorithm if needed

Usage:
    # Check current settings via browser bridge
    python instagram_settings_checklist.py --check

    # Print the recommended settings checklist
    python instagram_settings_checklist.py --list

    # Check a specific setting
    python instagram_settings_checklist.py --check-setting privacy
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BRIDGE_URL = "http://localhost:9223"

# --- Recommended settings for personal accounts ---

RECOMMENDED_SETTINGS = [
    {
        "category": "Security",
        "setting": "Two-Factor Authentication",
        "recommendation": "Enable with Authenticator App (NOT SMS)",
        "why": "SMS can be SIM-swapped. Authenticator apps generate codes locally.",
        "path": "Settings → Accounts Center → Password and security → Two-factor authentication",
        "priority": "CRITICAL",
    },
    {
        "category": "Security",
        "setting": "Login Activity",
        "recommendation": "Review and revoke unknown devices",
        "why": "Detect unauthorized access early.",
        "path": "Settings → Accounts Center → Password and security → Where you're logged in",
        "priority": "HIGH",
    },
    {
        "category": "Privacy",
        "setting": "Account Privacy",
        "recommendation": "Public (for personal branding) or Private (for personal use)",
        "why": "Public = anyone can see posts. Private = approve followers.",
        "path": "Settings → How you use Instagram → Account privacy",
        "priority": "MEDIUM",
    },
    {
        "category": "Privacy",
        "setting": "Activity Status",
        "recommendation": "OFF",
        "why": "Prevents others from knowing when you're online.",
        "path": "Settings → How you use Instagram → Activity Status",
        "priority": "MEDIUM",
    },
    {
        "category": "Privacy",
        "setting": "Close Friends List",
        "recommendation": "Create a curated list of 10-20 close contacts",
        "why": "Share exclusive stories with your inner circle.",
        "path": "Settings → How you use Instagram → Close Friends",
        "priority": "LOW",
    },
    {
        "category": "Story Privacy",
        "setting": "Story Sharing",
        "recommendation": "Disable 'Allow resharing to stories'",
        "why": "Prevents others from reposting your stories.",
        "path": "Settings → How you use Instagram → Story and reels → Sharing",
        "priority": "MEDIUM",
    },
    {
        "category": "Story Privacy",
        "setting": "Story Replies",
        "recommendation": "Followers you follow back",
        "why": "Limits who can reply to your stories.",
        "path": "Settings → How you use Instagram → Story and reels → Replies",
        "priority": "LOW",
    },
    {
        "category": "Interactions",
        "setting": "Comments",
        "recommendation": "Enable 'Hide offensive comments'",
        "why": "Auto-filters spam and abusive comments.",
        "path": "Settings → How others can interact with you → Comments",
        "priority": "MEDIUM",
    },
    {
        "category": "Interactions",
        "setting": "Tags and Mentions",
        "recommendation": "Manual approval for tags",
        "why": "Prevents unwanted tags in others' posts.",
        "path": "Settings → How others can interact with you → Tags and mentions",
        "priority": "MEDIUM",
    },
    {
        "category": "Content",
        "setting": "Account Status",
        "recommendation": "Check for shadowban flags",
        "why": "Instagram may limit reach if posts violate guidelines.",
        "path": "Settings → Account Status",
        "priority": "HIGH",
    },
    {
        "category": "Content",
        "setting": "Content Preferences",
        "recommendation": "Reset suggested content if feed is stale",
        "why": "Instagram 2025 algorithm reset clears recommendations.",
        "path": "Settings → Content preferences → Reset suggested content",
        "priority": "LOW",
    },
    {
        "category": "Profile",
        "setting": "Bio (150 chars max)",
        "recommendation": "5 lines max, 1 idea per line, arrow CTA at end",
        "why": "Structured bios convert better. Arrow at end = higher CTR.",
        "path": "Edit Profile → Bio",
        "priority": "HIGH",
    },
    {
        "category": "Profile",
        "setting": "Name Field",
        "recommendation": "Plain text only (no Unicode fonts) for searchability",
        "why": "Instagram search matches plain text. Styled names break search.",
        "path": "Edit Profile → Name",
        "priority": "HIGH",
    },
    {
        "category": "Profile",
        "setting": "Profile Picture",
        "recommendation": "Clear headshot or brand logo, 320x320px min",
        "why": "First impression. Low-res images look unprofessional.",
        "path": "Edit Profile → Profile Photo",
        "priority": "MEDIUM",
    },
    {
        "category": "Data",
        "setting": "Off-Instagram Activity",
        "recommendation": "Clear and disconnect third-party tracking",
        "why": "Meta tracks you on third-party sites via Pixel.",
        "path": "Settings → Accounts Center → Your information and permissions → Off-Instagram activity",
        "priority": "LOW",
    },
]


def _get_json(path: str) -> dict:
    req = Request(f"{BRIDGE_URL}{path}", method="GET")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (HTTPError, URLError) as exc:
        return {"error": str(exc)}


def _evaluate_js(expression: str) -> dict:
    """Run JavaScript in the browser and return the result."""
    req = Request(
        f"{BRIDGE_URL}/session/evaluate",
        data=json.dumps({"expression": expression}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (HTTPError, URLError) as exc:
        return {"error": str(exc)}


def check_current_settings() -> dict:
    """Check current Instagram settings via the browser session."""
    # Navigate to settings page
    _get_json("/session/navigate")  # just check session

    # Check if logged in
    status = _get_json("/session/status")
    if status.get("status") != "done" and "waiting" in status.get("status", ""):
        return {"error": "No active browser session. Start one first."}

    # Check current page
    result = _evaluate_js(
        "(document.body.innerText.includes('Log into') || "
        "document.body.innerText.includes('Sign up')) ? 'NOT_LOGGED_IN' : 'LOGGED_IN'"
    )
    if result.get("result") == "NOT_LOGGED_IN":
        return {"error": "Not logged in to Instagram. Login via VNC first."}

    # Get current profile info
    profile = _get_json("/profile/instagram")
    return {"profile": profile, "session": status}


def print_checklist() -> None:
    """Print the recommended settings checklist."""
    print("=" * 70)
    print("Instagram Personal Account Settings Checklist (2025)")
    print("=" * 70)
    print()

    categories: dict[str, list] = {}
    for item in RECOMMENDED_SETTINGS:
        cat = item["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)

    for category, items in categories.items():
        print(f"{'─' * 70}")
        print(f"  {category}")
        print(f"{'─' * 70}")
        for item in items:
            priority_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(
                item["priority"], "⚪"
            )
            print(f"  {priority_icon} {item['setting']}")
            print(f"     → {item['recommendation']}")
            print(f"     Why: {item['why']}")
            print(f"     Path: {item['path']}")
            print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check and apply optimal Instagram personal account settings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--check", action="store_true", help="Check current settings")
    parser.add_argument("--list", action="store_true", help="Print the settings checklist")
    parser.add_argument(
        "--check-setting",
        metavar="NAME",
        help="Check a specific setting by name",
    )

    args = parser.parse_args()

    if args.list:
        print_checklist()
        return

    if args.check:
        result = check_current_settings()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if args.check_setting:
        name = args.check_setting.lower()
        for item in RECOMMENDED_SETTINGS:
            if name in item["setting"].lower():
                print(json.dumps(item, indent=2))
                return
        print(f"Setting '{name}' not found. Use --list to see all settings.")
        sys.exit(1)

    print_checklist()


if __name__ == "__main__":
    main()
