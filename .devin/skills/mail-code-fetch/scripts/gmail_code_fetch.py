#!/usr/bin/env python3
"""gmail_code_fetch.py — poll baltzakis.themis@gmail.com for the newest
one-time code from a sender. Refreshes the OAuth token in
%APPDATA%\\devin\\secrets\\gmail-token.json.

Usage:
  gmail_code_fetch.py --from security@facebookmail.com --pattern "(\\d{6}) is your"
                      [--exclude 911250,019400] [--timeout 120]
Prints the code (first group of --pattern matched against Subject).
"""
import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request

SECRETS = os.path.expandvars(
    r"C:\Users\tbaltzakis\AppData\Roaming\devin\secrets")
TOKEN_PATH = os.path.join(SECRETS, "gmail-token.json")
KEYS_PATH = os.path.join(SECRETS, "gcp-oauth.keys.json")


def access_token():
    tok = json.load(open(TOKEN_PATH))
    keys = json.load(open(KEYS_PATH))
    c = keys.get("installed", keys.get("web", {}))
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "refresh_token": tok["refresh_token"],
            "grant_type": "refresh_token"}).encode())
    r = json.load(urllib.request.urlopen(req, timeout=15))
    tok["access_token"] = r["access_token"]
    json.dump(tok, open(TOKEN_PATH, "w"))
    return r["access_token"]


def fetch(sender, pattern, exclude, timeout):
    at = access_token()
    pat = re.compile(pattern)
    deadline = time.time() + timeout
    while time.time() < deadline:
        q = f"from:{sender} newer_than:15m"
        u = ("https://gmail.googleapis.com/gmail/v1/users/me/messages?"
             + urllib.parse.urlencode({"q": q, "maxResults": 5}))
        req = urllib.request.Request(u, headers={"Authorization": f"Bearer {at}"})
        msgs = json.load(urllib.request.urlopen(req, timeout=15)).get("messages", [])
        for m in msgs:
            u2 = (f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}"
                  "?format=metadata&metadataHeaders=Subject")
            d = json.load(urllib.request.urlopen(urllib.request.Request(
                u2, headers={"Authorization": f"Bearer {at}"}), timeout=15))
            subj = next((h["value"] for h in d["payload"]["headers"]
                         if h["name"] == "Subject"), "")
            g = pat.search(subj)
            if g and g.group(1) not in exclude:
                return g.group(1)
        time.sleep(6)
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="sender", required=True)
    ap.add_argument("--pattern", default=r"(\d{6})")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()
    c = fetch(a.sender, a.pattern, set(a.exclude.split(",")) - {""}, a.timeout)
    print(c if c else "NONE")
