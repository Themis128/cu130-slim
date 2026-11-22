---
name: codeql-triage-ops
description: Triage the CodeQL PR gate on cu130-slim — fetch the alerts a PR actually introduced (not the dep-CVE backlog), classify real-bug vs intentional-design, fix or dismiss with correct API vocabulary, and verify the gate clears. Use when a PR shows "CodeQL fail", when pr_watch marks a CodeQL failure actionable, or when code-scanning alerts need review. Tool: scripts/codeql_pr_alerts.py.
---

# CodeQL Triage Ops

A `CodeQL fail` check-run on a PR means **new alerts introduced by that diff** —
the merge is gated, not advisory. Proven 2026-11-21 on PR #2463 (3 high alerts
blocked auto-merge while `pr_watch` reported "no actionable failures").

## What the gate counts (and what it doesn't)

- **Gate-relevant:** `tool=CodeQL` alerts introduced by the PR diff.
- **NOT the gate:** dependency CVE/GHSA rows — the `?pr=` filter on
  `code-scanning/alerts` returns the *broad associated set* including Dependabot-class
  alerts on vendored deps. A PR can show 60+ "alerts" and still have zero gate items.
- **Check-run vs workflow-run:** a `github.com/…/runs/<id>` CodeQL link is a
  *check-run* ID — `gh run view` 404s on it. Use
  `gh api repos/{o}/{r}/check-runs/<id>` for the summary, or skip straight to the
  alert list below.

## The triage loop

```bash
# 1. List what the PR actually introduced vs dep-CVE noise
python3 .devin/skills/codeql-triage-ops/scripts/codeql_pr_alerts.py <pr>

# 2. For each gate-relevant alert — decide:
```

| Alert class | Action |
|---|---|
| Real bug (taint, sanitization, injection) | Fix the diff, push — alert auto-closes |
| Intentional design (e.g. credential tooling *must* write a password to `.env`) | Dismiss `"won't fix"` + cite the precedent in the comment |
| Test/fixture code | Dismiss `"used in tests"` |
| Dep CVE rows | Ignore — not this gate; tracked by dependency-scan separately |

```bash
# 3. Dismiss when justified — note the API vocabulary
python3 .devin/skills/codeql-triage-ops/scripts/codeql_pr_alerts.py <pr> \
    --dismiss <alert_num> --reason "won't fix" \
    --comment "<why + precedent, e.g. 'same pattern as merged fb_pw_reset.py, alert #7698'>"

# 4. Verify
python3 .devin/skills/codeql-triage-ops/scripts/codeql_pr_alerts.py <pr> --check   # exit 0 = clear
gh pr checks <pr> | grep CodeQL                    # next push re-runs the check
```

## Gotchas learned the hard way

- **Dismissal reasons are human strings:** the API accepts exactly
  `"false positive"`, `"won't fix"`, `"used in tests"`, `"mitigated"`.
  Snake-case `wont_fix` → HTTP 422.
- **Clear-text storage of a password to `.env` is this repo's sanctioned
  credential-recovery pattern** (`chmod 600`, gitignored, consumed by
  docker-compose env) — the merged sibling `fb_pw_reset.py` carries the same
  open alert #7698. Dismiss with that citation; the class alert on master
  stays open deliberately so the pattern stays visible.
- **URL substring checks** (`"auth.meta.com" in url`) are a real finding —
  `auth.meta.com.evil.com` passes. Fix with `urlparse(url).hostname` equality,
  not dismissal.
- **AGENTS.md convention:** append each fixed/dismissed alert to the
  "CodeQL alert history" section, and don't claim an alert closed until the
  re-run scan confirms.

## When to escalate

- Alerts on files the PR didn't touch → scan drift, check workflow config.
- Dismissal would hide a genuinely new secret/crypto/path bug → fix it.
- Repeated identical alerts across consecutive PRs → the class is endemic;
  fix the shared helper, not each instance.
