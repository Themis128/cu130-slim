---
name: pr-heal-ops
description: Heal DIRTY/BEHIND PRs when git push from WSL is auth-broken — BEHIND via gh pr update-branch; DIRTY via clean-rebuild (push only the files the PR adds/changes on top of latest master as a new PR, close the conflicted one). Use when mergeStateStatus=DIRTY/BEHIND on cu130-slim PRs.
---

# PR heal ops

## Triage
`gh pr view N --json mergeStateStatus` → `BEHIND` or `DIRTY`.

## BEHIND
`gh pr update-branch N` — merges master into the branch, CI re-runs,
auto-merge resumes. Works for most PRs.

## DIRTY (real conflicts) — clean rebuild
`scripts/pr_rebuild.py N` does the proven sequence:
1. `gh pr diff N` → collect changed file paths.
2. For each path, fetch the PR-branch version (`?ref=<head>`) unless identical
   to master — skip files already on master (avoids stomping merged work).
3. blobs → tree(base=master HEAD) → commit → new branch `<head>-rebuilt` →
   PR → `gh pr merge --squash --auto` → close the old PR "superseded".
4. Deleted-file PRs can't be rebuilt this way — flag them for manual merge.

Verified on PR #1354 → chore/leftover-sync-v2 (13 files, zero conflicts).

## Never
- Never `git push` to master (GH006) or `git reset --hard` (policy-blocked).
- Don't rebuild a DIRTY PR whose author is mid-review — comment instead.
