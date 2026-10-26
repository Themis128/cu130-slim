#!/usr/bin/env python3
"""Wire X OAuth creds into the SocialAuto stack.

Usage: python wire.py <client_id> <client_secret>
Writes .env → restarts social-api → prints the authorize URL hint.
The creds already landed once via this path (token live @TBaltzakis).
"""
import re, subprocess, sys, time

ENV = r"C:\Users\tbaltzakis\cu130-slim\.env"

def run(*a): return subprocess.run(a, capture_output=True, text=True)

cid, secret = sys.argv[1], sys.argv[2]
env = open(ENV, encoding="utf-8").read()
for k, v in (("TWITTER_CLIENT_ID", cid), ("TWITTER_CLIENT_SECRET", secret)):
    if f"{k}=" in env:
        env = re.sub(rf"{k}=.*", f"{k}={v}", env)
    else:
        env += f"\n{k}={v}"
open(ENV, "w", encoding="utf-8").write(env)
print("env written")

run("docker", "compose", "restart", "social-api")
print("restarting social-api…")
time.sleep(20)
h = run("docker", "ps", "--filter", "name=social-api", "--format", "{{.Status}}")
print(h.stdout.strip() or h.stderr.strip())
print("then: app → Accounts → Connect X, or the OAuth authorize endpoint with a user JWT + team_id")
