#!/usr/bin/env python3
"""Generate a batch of emojis/icons from a file of concepts (one per line).

Usage: generate-batch.py CONCEPTS_FILE [STYLE] [SIZE] [OUTPUT_DIR]

Example:
  generate-batch.py concepts.txt flat 512 ./emojis"""

import base64
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, api_login, env, request, usage  # noqa: E402

concepts_file = sys.argv[1] if len(sys.argv) > 1 else \
    usage("generate-batch.py CONCEPTS_FILE [STYLE] [SIZE] [OUTPUT_DIR]")
style = sys.argv[2] if len(sys.argv) > 2 else "flat"
size = int(sys.argv[3]) if len(sys.argv) > 3 else 512
output_dir = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("./emojis")
output_dir.mkdir(parents=True, exist_ok=True)

api = api_base("SOCIAL_API_URL", "http://localhost:8083")
email = env("SOCIAL_ADMIN_EMAIL", "admin@cloudless.gr")
password = env("SOCIAL_ADMIN_PASSWORD")
if not password:
    print("SOCIAL_ADMIN_PASSWORD not set in .env", file=sys.stderr)
    sys.exit(1)

token = api_login(api, email, password)

# Switch team
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

concepts = [ln.strip() for ln in Path(concepts_file).read_text().splitlines()
            if ln.strip()]
print(f"→ Generating {len(concepts)} emojis ({style}, {size}px)...")

d = request("POST", f"{api}/api/v1/ai/emoji/batch", token=token, data={
    "concepts": concepts,
    "style": style,
    "size": size,
    "background": "transparent",
    "steps": 20,
    "cfg_scale": 8.0,
    "provider": "local-diffusers",
    "enhance_prompt": True,
    "remove_bg": True,
}, timeout=600)

emojis = d.get("emojis", [])
success = 0
for e in emojis:
    c = e.get("concept", "unknown")
    safe = c.replace(" ", "-").replace("/", "-")
    if e.get("success"):
        img = base64.b64decode(e["image_base64"])
        path = output_dir / f"{safe}.png"
        path.write_bytes(img)
        print(f"  ✓ {c} → {path} ({len(img)} bytes)")
        success += 1
    else:
        print(f'  ✗ {c} — {e.get("error", "unknown error")}')
print(f"\n{success}/{len(emojis)} emojis generated successfully")
