#!/usr/bin/env python3
"""Cross-reference OAuth scopes/permissions SocialAuto requests vs code usage.

Parses PLATFORM_SCOPES + client base_scopes from app/api/auth.py, then counts
references to each scope in the backend (literal scope name — the codebase
documents required permissions in docstrings — plus per-scope endpoint/method
hint patterns). Scopes with zero usage hits are prune candidates for the
developer-app submission — always confirm with the platform console's real
API-call counts before removing anything.

Usage (from repo root):
    python3 .devin/skills/developer-apps-ops/scripts/audit_scope_usage.py
    python3 .devin/skills/developer-apps-ops/scripts/audit_scope_usage.py --verbose
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "social-automation" / "backend"
AUTH = BACKEND / "app" / "api" / "auth.py"

# scope -> extra regex hints (endpoint paths / methods) beyond the literal name
HINTS: dict[str, list[str]] = {
    # Meta (Facebook Login family)
    "pages_messaging": [r"send_api|/conversations|messenger_profile|/messages\b"],
    "pages_manage_posts": [r"feed.*POST|/photos|page_token.*post|publish.*page"],
    "pages_manage_engagement": [r"/comments|/likes|/reactions"],
    "pages_manage_metadata": [r"subscribed_apps|/settings|/subscriptions"],
    "pages_read_engagement": [r"/posts|/feed|follower"],
    "pages_read_user_content": [r"visitor|/ratings|user.*content"],
    "pages_show_list": [r"me/accounts"],
    "read_insights": [r"/insights"],
    "instagram_basic": [r"instagram_business_account|ig_user|/media\b"],
    "instagram_content_publish": [r"media_publish|/media\?|ig_container"],
    "instagram_manage_messages": [r"instagram.*message|ig.*dm|/conversations"],
    "instagram_manage_insights": [r"/insights.*ig|ig.*insights"],
    "instagram_manage_comments": [r"/comments|hide_comment|reply.*comment"],
    "business_management": [r"businesses|business_manager|owned_"],
    "ads_read": [r"/ads|ad_accounts|insights.*ad"],
    "ads_management": [r"/ads|ad_accounts|campaigns"],
    "whatsapp_business_messaging": [r"whatsapp|waba|phone_numbers"],
    "whatsapp_business_management": [r"whatsapp|waba"],
    "pages_utility_messaging": [r"message_templates|utility"],
    "Human Agent": [r"human_agent"],
    # Instagram Business Login family
    "instagram_business_basic": [r"api\.instagram\.com|business_login|ig_business_id"],
    "instagram_business_manage_messages": [r"instagram_messenger|ig.*dm|business_login"],
    "instagram_business_content_publish": [r"media_publish|ig.*publish|business_login"],
    "instagram_business_manage_comments": [r"/comments|ig.*comment"],
    "instagram_business_manage_insights": [r"/insights|ig.*insight"],
    # Threads
    "threads_basic": [r"threads\.net|threads_api|/me/threads"],
    "threads_content_publish": [r"threads.*publish|/threads\?|media_container"],
    "threads_manage_insights": [r"threads.*insights"],
    "threads_manage_replies": [r"threads.*replies|/replies|reply_control"],
    # TikTok
    "user.info.basic": [r"user/info|tiktok.*user"],
    "user.info.profile": [r"user/info|profile"],
    "user.info.stats": [r"user/info|stats"],
    "video.publish": [r"video/upload|video/init|publish/init|DIRECT_POST|content/post"],
    "video.upload": [r"video/upload|FILE_UPLOAD|MEDIA_UPLOAD|inbox"],
    "video.list": [r"video/list"],
    # LinkedIn
    "w_member_social": [r"ugcPosts|/posts|personal.*post"],
    "w_organization_social": [r"organization.*post|ugcPosts.*organization|company"],
    "r_organization_social": [r"organizationalEntity|organization.*stats|follower"],
    "r_organization_admin": [r"organizationAcls|/organizations\?|administered"],
    "rw_organization_admin": [r"organizationAcls"],
    # Twitter/X
    "tweet.read": [r"/2/tweets|tweets/"],
    "tweet.write": [r"/2/tweets|media/upload"],
    "users.read": [r"/2/users|users/me"],
    "offline.access": [r"refresh_token"],
    "dm.read": [r"dm_events|/dm"],
    "dm.write": [r"dm_events|/dm"],
    "media.write": [r"media/upload"],
}


def requested_scopes() -> dict[str, list[str]]:
    """Parse PLATFORM_SCOPES dict + client base_scopes lists from auth.py."""
    src = AUTH.read_text()
    out: dict[str, list[str]] = {}
    # PLATFORM_SCOPES = {"platform": [..], ...}
    m = re.search(r"PLATFORM_SCOPES[^=]*=\s*\{(.*?)\n\s*\}", src, re.S)
    if m:
        for pm in re.finditer(r'"(\w+)":\s*\[(.*?)\]', m.group(1), re.S):
            out[pm.group(1)] = re.findall(r'"([^"]+)"', pm.group(2))
    # base_scopes=[...] with name="x"
    for cm in re.finditer(r'base_scopes=\[(.*?)\](?:.*?name="(\w+)")?', src, re.S):
        name = cm.group(2) or "?"
        out.setdefault(name, [])
        for s in re.findall(r'"([^"]+)"', cm.group(1)):
            if s not in out[name]:
                out[name].append(s)
    return out


def code_refs(pattern: str, exclude_auth_scopes: bool) -> list[str]:
    hits: list[str] = []
    rx = re.compile(pattern, re.I)
    for f in BACKEND.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        text = f.read_text(errors="replace")
        matches = [i + 1 for i, line in enumerate(text.splitlines()) if rx.search(line)]
        if matches:
            rel = f.relative_to(BACKEND)
            hits.append(f"{rel}:{','.join(map(str, matches[:5]))}")
    return hits


def main() -> None:
    verbose = "--verbose" in sys.argv
    scopes = requested_scopes()
    prune_candidates: list[str] = []
    for platform in sorted(scopes):
        print(f"\n=== {platform} ({len(scopes[platform])} scopes requested) ===")
        for scope in scopes[platform]:
            pats = [re.escape(scope)] + HINTS.get(scope, [])
            hits: list[str] = []
            seen: set[str] = set()
            for pat in pats:
                for h in code_refs(pat, True):
                    if h not in seen:
                        seen.add(h)
                        hits.append(h)
            tag = "" if hits else "  <-- NO CODE REFS"
            print(f"  {scope:45} {len(hits):3} file(s){tag}")
            if verbose and hits:
                for h in hits[:8]:
                    print(f"      {h}")
            if not hits:
                prune_candidates.append(f"{platform}:{scope}")
    print(f"\n=== prune candidates (verify console API-call counts first) ===")
    for c in prune_candidates:
        print(f"  {c}")
    if not prune_candidates:
        print("  none")


if __name__ == "__main__":
    main()
