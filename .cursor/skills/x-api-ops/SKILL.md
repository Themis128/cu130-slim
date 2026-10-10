---
name: x-api-ops
description: >
  X (Twitter) API v2 ops tooling via scripts/x_tool.py — token validation,
  followers/metrics for @TBaltzakis, user lookups, tweet metrics, rate-limit
  headers, and the 402 credits-depleted boundary (which endpoints are free vs
  pay-per-use). Use for X token health, quota debugging, follower tracking for
  the FB-Stars funnel, or checking whether a failure is quota vs session.
---

# X API ops

Official X API v2 ops surface. Console write-paths stay in `x-content-ops`
(API → browser fallback); browser-sidecar work stays in `browser-ops`.

## Tool

```bash
docker exec social-api python3 /app/scripts/x_tool.py <cmd> [-a ACCOUNT_UUID]
```

| Command | Endpoint | Notes |
|---|---|---|
| `accounts` | DB only | twitter SocialAccounts, user_id, scopes |
| `validate` | `GET /2/users/me` | token check — id/username |
| `me` | `GET /2/users/me` + `user.fields` | followers/following/tweet counts, verified |
| `user <handle>` | `GET /2/users/by/username/:h` | public metrics for any account (pay-per-use) |
| `tweet <id>` | `GET /2/tweets/:id` | impressions/likes/rt/replies/quotes/bookmarks |
| `recent [n]` | `GET /2/users/:id/tweets` | own timeline w/ metrics (pay-per-use) |
| `quota` | `GET /2/users/me` headers | `x-*-limit-*` headers — quota state without a write call |

Never prints tokens — decrypts `SocialAccount.access_token_enc` in-process only.

## Free vs pay-per-use (verified live 2026-10)

The app token runs on X's usage-credit model. Read endpoints split:

- **Works (in allowance):** `POST/GET/DELETE /2/tweets`, media upload
  (OAuth1.0a-signed `media_signer`), DMs, `GET /2/users/me` + fields.
- **402 `credits-depleted`:** `GET /2/users/:id/tweets`, `GET
  /2/users/by/username/:h` — user timelines and user lookup are billed per
  call; when the app's credit balance is empty these return HTTP 402 with
  `type: credits-depleted`. `x_tool` surfaces this instead of a generic error.
- Client helper `_is_credits_depleted()` already detects the signature —
  publishing code treats it as retryable-after-refill, NOT a session failure.

So `validate`/`me` working while `recent`/`user` 402 = **quota exhausted**, not
a broken token. Refill is a dev-console billing action — flag to the user,
don't retry-loop.

## Account (verified)

- `a89d6852-eff8-479a-835f-50d806cf59dd` @TBaltzakis — OAuth2 `3L`, scopes
  `tweet.read tweet.write users.read dm.read dm.write offline.access`
- Live: 0 followers / 16 following / 15 tweets — the FB-Stars personal funnel
  start point; `me` is the growth-check command.

## Skipped repos (searched `x`/`twitter` on github.com)

- `tweepy/tweepy` — sync blocking client over the same v2 endpoints; our async
  httpx client is already equivalent. No adoption needed.
- `xdevplatform/samples` — reference snippets, nothing missing.
- `xdevplatform/*-sdk` — Java/TypeScript, wrong runtime.
- `twitterdev/Twitter-API-v2-sample-code` — superseded by `xdevplatform/samples`.

## Related

- `x-content-ops` — what/how to post, API→browser fallback chain, 280-char rules
- `browser-ops` — sidecar fallback when API quota/auth fails
- `social-content-core` — account map, funnel, media-required rule
