#!/usr/bin/env bash
# Fetch the latest TikTok Content Posting API documentation pages.
# Useful for checking error codes, API changes, and new features.
set -euo pipefail

echo "=== TikTok Content Posting API — Media Transfer Guide ==="
echo ""
curl -s "https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide" \
  | python3 -c "
import sys, re, html
text = sys.stdin.read()
# Extract text content from the main doc section
# Strip HTML tags
text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
text = re.sub(r'<[^>]+>', ' ', text)
text = html.unescape(text)
# Collapse whitespace
text = re.sub(r'\s+', ' ', text).strip()
# Print first 3000 chars
print(text[:3000])
" 2>&1 | head -80

echo ""
echo "=== Error Codes Reference ==="
echo ""
echo "Common TikTok Content Posting API errors:"
echo ""
echo "  400 invalid_params                        — Check error message for details"
echo "  403 spam_risk_too_many_pending_share      — 5+ pending uploads in 24h"
echo "  403 spam_risk_user_banned_from_posting    — User banned from posting"
echo "  403 spam_risk_too_many_posts              — Daily post cap reached"
echo "  403 url_ownership_unverified              — Domain not verified for PULL_FROM_URL"
echo "  403 unaudited_client_can_only_post_to_private_accounts — DIRECT_POST needs audit"
echo "  403 reached_active_user_cap               — Daily quota for active users reached"
echo "  401 access_token_invalid                  — Token expired (24h lifetime)"
echo "  401 scope_not_authorized                  — Missing video.upload or video.publish scope"
echo "  429 rate_limit_exceeded                   — API rate limit exceeded"
echo ""
echo "Full docs:"
echo "  https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide"
echo "  https://developers.tiktok.com/doc/content-posting-api-reference-upload-video"
echo "  https://developers.tiktok.com/doc/content-posting-api-reference-direct-post"
echo "  https://developers.tiktok.com/doc/content-posting-api-reference-photo-post"
echo "  https://developers.tiktok.com/doc/working-with-organizations"
