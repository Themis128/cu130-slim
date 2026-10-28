---
name: meta-graph-ops
description: Meta Graph API tooling via the official facebook-business SDK — token introspection (debug_token), page insights, account inventory for FB/IG/Threads. Use when checking Meta token health/scopes, page metrics, debugging insights metric deprecations, or validating what a stored token can actually do.
---

# Meta Graph Ops (facebook-business SDK)

Official `facebook_business` SDK (github.com/facebook/facebook-python-business-sdk,
`facebook-business` in pyproject deps) installed in the backend image.

## Tool: `social-automation/backend/scripts/meta_tool.py`

Run inside the social-api container (it imports `app.*` for DB + token
decryption; never prints tokens):

```bash
docker exec social-api python3 /app/scripts/meta_tool.py accounts
docker exec social-api python3 /app/scripts/meta_tool.py debug-token --account <uuid>
docker exec social-api python3 /app/scripts/meta_tool.py pages     --account <uuid>
docker exec social-api python3 /app/scripts/meta_tool.py insights  --account <uuid> --days 7
```

- `accounts` — Meta-platform SocialAccount rows (id, platform, username,
  stored token expiry, status).
- `debug-token` — calls Graph `debug_token` with the app token
  (`FACEBOOK_CLIENT_ID|FACEBOOK_APP_SECRET`): validity, real expiry,
  `data_access_expires_at`, granted scopes, granular scopes. Use this BEFORE
  assuming a session/token problem is a session problem.
- `insights` — requests page metrics **individually** because Meta deprecates
  `page_*` metrics between API versions and one invalid metric fails the whole
  call (`(#100) The value must be a valid insights metric`). Deprecated names
  report `unavailable (100)` — update the candidate list as Meta moves them.

## Token model

`SocialAccount.access_token_enc` is Fernet-encrypted; `decrypt_token()` in
`app/core/security.py`. The app token for `debug_token` is
`FACEBOOK_CLIENT_ID|FACEBOOK_APP_SECRET` from settings — no user token needed
for introspection.

## Meta repo map (what else is worth reaching for)

- `facebook/facebook-python-business-sdk` — installed. Pages/IG/Ads/WhatsApp
  objects; prefer it over raw httpx for NEW Meta API work.
- `fbsamples/graph-api-webhooks-samples` — reference for Threads/IG webhook
  subscriptions if we add real-time inbound events.
- `fbsamples/whatsapp-api-examples`, `fbsamples/messenger-platform-samples` —
  canonical request shapes for whatsapp-ops / messenger-ops debugging.
- `facebook/ThreatExchange` (PDQ/TMK hashing) — needs a C++ toolchain absent
  from the slim image; media dedup uses pure-Python `imagehash` instead
  (`media-dedup` skill). Revisit only if the image ever ships gcc.
- `facebookresearch/segment-anything(-2)` — optional upgrade path for
  remove-bg/smart-crop masks; current CF-rembg + rembg fallback is sufficient.
  GPU contention with ComfyUI on the shared 3070 makes this poor value now.
- `facebookresearch/audiocraft` (MusicGen) — rejected: social autoplay is
  muted; branded videos are intentionally silent.
