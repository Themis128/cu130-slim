#!/usr/bin/env bash
# land_pr.sh — rebase a PR branch onto origin/master via a throwaway
# worktree and force-push, sidestepping a dirty main checkout.
#
# Usage:
#   land_pr.sh <pr-branch> [commit...]     # commits default to branch tip(s)
#                                          # not on origin/master
#
# Env: WORKTREE_DIR (default mktemp -d /tmp/land-XXXX)
#
# Behavior:
#   - Creates a worktree at origin/master, cherry-picks the branch's
#     unique commits, force-pushes to the PR branch.
#   - If cherry-pick hits conflicts it drops you into the worktree to
#     resolve (git status shows them), then: resolve + `git cherry-pick
#     --continue`, and re-run this script's push step printed at the end.
#   - Never touches the main checkout's dirty tree.
set -euo pipefail

BRANCH="${1:?usage: land_pr.sh <pr-branch> [commit...]}"
shift || true

cd "$(git rev-parse --show-toplevel)"
git fetch origin -q

if [ $# -gt 0 ]; then
  COMMITS=("$@")
else
  mapfile -t COMMITS < <(git rev-list --reverse "origin/master..$BRANCH")
fi
[ "${#COMMITS[@]}" -gt 0 ] || { echo "nothing to rebase"; exit 0; }

WT="${WORKTREE_DIR:-$(mktemp -d /tmp/land-XXXX)}"
TMPB="tmp-land-$(basename "$WT")"

git worktree add "$WT" -b "$TMPB" origin/master >/dev/null
cd "$WT"

if ! git cherry-pick "${COMMITS[@]}"; then
  cat <<EOF

Conflict — resolve inside the worktree:
  cd $WT
  # edit conflicted files (BACKLOG.md: take --ours, re-add your bullet)
  git add -A && git cherry-pick --continue
  git push --force-with-lease origin HEAD:refs/heads/$BRANCH
  cd - && git worktree remove $WT --force && git branch -D $TMPB
EOF
  exit 1
fi

git push --force-with-lease origin "HEAD:refs/heads/$BRANCH"
cd - >/dev/null
git worktree remove "$WT" --force && git branch -D "$TMPB" >/dev/null
echo "rebased $BRANCH onto origin/master ($(git rev-parse --short origin/master)) and pushed"
