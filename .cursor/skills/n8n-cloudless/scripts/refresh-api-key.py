#!/usr/bin/env python3
"""Mint / refresh N8N_API_KEY using scripts/init-n8n-api-key.py"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import die, load_env, repo_root  # noqa: E402

root = repo_root()
script = root / "scripts" / "init-n8n-api-key.py"
if not script.is_file():
    die("Missing scripts/init-n8n-api-key.py")

e = load_env()
os.environ.setdefault("N8N_URL", "http://127.0.0.1:5678")
os.environ["N8N_USER"] = e.get("N8N_BASIC_AUTH_USER") or e.get("N8N_USER", "")
os.environ["N8N_PASSWORD"] = e.get("N8N_BASIC_AUTH_PASSWORD") or e.get("N8N_PASSWORD", "")
for k, v in e.items():
    os.environ.setdefault(k, v)

print("Refreshing n8n API key (details not printed)...")
subprocess.run([sys.executable, str(script)], check=True)
print("Done. Restart social-api if deploy-via-API still 401: docker compose restart social-api")
