## n8n failure notifications → Slack

Goal: when an n8n workflow fails, send a short alert to `#socialauto-alerts` without duplicating secrets in workflow JSON.

### Delivery path — bot token, not incoming webhooks

**Do not use Slack Incoming Webhook URLs** (`hooks.slack.com/services/...`).
All webhook URLs in `.env` returned 404 `no_service` on 2026-10-19 (Slack app
reinstalled) — a workflow posting to one fails silently. The verified-working
path is `chat.postMessage` with `SLACK_BOT_TOKEN`, which is already injected
into the n8n container and posts to every configured channel
(`SLACK_ALERTS_CHANNEL_ID` = `#socialauto-alerts` = `C0C7N9SB59S`).

### Environment variables

- `SLACK_BOT_TOKEN` — present in the n8n container env (verified 2026-10-20)
- `SLACK_ALERTS_CHANNEL_ID` — target channel (`C0C7N9SB59S`), injected into
  the n8n container via docker-compose (defaults to `C0C7N9SB59S`); requires
  `docker compose up -d n8n` to land in the live env

### Example n8n flow

1. Add an **Error Trigger** node.
2. Add an **HTTP Request** node configured as:
   - Method: `POST`
   - URL: `https://slack.com/api/chat.postMessage`
   - Authentication: Generic Credential Type → Header Auth, or set the header directly:
     - `Authorization`: `=Bearer {{$env.SLACK_BOT_TOKEN}}`
     - `Content-Type`: `application/json`
   - Body Content Type: `JSON`
   - JSON Body:

```json
{
  "channel": "{{$env.SLACK_ALERTS_CHANNEL_ID}}",
  "text": "*n8n workflow failed*\n• workflow: {{$json.workflow.name}}\n• execution: {{$json.execution.id}}\n• error: {{$json.error.message}}"
}
```

### Incoming-webhook fallback (only after webhooks are regenerated)

If dedicated webhooks are re-created in the Slack app console
(`N8N_ERROR_SLACK_WEBHOOK_URL` / `SLACK_ALERTS_WEBHOOK_URL`), a plain
`{"text": "..."}` POST to the webhook URL also works — but the bot-token path
above is preferred and is the one that is verified live.

### Wiring — the handler alone alerts on nothing

An Error Trigger workflow only fires for workflows that name it. After
creating the two-node flow above:

1. **Save and activate/publish** the error-handler workflow (e.g.
   `Notify failures`).
2. For **each monitored workflow**: Settings → **Error Workflow** → select
   `Notify failures`. Repeat per workflow — there is no global default.
3. **Verify with a production execution**: a manual "Test workflow" run in
   the editor does **not** reliably invoke the Error Workflow — n8n only
   guarantees the error path on real executions. Force a failure on a
   scheduled or webhook-triggered run (e.g. point a node at a bad URL and
   let the cron/webhook fire it) and confirm the alert lands in
   `#socialauto-alerts`.

### Token lifetime caveat

`SLACK_BOT_TOKEN` is a rotating user token (12h). `slack_token_sync.py`
refreshes it and recreates every consumer in its `SERVICES` list — **n8n
is in that list** (added 2026-10-20), so post-rotation the container picks
up the fresh token automatically. Recreating n8n is deferred while its
executions run (`EXECUTIONS_MODE=regular` shares the container — a forced
recreate mid-run would kill them); the sync polls
`GET /api/v1/executions?status=running`, waits up to `N8N_BUSY_WAIT_S`
(600s) for a drain, then issues a graceful `compose stop -t
N8N_STOP_GRACE_S` (120s — SIGTERM refuses new triggers and drains
in-flight runs) before recreating. A failed API probe only proceeds when
`docker inspect` reports the container stopped; otherwise n8n stays in
its per-service pending marker for the next 2h cron pass. If the sync is
ever replaced, failure alerts silently stop at the next token expiry.

> Status 2026-10-20: wired live. `notify-failures` (Error Trigger → Slack
> `chat.postMessage`, `n8n-workflows/notify-failures.json`) is deployed and
> published; all 21 active workflows have `settings.errorWorkflow =
> "notify-failures"` — set in the repo JSONs (source of truth) and applied
> live via `PUT /api/v1/workflows/{id}` (no restart needed — the setting is
> read at execution time). `SLACK_BOT_TOKEN` and `SLACK_ALERTS_CHANNEL_ID`
> are present in the n8n container env. End-to-end delivery is verified by
> the next real workflow failure — force one on a scheduled run if a live
> confirmation is wanted sooner.
