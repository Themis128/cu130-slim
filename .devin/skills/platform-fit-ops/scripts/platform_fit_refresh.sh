#!/usr/bin/env bash
# platform_fit_refresh.sh — weekly unattended platform-fit refresh.
#
# Runs the whole pipeline against a CLEAN worktree of origin/master so the
# live checkout (which may carry unrelated WIP) is never touched:
#   fetch RSS feeds -> LLM ingest -> platform_fit.json + skill blocks ->
#   commit -> push branch -> PR (review-gated, no auto-merge) -> brain seed.
#
# Scheduled via /etc/cron.d/platform-fit-refresh (Sun 07:00).
# Log: ~/logs/platform-fit-refresh.log
set -euo pipefail

# cron's default PATH lacks /usr/local/bin (gh lives there)
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

REPO="${PLATFORM_FIT_REPO_ROOT:-/home/tbaltzakis/cu130-slim}"
SYNC="$REPO/.devin/skills/platform-fit-ops/scripts/platform_fit_sync.py"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BRANCH="platform-fit-auto-$STAMP"
WORK="$(mktemp -d)/wt"

cd "$REPO"
git fetch origin -q
git worktree add -b "$BRANCH" "$WORK" origin/master
cleanup() { git -C "$REPO" worktree remove --force "$WORK" 2>/dev/null || true; rm -rf "$(dirname "$WORK")"; }
trap cleanup EXIT

# Ingest feeds inside the worktree (PLATFORM_FIT_REPO points every path at it).
# --commit stages platform_fit.json + changed SKILL.md files in the worktree.
PLATFORM_FIT_REPO="$WORK" python3 "$SYNC" --ingest-feeds --commit

# Nothing changed -> no PR.
if [ -z "$(git -C "$WORK" status --porcelain -- social-automation/backend/app/services/platform_fit.json .devin/skills .cursor/skills)" ]; then
  echo "no drift — skipping PR"
  exit 0
fi

git -C "$WORK" push -u origin "$BRANCH"

GH_TOKEN="$(sed -n 's|https://[^:]*:\([^@]*\)@github.com|\1|p' "$HOME/.git-credentials")"
export GH_TOKEN

gh pr create --repo Themis128/cu130-slim --base master --head "$BRANCH" \
  --title "content: weekly platform-fit auto-refresh" \
  --body "Automated weekly refresh of platform audience/algorithm content-fit knowledge (platform-fit-ops --ingest-feeds). Review the platform_fit.json diff before merge — LLM extraction can over-include platforms." \
  | tee /tmp/pf-pr-url.txt

PR_NUM="$(grep -oE '[0-9]+$' /tmp/pf-pr-url.txt | tail -1)"
# No --auto: LLM-extracted content gets human/agent review via the normal
# PR-sweep before it can reach the generation prompts. The PR is created
# regardless so drift is never silent.
if [ -n "$PR_NUM" ]; then
  echo "PR #$PR_NUM opened — awaiting review (no auto-merge by design)"
fi
