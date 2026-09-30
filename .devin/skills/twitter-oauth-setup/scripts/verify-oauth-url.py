#!/usr/bin/env python3
"""Verify Twitter/X OAuth authorize URL.
Usage: verify-oauth-url.py"""

import base64
import hashlib
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env  # noqa: E402

print("=== Twitter/X OAuth URL Verification ===\n")

redirect = env("TWITTER_REDIRECT_URI",
               "https://social.cloudless.gr/api/v1/auth/oauth/twitter/callback")
code_verifier = secrets.token_urlsafe(64)[:128]
challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
client_id = env("TWITTER_CLIENT_ID", "MISSING")

url = ("https://twitter.com/i/oauth2/authorize"
       f"?client_id={client_id}"
       f"&redirect_uri={redirect}"
       "&response_type=code"
       "&scope=tweet.read tweet.write users.read offline.access"
       f"&code_challenge={challenge}"
       "&code_challenge_method=S256&state=test")

print("Twitter authorize URL:")
print(f"  {url}")
print(f"  client_id: {client_id}")
print(f"  redirect_uri: {redirect}")
print(f"  code_challenge: {challenge[:20]}...")
print(f"  code_verifier: {code_verifier[:20]}...\n")

print("=== Checks ===")
for var in ("TWITTER_CLIENT_ID", "TWITTER_CLIENT_SECRET", "TWITTER_REDIRECT_URI"):
    print(f"{'WARNING: ' + var + ' is empty' if not env(var) else 'OK: ' + var + ' set'}")

print("""
NOTE: Open the URL in a browser to verify the OAuth consent screen appears.
      Twitter requires HTTPS redirect URIs.""")
