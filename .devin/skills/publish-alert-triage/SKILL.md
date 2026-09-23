---
name: publish-alert-triage
description: >-
  Triage SocialAuto Slack-digest alerts — failed publish targets and
  analytics-sync errors — into actionable buckets (platform limit vs app bug
  vs session vs config vs transient). Covers the signature table for every
  observed error (TikTok url_ownership_unverified, FB #200 personal profile,
  IG Session expired / media not available / missing image, X quota + browser
  fallback selectors, Meta insights metric rejections, Threads object-not-
  found) and the runbook for each fix. Use when digests fire "We couldn't
  publish a post" or "Analytics sync had trouble", or after a publishing
  incident.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Publish alert triage

## When to use

- Slack digest posts "We couldn't publish a post" / "Analytics sync had trouble"
- Failed targets accumulate in `post_targets` / `publish_queue`
- Deciding what's retryable vs a platform limit vs a real bug

## Tool

```bash
# Full triage report (default 7d window)
.devin/skills/publish-alert-triage/scripts/alert_triage.py

# Narrow the window / sections
alert_triage.py --days 2
alert_triage.py --section failures,analytics
alert_triage.py --json
```

Sections: **failures** (post_targets status=failed grouped by signature),
**analytics** (snapshot error notes — the digest's "Heads up" source),
**accounts** (token status/expiry/refresh-token presence), **sessions**
(sidecar + bridge session health), **queue** (publish_queue status +
overdue items), **env** (config presence).

Classification classes: `app-bug` → fix code · `config` → env/console ·
`session` → reconnect/login · `platform-limit` → don't retry · `transient`
→ retry covers it · `info` → benign.

## Signature runbook

### Platform limits — do NOT retry, adjust targeting/config

| Signature | Meaning | Action |
|---|---|---|
| `(#200) If posting to a group…` / `publish_to_groups` | FB Groups API deprecated (v19+) | Soft-skip; retarget to a Page (`pages_manage_posts`). Groups API removed Apr 2024 — reconnect will not help |
| `Instagram requires at least one image` | Text-only post to IG | Attach media or drop the IG target |
| `X free tier monthly write quota` | 1,500 tweets/mo exhausted | Waits for billing reset; browser fallback covers real posts |
| `stats HTTP 402` / `non_public_metrics` | Paid-tier metric on free plan | Expected — public_metrics still sync |
| `quota_exhausted` | X free-tier read cap hit (402/429 → coded state) | Expected — persists until billing reset; publishing unaffected |

### Config — one-time fixes

| Signature | Meaning | Action |
|---|---|---|
| `url_ownership_unverified` | TikTok `PULL_FROM_URL` needs a verified domain | `tiktok-console-ops` → `domain-verify.sh` (console token → CF DNS TXT → Verify) |
| `MEDIA_PUBLIC_BASE_URL not set` | No public media URL for TikTok pull | Set env to public https URL (CF tunnel); verify tunnel running |
| `(#10) Application does not have permission` | Meta app lacks scope/review | `meta-app-review` skill — `instagram_content_publish`/`pages_*` scopes |

### Session — reconnect/re-login

| Signature | Meaning | Action |
|---|---|---|
| `Session has expired` / `Cannot parse access token` / `stats HTTP 401` / `REVOKED_ACCESS_TOKEN` | Dead OAuth token | Reconnect in Accounts UI; verify `refresh_expiring_tokens` rotates what it can. Note: X tokens live 2h (`expires_in:7200`) and refresh tokens are single-use — a skipped boundary run leaves ~1h of 401s; `_skip_for_recent_update` in token_refresh.py guards this |
| `Browser session not logged in` | Bridge/sidecar session dead | noVNC re-login or `session-transplant` skill (cookie move) |
| `does not exist, cannot be loaded due to missing permissions` | Stale media id or missing scope | Usually media deleted on platform or wrong account — check |
| `publish_cancelled` (TikTok) | MEDIA_UPLOAD inbox draft dismissed | Republish; DIRECT_POST needs approved app audit |

### App bugs — fix in code

| Signature | Root cause | Fix |
|---|---|---|
| `metric[N] must be one of…` | Meta rejects a metric name for this API version | `_meta_insights_get` adaptive dropper should eat these — if the note persists, the failing call bypasses the helper or the *last* metric was invalid |
| `(#100) The value must be a valid insights metric` | FB post-insights metric invalid | Valid post metrics: `post_impressions`, `post_media_view`, `post_clicks`, `post_reactions_like_total`, `post_comments`, `post_shares` |
| `Media ID is not available` | `media_publish` before container `FINISHED` | Poll `GET {container_id}?fields=status_code` until `FINISHED` before publishing |
| `Post button disabled` / `Could not find the tweet composer` / `Continue button not found` | X web UI selector drift | Update bridge X selectors (login flow + composer) |

### Transient / info — no action

- `Session already active for X` / `Browser busy` — shared bridge serializes sessions; queue retries handle it.
- `Page.goto…browser closed` / `All connection attempts failed` — browser restart/network blip.
- `HTTP 5xx`, `upload timeout` — platform-side.
- `stats HTTP 404` / `video_not_found` — media deleted on platform.
- `member_stats_not_implemented`, `organization_lifetime` — informational markers, not errors.
- Digest soft-skips: `activityids`, `stats_unavailable` (mirrors `slack_digest.py`).

## Known noise sources

- **E2E/test teams** (Calendar E2E, E2E Test User, Wonf…) — digests run per
  team; a team with 0 accounts emits "No social accounts connected" every
  morning. Teams with no accounts AND no posts in the window arguably
  shouldn't emit at all.
- **LinkedIn sidecar 429** — circuit auto-opens on redirect loops; clears
  via `POST :9225/session/clear-rate-limit`. Repeated openings = LinkedIn
  anti-bot pressure, not an app bug.
- **IG `Session has expired` bursts** — token expires ~60 days after issue;
  `instagram/cloudless.gr` has no refresh token so it can't self-heal —
  reconnect is the only fix.

## Related skills

`tiktok-console-ops` (domain verify, QR login), `session-transplant`
(cookie moves), `session-health-ops` (sidecar sweep), `meta-app-review`
(Meta permissions), `socialauto-accounts` (token refresh), `n8n-cloudless`
(digest workflow).
