#!/usr/bin/env python3
"""Generate a single emoji/icon via the SocialAuto API.

Usage: generate-emoji.py CONCEPT [STYLE] [SIZE] [BACKGROUND] [OUTPUT]

Examples:
  generate-emoji.py "happy cloud" kawaii 512 transparent
  generate-emoji.py "fire rocket" flat 256 white rocket.png
  generate-emoji.py "thumbs up" 3d 512 transparent"""

import base64
import sys
import urllib.error
import urllib.request
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, env, request, usage  # noqa: E402

concept = sys.argv[1] if len(sys.argv) > 1 else \
    usage("generate-emoji.py CONCEPT [STYLE] [SIZE] [BACKGROUND] [OUTPUT]")
style = sys.argv[2] if len(sys.argv) > 2 else "flat"
size = int(sys.argv[3]) if len(sys.argv) > 3 else 512
background = sys.argv[4] if len(sys.argv) > 4 else "transparent"
output = sys.argv[5] if len(sys.argv) > 5 else f"emoji-{concept.replace(' ', '-')}.png"

api = api_base("SOCIAL_API_URL", "http://localhost:8083")
email = env("SOCIAL_ADMIN_EMAIL", "admin@cloudless.gr")
password = env("SOCIAL_ADMIN_PASSWORD")
if not password:
    print("SOCIAL_ADMIN_PASSWORD not set in .env", file=sys.stderr)
    sys.exit(1)

print(f"→ Logging in as {email}...")
token = api_login(api, email, password)

# Switch to the first team that has accounts
try:
    req = urllib.request.Request(f"{api}/api/v1/teams",
                                 headers={"Authorization": f"Bearer {token}"})
    teams = json.loads(urllib.request.urlopen(req, timeout=15).read())
except Exception:
    teams = []
if teams:
    try:
        resp = request("POST", f"{api}/api/v1/auth/switch-team",
                       token=token, data={"team_id": teams[0]["id"]})
        token = resp.get("access_token", token)
    except SystemExit:
        pass

print(f"→ Generating emoji: '{concept}' ({style}, {size}px, {background} bg)...")
d = request("POST", f"{api}/api/v1/ai/emoji/generate", token=token, data={
    "concept": concept,
    "style": style,
    "size": size,
    "background": background,
    "steps": 20,
    "cfg_scale": 8.0,
    "provider": "local-diffusers",
    "enhance_prompt": True,
    "remove_bg": background == "transparent",
})

img = base64.b64decode(d["image_base64"])
Path(output).write_bytes(img)
print(f"✓ Saved {len(img)} bytes to {output}")
print(f'  Provider: {d.get("provider", "?")}')
print(f'  Enhanced prompt: {d.get("enhanced_prompt", "?")[:100]}')
qc = d.get("quality_check")
if qc:
    print(f'  Quality: {qc.get("score", "?")}/10 - {qc.get("issues", "")[:80]}')
