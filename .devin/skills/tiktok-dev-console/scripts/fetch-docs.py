#!/usr/bin/env python3
"""Fetch the latest TikTok Content Posting API documentation pages.
Useful for checking error codes, API changes, and new features.
Usage: fetch-docs.py"""

import urllib.request
from html.parser import HTMLParser


class _TextOnly(HTMLParser):
    """Collect visible text nodes from an HTML document.

    Uses Python's stdlib ``HTMLParser`` (a proper tokeniser) — NOT regex —
    so there is no risk of a "bad HTML filtering regexp" bypass.  Script,
    style, noscript and template elements are skipped entirely.
    ``convert_charrefs=True`` handles entity decoding at parse time.
    """

    _SKIP = frozenset({"script", "style", "noscript", "template"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def get_text(self) -> str:
        """Return collapsed, whitespace-normalised plain text."""
        words = " ".join(self.parts).split()
        return " ".join(words[:600])


def _extract_text(raw_html: str) -> str:
    """Return visible text from *raw_html* using a proper HTML parser."""
    parser = _TextOnly()
    parser.feed(raw_html)
    words = " ".join(parser.parts).split()
    return " ".join(words[:600])


print("=== TikTok Content Posting API — Media Transfer Guide ===\n")
try:
    req = urllib.request.Request(
        "https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide",
        headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode(errors="replace")
    text = _extract_text(raw)
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
