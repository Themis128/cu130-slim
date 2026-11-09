---
name: platform-fit-ops
description: >-
  Single source of truth for per-platform audience/algorithm content-fit
  knowledge (what each social network's audience mindset rewards). Edit
  platform_fit.json once, then one script regenerates every surface:
  PLATFORM_GUIDES fit lines, the marked blocks in all *-content-ops skills
  (.devin + .cursor), and the company-brain reference doc + Nextcloud note.
  Use when platform algorithms/audience expectations change or new
  platform-fit research lands.
allowed-tools:
  - read
  - exec
  - grep
  - glob
triggers:
  - user
  - model
---

# Platform Fit Ops

Every surface that carries "what content works on platform X" is generated
from ONE file:

```
social-automation/backend/app/services/platform_fit.json
```

Per platform key (`linkedin`, `twitter`, `instagram`, `facebook`,
`threads`, `tiktok`):

- `guide_line` — one sentence appended to `PLATFORM_GUIDES[key]` as
  `Audience fit: ...` at import time in `content_guides.py` (feeds
  `/api/v1/ai/generate-content` + autonomous executors).
- `skill_title` + `bullets` — rendered into the
  `<!-- PLATFORM-FIT:BEGIN/END -->` marker block inside `## Content rules`
  of the matching `*-content-ops/SKILL.md` in BOTH `.devin/skills/` and
  `.cursor/skills/`.

## Sync

```bash
python3 .devin/skills/platform-fit-ops/scripts/platform_fit_sync.py
# drift check (exit 1 on drift):
python3 .devin/skills/platform-fit-ops/scripts/platform_fit_sync.py --check
# verify the guides resolve inside social-api (after compose cp):
python3 .devin/skills/platform-fit-ops/scripts/platform_fit_sync.py --verify-guides
# also upsert the company brain (Chroma + Nextcloud note):
python3 .devin/skills/platform-fit-ops/scripts/platform_fit_sync.py --brain
# FULL AUTOMATION — paste/drop new platform-fit research and let the
# stack's LLM chain (DMR -> Cloudflare fallback, inside social-api)
# extract the per-platform entries, merge into platform_fit.json,
# rewrite every surface, and seed the brain in one shot:
python3 .devin/skills/platform-fit-ops/scripts/platform_fit_sync.py --ingest research.md
cat research.md | python3 .devin/skills/platform-fit-ops/scripts/platform_fit_sync.py --ingest -
python3 .devin/skills/platform-fit-ops/scripts/platform_fit_sync.py --ingest https://example.com/article
# --ingest extras: --no-brain skips the brain seed, --commit stages the
# touched files (platform_fit.json + changed SKILL.md) on the current branch.
```

`--ingest` is the hands-off path: the extraction prompt lives in the script
(`INGEST_PROMPT`), output is merged per-platform (only platforms the
research actually covers are touched — the rest of the JSON is preserved),
then the normal sync + brain seed run. Requires `social-api` up (it hosts
the LLM call).

**Quality caveat**: the local 4B model produces focused, platform-specific
output on *curated* research text, but generic filler on large firehose
ingests (15 RSS items/feed degenerated; ~6 items/feed ≈ 7k chars is the
sweet spot). Always review the merged `platform_fit.json` diff before
`--commit`/merge — bot PRs never auto-merge for this reason.

## Weekly auto-refresh (cron)

`/etc/cron.d/platform-fit-refresh` runs
`scripts/platform_fit_refresh.sh` Sundays 07:00. It creates a clean
`git worktree` of `origin/master` (the live checkout is never touched),
runs `--ingest-feeds` (4 RSS sources: Social Media Examiner, Hootsuite,
Buffer, Sprout Social — ~6 latest items each), commits + pushes a
`platform-fit-auto-<stamp>` branch, and opens a PR **without**
auto-merge so the normal PR-sweep review gates it. No drift → no PR.
Log: `~/logs/platform-fit-refresh.log`. Requires PR #1543 (this skill)
merged to master first — the worktree is cut from `origin/master`.
`PLATFORM_FIT_REPO` env var points the sync script at an alternate repo
root (used by the driver).

`--brain` writes doc `platform-fit-reference` (metadata
`kind=platform-fit`) into the team's Chroma `team_*_brain` collection and
the "Platform Content Fit — Reference" note (category "Company Brain").
Team resolves via `SOCIALAUTO_TEAM_ID` env or the `*baltzakis*` team row.

## Deploy notes

- `content_guides.py` + `platform_fit.json` live under
  `social-automation/backend/app/services/` — they ship in the image, but
  for hot-patching a running stack:
  `docker compose cp` both files into `social-api` (and workers if the
  generation path runs there), then restart `social-api`.
- The `_platform_fit_lines()` loader is `lru_cache`d and fails soft — a
  missing/broken JSON just omits the fit line, never breaks generation.
- Skill SKILL.md blocks are docs for agents — no container sync needed;
  commit + PR like any repo change (master is PR-only).
