# gmail-ops — read Gmail via OAuth

Read-only Gmail search through the Google OAuth client (`client_secret_…json` in Downloads). Stdlib-only — no deps.

## One-time consent (human gate)

```bash
python3 .devin/skills/gmail-ops/scripts/gmail.py login
# opens the consent URL — approve once → GMAIL_REFRESH_TOKEN saved to .env
```

## Search

```bash
python3 .devin/skills/gmail-ops/scripts/gmail.py search "newer_than:3d synigoros OR ombudsman"
python3 .devin/skills/gmail-ops/scripts/gmail.py search "subject:202610260029"
```

Prints `date | from | subject` per match. Scope is `gmail.readonly` — no send/delete.

## Notes

- The OAuth client must allow `http://127.0.0.1` loopback redirects (Desktop-app clients do).
- Gmail IMAP needs a 16-char **app password**, not the account password — this OAuth path avoids that entirely.
- Token lives in `.env` as `GMAIL_REFRESH_TOKEN` — treat as a secret, never log it.
