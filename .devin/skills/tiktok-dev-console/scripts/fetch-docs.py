#!/usr/bin/env python3
"""Fetch the latest TikTok Content Posting API documentation pages.
Useful for checking error codes, API changes, and new features.
Usage: fetch-docs.py"""

import html
import re
import urllib.request

print("=== TikTok Content Posting API — Media Transfer Guide ===\n")
try:
    req = urllib.request.Request(
        "https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide",
        headers={"User-Agent": "Mozilla/5.0"})
    text = urllib.request.urlopen(req, timeout=30).read().decode(errors="replace")
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    print(text[:3000])
except Exception as e:
    print(f"(fetch failed: {e})")

print("""
=== Error Codes Reference ===

Common TikTok Content Posting API errors:

  400 invalid_params                        — Check error message for details
  403 spam_risk_too_many_pending_share      — 5+ pending uploads in 24h
  403 spam_risk_user_banned_from_posting    — User banned from posting
  403 spam_risk_too_many_posts              — Daily post cap reached
  403 url_ownership_unverified              — Domain not verified for PULL_FROM_URL
  403 unaudited_client_can_only_post_to_private_accounts — DIRECT_POST needs audit
  403 reached_active_user_cap               — Daily quota for active users reached
  401 access_token_invalid                  — Token expired (24h lifetime)
  401 scope_not_authorized                  — Missing video.upload or video.publish scope
  429 rate_limit_exceeded                   — API rate limit exceeded

Full docs:
  https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
  https://developers.tiktok.com/doc/content-posting-api-reference-upload-video""")
