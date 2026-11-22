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

## Devin `gmail` MCP plugin (2026-11-12 state)

The IDE's remote `gmail` MCP (gmailmcp.googleapis.com) is a **plugin-side
OAuth** surface, separate from this repo's `GMAIL_REFRESH_TOKEN`. When the
plugin reports "Failed to connect", the fix is client-side: re-login the
Gmail plugin in Devin Desktop / run `devin auth login` — there is no
repo-side config that repairs it (`~/.config/devin/mcp_config.json` only
carries `mcp-docker`; the gmail server is injected by the plugin, not
configured there). The repo path above (`gmail.py` + `google-consent.py`)
is unaffected and remains the working fallback — refresh token verified
live 2026-11-12.
