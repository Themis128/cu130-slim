#!/usr/bin/env python3
"""Update Messenger Profile properties.
Usage: update-profile.py [account_id] --greeting "text" | --menu 'json' | --domains '["url"]' | --ice-breakers 'json'"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api, usage  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("account_id", nargs="?", default="3f2f59c4-f190-44ad-aefe-4321af08ef89")
p.add_argument("--greeting")
p.add_argument("--menu")
p.add_argument("--domains")
p.add_argument("--ice-breakers")
a = p.parse_args()

d = {}
if a.greeting:
    d["greeting"] = [{"locale": "default", "text": a.greeting}]
    d["get_started"] = {"payload": "GET_STARTED"}
if a.menu:
    d["persistent_menu"] = json.loads(a.menu)
if a.domains:
    d["whitelisted_domains"] = json.loads(a.domains)
if a.ice_breakers:
    d["ice_breakers"] = json.loads(a.ice_breakers)
if not d:
    usage("update-profile.py [account_id] --greeting 'text' | --menu 'json' | "
          "--domains 'json' | --ice-breakers 'json'")

api, token = social_api()
print("Updating Messenger Profile...")
r = request("PUT", f"{api}/api/v1/messenger/{a.account_id}/profile", token=token, data=d)
print(json.dumps(r, indent=2, ensure_ascii=False))
