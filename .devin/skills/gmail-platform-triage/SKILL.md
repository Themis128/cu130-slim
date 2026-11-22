# gmail-platform-triage

Scan and clean platform-related mail (Meta/LinkedIn/TikTok/X/Cloudflare/
Vercel/GitHub/Google-dev/Slack/Polar/Resend/Cal.com/WhatsApp/Viber/Bluesky/
Telegram) in the operator's Gmail mailboxes.

## Auth lanes (auto-detected from repo `.env`)

| Token | Scope | Capabilities |
|---|---|---|
| `GMAIL_SEND_TOKEN_JSON` | `gmail.modify` | scan + trash |
| `GMAIL_TOKEN_JSON` | `gmail.readonly` | scan only |
| `GMAIL_REFRESH_TOKEN[_THEMIS_BALTZAKIS]` | refresh → readonly | scan only |

All current tokens resolve to `baltzakis.themis@gmail.com`. There is **no
token for `themis.baltzakis@gmail.com`** — that mailbox needs its own OAuth
grant before it can be scanned or trashed.

## Commands

```bash
# inventory — per-category counts + newest subjects
python3 .devin/skills/gmail-platform-triage/scripts/gmail_platform_triage.py scan --days 60

# dry-run trash
python3 .devin/skills/gmail-platform-triage/scripts/gmail_platform_triage.py \
  trash --categories meta,cloudflare,github --days 60

# real trash (moves to Gmail Trash, recoverable ~30d — never permanent)
python3 .devin/skills/gmail-platform-triage/scripts/gmail_platform_triage.py \
  trash --all-addressed --days 60 --yes

# security mail is opt-in only
python3 .devin/skills/gmail-platform-triage/scripts/gmail_platform_triage.py \
  trash --categories linkedin-sec,google-sec --include-security --yes
```

## Rules

- **Trash ≠ delete forever** — `batchModify addLabelIds=TRASH` keeps mail
  recoverable for ~30 days. Only run after the underlying platform issue
  was actually addressed (code shipped, token verified, human gate
  documented) — same convention as `mail-triage-ops`.
- **Security categories are gated** — `linkedin-sec` (device-verify,
  unusual sign-in) and `google-sec` require `--include-security`.
  Device-verify mail may indicate unverified sidecar sessions; confirm
  token health (`linkedin_graph_tool.py`, `x_tool.py`, etc.) before trashing.
- **Newsletters/job-alerts are included** in `linkedin`/`google-dev`
  counts — they are platform mail but produce no action; trashing them is
  cosmetic cleanup, do it in bulk only when asked.
- Never print or commit token values — the script reads `.env` at runtime.
