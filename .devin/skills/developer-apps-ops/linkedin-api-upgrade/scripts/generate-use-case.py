#!/usr/bin/env python3
"""Generate the LinkedIn API business use case description from the codebase.
Outputs a formatted description of how SocialAuto uses the LinkedIn API.
Usage: generate-use-case.py"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

ROOT = repo_root()

print("=== LinkedIn API Business Use Case ===\n")

print("--- Configured LinkedIn OAuth Scopes ---")
auth_py = (ROOT / "social-automation/backend/app/api/auth.py").read_text()
lines = auth_py.splitlines()
for i, line in enumerate(lines):
    if "LINKEDIN_SCOPES" in line:
        print("\n".join(lines[i:i + 10]))
        break
print()

print("--- LinkedIn API Client Methods ---")
client_py = ROOT / "social-automation/backend/app/services/linkedin_api.py"
count = 0
for line in client_py.read_text().splitlines():
    if re.search(r"\basync def |\bdef ", line) and "__" not in line:
        print(line.strip())
        count += 1
        if count >= 20:
            break
print()

print("--- LinkedIn API Endpoints ---")
routes_py = ROOT / "social-automation/backend/app/api/linkedin.py"
count = 0
for line in routes_py.read_text().splitlines():
    if re.search(r"@router\.(get|post|put|delete)", line):
        print(line.strip())
        count += 1
        if count >= 15:
            break
print()

print("""--- LinkedIn Products Used ---
1. Share on LinkedIn (w_member_social)
   - Create organic posts on personal profiles
   - Create multi-image posts
   - Create article posts
   - Delete posts

2. Community Management API (w_organization_social, r_organization_social)
   - Create organic posts on Company Pages
   - Read post analytics (impressions, clicks, engagement)
   - Read organization stats (follower counts, lifetime analytics)

3. Organizations API (r_organization_admin)
   - Discover Company Pages the member administers
   - Read organization profile data

NOT USED:
- No LinkedIn Ads (rw_ads, r_ads)
- No Campaign Manager integration
- No sponsored content creation""")
