# socialauto-janitor

Recurring operational leftovers sweeper for the SocialAuto stack. Dry-run by
default; every write requires `--apply`.

## What it cleans

| Command | Finds | Action (`--apply`) |
|---|---|---|
| `fixtures` | Posts with zero `post_targets` whose title/content matches test patterns (`e2e`, `fixture`, `seeded`, `test post`, `dummy`, `lorem`) in draft/scheduled/failed/review | `status → archived` |
| `media-orphans` | `media_assets` not referenced by any `posts.media_ids`, older than N hours, optional filename `--pattern` | `is_archived → true` |
| `queue-status` | `publish_queue` rows not `completed`, grouped counts + open rows | report only |
| `queue-requeue` | Latest `failed` queue row for a post prefix | reset to `pending` at `now + --delay-minutes` (default 60), `attempts=0` |
| `accounts` | `social_accounts` whose status isn't active/connected/ok | report only |
| `sweep` | fixtures + branded-* orphans + queue-status + accounts in one pass | `--apply` archives fixtures+orphans |

## When to use

- After media-pipeline repairs (orphaned ad-hoc assets)
- After publish outages (stuck/failed queue rows, expired accounts)
- Periodic hygiene — E2E fixtures and dead assets accumulate
- Before/after any bulk media regeneration

## Rules

- **Dry-run first.** Every mutating command prints what it would change.
- Media creation/repair still goes through `/api/v1/media/*` or
  `/api/v1/ai/*` only — the janitor never creates media, it only archives
  unreferenced assets.
- `queue-requeue` only touches the *latest* failed row per post; older failed
  rows are history.
- Fixture detection requires BOTH a test-like name AND zero targets — real
  posts with targets are never touched.

## Run

```bash
.devin/skills/socialauto-janitor/scripts/janitor.py sweep            # report
.devin/skills/socialauto-janitor/scripts/janitor.py sweep --apply    # clean
.devin/skills/socialauto-janitor/scripts/janitor.py queue-requeue adb230e0 --delay-minutes 60 --apply
```

Requires the `social-api` container running (payload executes inside it via
`docker exec -i`). Mirrors DB schema of `app/models/{content,queue,social_account}.py`.
