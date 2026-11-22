#!/usr/bin/env python3
"""viber_token_fetch.py — validate + store the Viber Business bot token.

NOTE (2026-11-08): partners.viber.com / webadmin.viber.com are RETIRED —
the old CDP scrape path is dead. New bots are commercial-only (Rakuten
Viber application); once a token is issued, use --token or paste it.

Usage:
  viber_token_fetch.py --token <TOKEN>   # validate + store
  viber_token_fetch.py                   # prompt for token (interactive)
"""
import argparse
import json
import os
import re
import sys
import urllib.request

ENV_PATH = r"C:\Users\tbaltzakis\cu130-slim\.env"


def validate(token):
    req = urllib.request.Request(
        "https://chatapi.viber.com/pa/get_account_info",
        data=b"{}", headers={"X-Viber-Auth-Token": token,
                             "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=15))
    return d.get("status") == 0, d


def store(token):
    line = f"VIBER_AUTH_TOKEN={token}\n"
    if os.path.exists(ENV_PATH):
        s = open(ENV_PATH, encoding="utf-8", errors="replace").read()
        s = re.sub(r"^VIBER_AUTH_TOKEN=.*", line.strip(), s, flags=re.M) \
            if "VIBER_AUTH_TOKEN=" in s else s + "\n" + line
        open(ENV_PATH, "w", encoding="utf-8").write(s)
    print("VIBER_AUTH_TOKEN stored")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("VIBER_AUTH_TOKEN"))
    a = ap.parse_args()

    token = (a.token or "").strip()
    if not token:
        print("partners.viber.com is retired — paste the bot token issued "
              "by Rakuten Viber / your Viber partner:")
        try:
            token = input("token> ").strip()
        except EOFError:
            print("no token provided (non-interactive)", file=sys.stderr)
            sys.exit(2)
    if not token:
        sys.exit(2)
    ok, info = validate(token)
    if ok:
        store(token)
        print("bot:", info.get("name"), "| subs:", info.get("subscribers_count"))
    else:
        print("invalid token:", info)


if __name__ == "__main__":
    main()
