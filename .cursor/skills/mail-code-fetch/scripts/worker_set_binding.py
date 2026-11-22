#!/usr/bin/env python3
"""Safely change ONE Cloudflare Worker env binding (mail-ingest worker).

NEVER PATCH /settings with a partial bindings list — the API REPLACES the
whole array. Doing that once wiped MAIL_INGEST_URL/FALLBACK_FORWARD/etc and
every inbound mail bounced 'Mailbox ingest not configured' (2026-11-05).

Usage: CF_TOKEN=... python worker_set_binding.py SPAM_REJECT_AT 9
"""
import json, os, sys, urllib.request

ACCT = "fb7dc7b69b662480cd5961a4d1913c78"
WORKER = "mail-ingest"
TOKEN = os.environ["CF_TOKEN"]
name, value = sys.argv[1], sys.argv[2]

def cf(method, path, body=None):
    r = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{ACCT}/workers/scripts/{WORKER}{path}",
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=20).read())

cur = cf("GET", "/settings")["result"]["bindings"]
found = False
for b in cur:
    if b.get("name") == name:
        b["text"] = value; found = True
if not found:
    cur.append({"type": "plain_text", "name": name, "text": value})
cf("PUT", "/settings", {"bindings": cur})
print(f"{name}={value} set; {len(cur)} bindings preserved")
