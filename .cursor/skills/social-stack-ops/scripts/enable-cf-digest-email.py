#!/usr/bin/env python3
"""Wire Cloudflare Email Sending for digests (keeps Slack xoxb untouched).
Usage:
  CLOUDFLARE_EMAIL_API_TOKEN='...' python3 enable-cf-digest-email.py
Token needs: Account -> Email Sending -> Edit
Create at: https://dash.cloudflare.com/profile/api-tokens"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import repo_root  # noqa: E402

token = os.environ.get("CLOUDFLARE_EMAIL_API_TOKEN", "").strip()
if not token:
    print("Set CLOUDFLARE_EMAIL_API_TOKEN env var (Email Sending Edit) "
          "before running.", file=sys.stderr)
    sys.exit(1)

env_path = repo_root() / ".env"
lines = env_path.read_text().splitlines()
out = []
seen = {"EMAIL_PROVIDER": False, "CLOUDFLARE_EMAIL_API_TOKEN": False}
for line in lines:
    if line.startswith("EMAIL_PROVIDER="):
        out.append("EMAIL_PROVIDER=cloudflare")
        seen["EMAIL_PROVIDER"] = True
    elif line.startswith("CLOUDFLARE_EMAIL_API_TOKEN="):
        out.append(f"CLOUDFLARE_EMAIL_API_TOKEN={token}")
        seen["CLOUDFLARE_EMAIL_API_TOKEN"] = True
    else:
        out.append(line)
if not seen["EMAIL_PROVIDER"]:
    out.append("EMAIL_PROVIDER=cloudflare")
if not seen["CLOUDFLARE_EMAIL_API_TOKEN"]:
    out.append(f"CLOUDFLARE_EMAIL_API_TOKEN={token}")
env_path.write_text("\n".join(out) + "\n")
print("updated .env EMAIL_PROVIDER=cloudflare + CLOUDFLARE_EMAIL_API_TOKEN")

env = dict(os.environ)
env.pop("EMAIL_PROVIDER", None)
env["EMAIL_PROVIDER"] = "cloudflare"
subprocess.run(
    ["docker", "compose", "up", "-d", "social-api", "social-worker-publishing",
     "social-worker-media", "social-worker-default", "celery-beat",
     "--force-recreate"],
    cwd=repo_root(), env=env, check=True)
print("Recreated api/workers/beat. Test: POST "
      "/api/v1/ops/daily-digest?post_to_slack=false&post_to_email=true")
