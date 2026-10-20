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

> Status 2026-10-20: no active workflow wires an Error Trigger yet (verified —
> zero `errorTrigger`/`errorWorkflow`/webhook refs in the live n8n DB). Add the
> flow above when the first workflow needs failure alerting.
