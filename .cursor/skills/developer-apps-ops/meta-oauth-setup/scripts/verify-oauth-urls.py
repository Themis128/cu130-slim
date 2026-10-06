#!/usr/bin/env python3
"""Verify Meta OAuth authorize URLs for Facebook, Instagram, and Threads."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env  # noqa: E402

print("=== Meta OAuth URL Verification ===\n")

fb_redirect = env("FACEBOOK_REDIRECT_URI") or \
    "https://social.cloudless.gr/api/v1/auth/oauth/facebook/callback"
print("Facebook authorize URL:")
print(f"  https://www.facebook.com/dialog/oauth?client_id={env('FACEBOOK_CLIENT_ID') or 'MISSING'}"
      f"&redirect_uri={fb_redirect}"
      "&scope=public_profile,email,pages_show_list,pages_read_engagement,pages_manage_posts"
      "&response_type=code&state=test")
print(f"  client_id: {env('FACEBOOK_CLIENT_ID') or 'MISSING'}")
print(f"  redirect_uri: {fb_redirect}\n")

ig_redirect = env("INSTAGRAM_REDIRECT_URI") or \
    "https://social.cloudless.gr/api/v1/auth/oauth/instagram/callback"
print("Instagram authorize URL (FB Login flow):")
print(f"  https://www.facebook.com/dialog/oauth?client_id={env('INSTAGRAM_CLIENT_ID') or 'MISSING'}"
      f"&redirect_uri={ig_redirect}"
      "&scope=instagram_basic,instagram_content_publish,pages_show_list"
      "&response_type=code&state=test")
print(f"  client_id: {env('INSTAGRAM_CLIENT_ID') or 'MISSING'}")
print(f"  redirect_uri: {ig_redirect}\n")

th_redirect = env("THREADS_REDIRECT_URI") or \
    "https://social.cloudless.gr/api/v1/auth/oauth/threads/callback"
print("Threads authorize URL:")
print(f"  https://threads.net/oauth/authorize?client_id={env('THREADS_CLIENT_ID') or 'MISSING'}"
      f"&redirect_uri={th_redirect}"
      "&scope=threads_basic,threads_content_publish&response_type=code&state=test")
print(f"  client_id: {env('THREADS_CLIENT_ID') or 'MISSING'}")
print(f"  redirect_uri: {th_redirect}\n")

print("=== Checks ===")
for k in ("FACEBOOK_CLIENT_ID", "FACEBOOK_CLIENT_SECRET",
          "INSTAGRAM_CLIENT_ID", "INSTAGRAM_CLIENT_SECRET",
          "THREADS_CLIENT_ID", "THREADS_CLIENT_SECRET"):
    print(f"{'OK' if env(k) else 'WARNING'}: {k} {'set' if env(k) else 'is empty'}")

print("\nNOTE: Open each URL in a browser to verify the OAuth consent screen appears.")
print("      Meta requires HTTPS redirect URIs in production mode.")
