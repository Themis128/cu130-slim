# Cross-promotion drip campaigns

Grow one platform's audience by promoting it from your other accounts —
e.g. "follow me on Facebook" posts on LinkedIn, Threads, and Instagram to
reach a platform gate like Meta Stars' 500-follower threshold.

## Prerequisites

1. The target profile/URL you want to grow (e.g. a Facebook profile in
   professional mode).
2. Connected accounts on the platforms you'll promote from — see
   [Connecting social accounts](02-connecting-accounts.md).
3. For Instagram posts: an image already in the
   [media library](03-media-library.md).

## Steps

### 1. Draft platform-native copies

Write one copy per platform — same angle, different voice. Never paste the
identical text everywhere; same-day identical "follow me" posts read as
spam to both users and ranking systems.

- **LinkedIn**: keep the target URL *inline in the post body*. Putting it
  in a comment now costs ~80% reach and converts 3.4× worse.
- **Instagram**: caption links aren't clickable — write the URL as plain
  text and attach an image.
- **Threads**: casual, short, minimal hashtags.

### 2. Build a `plan.json`

```json
{
  "campaign": "my-campaign",
  "posts": [
    {
      "key": "LI-1",
      "platform": "linkedin",
      "text": "Your copy… https://example.com/you",
      "account_ids": ["<account-uuid>"],
      "scheduled_at": "2026-10-01T13:00:00Z"
    }
  ]
}
```

`scheduled_at` is UTC — Athens is UTC+3 (summer) / UTC+2 (winter).
Find account UUIDs under **Settings → Accounts** or `GET /api/v1/accounts`.

### 3. Analyze (dry run)

```bash
python3 .devin/skills/cross-promo-drip/scripts/drip.py plan.json
```

Each copy must score **SEO ≥90** with zero spellcheck issues. The tool
refuses anything below the bar — fix the copy and re-run.

### 4. Schedule

```bash
python3 .devin/skills/cross-promo-drip/scripts/drip.py plan.json --schedule
```

Creates `status=scheduled` posts, verifies stored text verbatim, and writes
post IDs to `plan.created.json` (`{"id", "fp", "verified"}` per key).

Re-running is safe and honest:

- Unchanged keys that verified cleanly are skipped.
- Unverified entries (e.g. a network error after creation) are re-checked
  on the next run before being trusted.
- If you **edit the plan** (text, time, accounts, or media) for an
  existing key, the run fails with a conflict — edit the post via the UI
  or delete it first, don't silently double-schedule.
- Identical text already in the queue is **adopted only when its
  schedule/accounts/media match**; a mismatch is flagged as a conflict.
- If a bad stored post can't be deleted cleanly, the entry is marked
  `pending_cleanup` and reconciled on the next run.
- Instagram entries without `media_ids` are refused (IG needs media).

### 5. Verify later

```bash
python3 .devin/skills/cross-promo-drip/scripts/drip.py plan.json --verify
```

Re-checks status, verbatim text, and target state for every recorded post.

## Rules

- **Drip, don't blast**: 1–2 posts per platform spread over ≥2 weeks.
- **No last-tier schedules**: X/Twitter and TikTok are opportunistic-only
  platforms (2-Platform Rule). `--schedule` rejects them — post manually.
- **Best slots (Athens)**: LinkedIn Wed/Thu 16:00, Fri 15:30 · Threads
  Wed/Thu 09:00 · Instagram Thu 09:00 · Facebook weekday 08:00–12:00.
- Instagram posts **require** `media_ids`.

## Reference

Full ruleset and account IDs:
`.devin/skills/cross-promo-drip/SKILL.md`
