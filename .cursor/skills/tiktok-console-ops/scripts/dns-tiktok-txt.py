#!/usr/bin/env python3
"""List or add TikTok DNS TXT records on cloudless.gr via Cloudflare API.
Usage:
  dns-tiktok-txt.py list
  dns-tiktok-txt.py add 'tiktok-domain-verification=....'"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import cf_request, cf_zone_id, tiktok_txt_records  # noqa: E402
from skill_http import usage  # noqa: E402

action = sys.argv[1] if len(sys.argv) > 1 else "list"
token_value = sys.argv[2] if len(sys.argv) > 2 else ""

zone_id = cf_zone_id()
if not zone_id:
    print(json.dumps({"ok": False, "error": "zone lookup failed"}))
    sys.exit(1)

if action == "list":
    print(json.dumps({"ok": True, "records": tiktok_txt_records(zone_id)}, indent=2))
elif action == "add":
    content = token_value.strip().strip('"')
    if not (content.startswith("tiktok-domain-verification=")
            or content.startswith("tiktok-developers-site-verification=")):
        print(json.dumps({"ok": False, "error": "token must start with "
                          "tiktok-domain-verification= or "
                          "tiktok-developers-site-verification="}))
        sys.exit(1)
    for r in cf_request(f"/zones/{zone_id}/dns_records?type=TXT&per_page=100").get("result", []):
        if r.get("content") == content:
            print(json.dumps({"ok": True, "action": "exists", "id": r["id"]}))
            sys.exit(0)
    created = cf_request(f"/zones/{zone_id}/dns_records", method="POST",
                         data={"type": "TXT", "name": "@", "content": content, "ttl": 120})
    print(json.dumps({"ok": bool(created.get("success")), "action": "created",
                      "id": (created.get("result") or {}).get("id"),
                      "errors": created.get("errors")}, indent=2))
    sys.exit(0 if created.get("success") else 1)
else:
    usage("dns-tiktok-txt.py list|add <token>")
