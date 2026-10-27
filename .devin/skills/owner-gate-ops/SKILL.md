# owner-gate-ops — convert owner-gated issues into one-reply decisions

Use when open GitHub issues are approval/review gates (BACKLOG-registered `*-ops` items: outreach approvals, platform verifications, offer decisions).

## Pattern (verified 2026-10-27)

Owner-gated issues stall because they carry stale backlog notes. The fix is always:

1. **Query live state first** — never trust the issue's "historical observations":
   - DB: `docker exec social-postgres psql -U social_user -d social_automation` — `social_accounts.status` can be STALE (WhatsApp showed `active` while WABA said NOT_VERIFIED — always cross-check via the platform tool).
   - Platform CLIs: `scripts/whatsapp_tool.py status`, `scripts/meta_tool.py debug-token --account <uuid>` (full UUID required; is_valid/expires_at/scopes), `scripts/tiktok_tool.py accounts|videos`, `scripts/newsletter_nurture.py list|run --dry-run`.
   - EspoCRM: `scripts/espo_leads_tool.py` (added alongside this skill).
2. **Post a data-grounded comment** on the issue: what live state actually is + the single action that unblocks it. Turn open questions into reply-able proposals (numbered lists, concrete content, concrete numbers) — "approve 1,3,5" / "approved" / "batch 10" should be all the owner ever needs to type.
3. **Never execute the gated action** — no outreach sends, no test posts, no profile edits, no captcha/SMS paths. Present the plan; wait for explicit approval.

## Heavy-script note (newsletter_nurture, etc.)

- `social-api` has a 1 GiB mem_limit — heavy scripts OOM (exit 137) even at small --limit.
- Run on an unconstrained container instead: `docker run -d --network cu130-db --env-file <env> --entrypoint sleep <social-api image> 600`, `docker network connect cu130-backend <id>`, then `docker exec`. Build env from `docker inspect <social-api> .Config.Env` + `get_settings().model_dump()` (env-file secrets don't live in Config.Env) — never print secrets.
- Scripts may need BOTH networks: postgres on `cu130-db`, browser sidecars on `cu130-backend`.

## Idle-sleep wake race

- `linkedin-browser-sidecar` + `browser-novnc` sleep when idle. First request wakes them; immediate follow-ups race the browser reconnect → RemoteProtocolError/ConnectError.
- Preflight before any send/dry-run: `GET /health` on the sidecar and check `browser_connected: true` (or wait for it) — otherwise first batch's early items fail.
- `Exited` containers are usually asleep, not broken — `scripts/stackctl.py status` before diagnosing.
