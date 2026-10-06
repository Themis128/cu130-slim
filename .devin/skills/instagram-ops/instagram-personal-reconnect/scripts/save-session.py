#!/usr/bin/env python3
"""Save the instagrapi session to SocialAuto's database so it persists
across restarts and is available to the polling/publishing tasks.

Usage: save-session.py <account_id> <session_id>"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import psql, sidecar_get  # noqa: E402
from skill_http import usage  # noqa: E402

if len(sys.argv) < 3:
    usage("save-session.py <account_id> <session_id>")
account_id, session_id = sys.argv[1], sys.argv[2]

raw = sidecar_get("/auth/settings", session_id=session_id)
try:
    d = json.loads(raw)
    settings_json = json.dumps(d.get("settings", d) if isinstance(d, dict) else d)
except ValueError:
    settings_json = "{}"

# Escape single quotes for SQL literal
sj = settings_json.replace("'", "''")
psql(f"""UPDATE social_accounts
SET meta_data = meta_data || jsonb_build_object(
  'private_api_session_id', '{session_id}',
  'private_api_settings', '{sj}'::jsonb,
  'private_api_connected_at', now()::text
)
WHERE id = '{account_id}';""")

print(f"✅ Session saved to SocialAuto for account {account_id}", file=sys.stderr)
print(f"  session_id: {session_id[:8]}...", file=sys.stderr)
print(f"  settings: {len(settings_json)} bytes", file=sys.stderr)
