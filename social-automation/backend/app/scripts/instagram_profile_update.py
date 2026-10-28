#!/usr/bin/env python3
"""Update an Instagram profile via the browser-novnc bridge.

Uses natural navigation (profile page → click "Edit profile") to avoid
Instagram's __coig_login redirect guard that blocks direct navigation
to /accounts/edit/.

Usage:
    # Update bio only
    python instagram_profile_update.py --username t_baltzakis --bio "🚀 Founder @ Cloudless"

    # Update bio + website (website only works for business/creator accounts)
    python instagram_profile_update.py --username t_baltzakis \\
        --bio "🚀 Founder @ Cloudless" \\
        --website "https://cloudless.gr"

    # Update full name
    python instagram_profile_update.py --username t_baltzakis --full-name "Themistoklis Baltzakis"

    # Read current profile
    python instagram_profile_update.py --username t_baltzakis --read

    # Verify a bio was saved (re-read after update)
    python instagram_profile_update.py --username t_baltzakis --verify-bio "🚀 Founder"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BRIDGE_URL = "http://localhost:9223"


def _post_json(path: str, data: dict) -> dict:
    """POST JSON to the browser bridge and return the response."""
    req = Request(
        f"{BRIDGE_URL}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        print(f"ERROR {exc.code}: {body}", file=sys.stderr)
        raise SystemExit(1) from None
    except URLError as exc:
        print(f"Connection error: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


def _patch_json(path: str, data: dict) -> dict:
    """PATCH JSON to the browser bridge and return the response."""
    req = Request(
        f"{BRIDGE_URL}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        print(f"ERROR {exc.code}: {body}", file=sys.stderr)
        raise SystemExit(1) from None
    except URLError as exc:
        print(f"Connection error: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


def _get_json(path: str) -> dict:
    """GET JSON from the browser bridge."""
    req = Request(f"{BRIDGE_URL}{path}", method="GET")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        print(f"ERROR {exc.code}: {body}", file=sys.stderr)
        raise SystemExit(1) from None
    except URLError as exc:
        print(f"Connection error: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


def check_session() -> dict:
    """Check if the browser bridge has an active session."""
    return _get_json("/session/status")


def read_profile() -> dict:
    """Read the current Instagram profile from the browser session."""
    return _get_json("/profile/instagram")


def update_profile(
    bio: str | None = None,
    full_name: str | None = None,
    website: str | None = None,
) -> dict:
    """Update the Instagram profile via the browser bridge.

    Uses the PATCH /profile/instagram endpoint which navigates naturally
    (profile page → click "Edit profile") to avoid __coig_login redirects.
    """
    payload: dict = {}
    if bio is not None:
        payload["biography"] = bio
    if full_name is not None:
        payload["full_name"] = full_name
    if website is not None:
        payload["external_url"] = website

    if not payload:
        print("No fields to update — specify --bio, --full-name, or --website")
        sys.exit(1)

    print(f"Updating profile with fields: {list(payload.keys())}")
    result = _patch_json("/profile/instagram", payload)
    print(f"Result: {json.dumps(result, indent=2)}")
    return result


def verify_bio(expected: str) -> bool:
    """Verify that the bio was saved by re-reading the profile."""
    print("Waiting 5s for Instagram to persist changes...")
    time.sleep(5)
    profile = read_profile()
    actual_bio = profile.get("biography", "")
    if expected in actual_bio:
        print(f"✅ Bio verified: contains '{expected}'")
        print(f"Full bio: {actual_bio}")
        return True
    else:
        print("❌ Bio verification failed")
        print(f"Expected to contain: {expected}")
        print(f"Actual: {actual_bio}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update Instagram profile via browser-novnc bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--username",
        help="Instagram username (for reference — session must be active)",
    )
    parser.add_argument("--bio", help="New biography text")
    parser.add_argument("--full-name", help="New display name")
    parser.add_argument("--website", help="New external URL (business/creator only)")
    parser.add_argument(
        "--read",
        action="store_true",
        help="Read and print the current profile",
    )
    parser.add_argument(
        "--verify-bio",
        metavar="EXPECTED",
        help="Verify that the current bio contains this text",
    )
    parser.add_argument(
        "--check-session",
        action="store_true",
        help="Check if the browser bridge has an active session",
    )

    args = parser.parse_args()

    if args.check_session:
        status = check_session()
        print(json.dumps(status, indent=2))
        return

    if args.verify_bio:
        success = verify_bio(args.verify_bio)
        sys.exit(0 if success else 1)

    if args.read:
        profile = read_profile()
        print(json.dumps(profile, indent=2, ensure_ascii=False))
        return

    if args.bio or args.full_name or args.website:
        update_profile(bio=args.bio, full_name=args.full_name, website=args.website)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
