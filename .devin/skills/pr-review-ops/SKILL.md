---
name: pr-review-ops
description: Review PRs when GitHub Copilot review fails on quota — fetches the diff, runs local heuristics (secrets, debug leftovers, size), runs ruff on touched .py via social-api, posts the review body + APPROVE/COMMENT via gh api. Use when a Copilot "insufficient quota" email arrives.
---

# PR review ops (Copilot-quota fallback)

When `@Copilot` fails with "insufficient remaining quota", review it yourself:

  GH_REPO=owner/repo pr_review.py <PR#> [--approve] [--comment "note"]

The tool:
1. `gh pr diff` → stats (files, additions, deletions).
2. Heuristic scan: AWS/GitHub-looking secrets, `pdb`/`console.log`/`TODO` leftovers,
   `.env`/`*.pem`/`*.key` paths, oversized diff (>3k lines).
3. For touched `.py` under social-automation/backend: copies into `social-api`,
   runs `ruff check` (same as CI).
4. Writes a review body (findings + verdict) and posts it:
   `gh api repos/{repo}/pulls/{n}/reviews -F event=COMMENT|APPROVE`.
5. `--approve` only when zero findings; defaults to COMMENT so a human can
   sanity-check before merge.

## Rules
- Copilot review is NOT a required check — `validate` + `secret-scan` gate merges;
  a failed Copilot review never blocks anything.
- Don't approve code you haven't actually diffed. When in doubt: COMMENT.
- Required-check failures beat heuristics: check `mergeStateStatus` first —
  BLOCKED-with-failing-checks is a fix, not a review.
