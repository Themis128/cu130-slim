#!/usr/bin/env python3
"""List all permissions in the Meta App Review submission and their status.
Usage: list-permissions.py [app_id] [access_token]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "meta-account-restriction/scripts"))
from _common import fb_token, graph, show  # noqa: E402

app_id = sys.argv[1] if len(sys.argv) > 1 else "1936126137016578"
token = fb_token(sys.argv[2] if len(sys.argv) > 2 else "")

print(f"Listing permissions for app {app_id}...\n")
show(graph(f"/{app_id}/permissions", {"access_token": token}))

DONE = ["pages_show_list", "pages_manage_metadata", "pages_messaging",
        "business_management", "pages_read_engagement",
        "instagram_business_basic", "instagram_business_manage_messages",
        "pages_read_user_content", "pages_manage_posts",
        "pages_manage_engagement", "pages_utility_messaging"]
TODO = ["instagram_business_content_publish", "instagram_manage_comments",
        "instagram_business_manage_insights", "threads_basic", "read_insights"]

print("\n=== Permission status reference ===\n")
print("Completed (allowed-usage saved):")
for p in DONE:
    print(f"  [x] {p}")
print("\nRemaining (allowed-usage not yet saved):")
for p in TODO:
    print(f"  [ ] {p}")
