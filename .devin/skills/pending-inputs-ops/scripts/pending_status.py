#!/usr/bin/env python3
"""pending_status.py — one-line status of every remaining human input."""
import os, subprocess, sys
from pathlib import Path

def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()

twit = sh("docker compose exec -T social-postgres psql -U social_user -d social_automation -tc \"SELECT status FROM social_accounts WHERE platform='twitter' LIMIT 1\"")
li = sh("docker compose exec -T social-postgres psql -U social_user -d social_automation -tc \"SELECT status FROM social_accounts WHERE platform='linkedin' AND account_id='cloudless-gr' LIMIT 1\"")
csv = "FOUND" if any((Path(d) / f).exists() for d in ["/mnt/c/Users/tbaltzakis/Downloads"] for f in ["Connections.csv"]) else "absent"
recips = sh("docker compose exec -T social-postgres psql -U social_user -d social_automation -tc \"SELECT count(*) FROM email_recipients WHERE campaign_id='9f98e2ce-24da-46f3-a19f-8d7e0e875270'\"")

print(f"twitter:        {twit or '?'}  → click reauth_urls.py URL")
print(f"linkedin org:   {li or '?'}  → re-auth once for CM scopes")
print(f"whatsapp:       rate-limited   → wa_verify.py --daemon retries hourly")
print(f"Connections.csv: {csv}  → audience_watch --daemon auto-attaches")
print(f"recipients:     {recips or '0'} queued")
