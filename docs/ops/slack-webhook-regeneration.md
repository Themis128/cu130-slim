# Slack Webhook Regeneration Runbook

**Status**: All incoming webhooks currently return `404 no_service` (dead). All Slack notifications use the `SLACK_BOT_TOKEN` fallback (`chat.postMessage`). Webhooks are not required for functionality but provide redundancy.

## Quick Check

```bash
# Probe all webhook URLs (empty POST — 400 = live, 404 = dead)
docker exec social-api python3 /app/scripts/slack_tool.py webhooks
```

## When to Regenerate

- Webhook probes return `404 no_service`
- The underlying Slack app was deleted/revoked
- You want webhook redundancy alongside bot-token delivery

## Regeneration Steps

### 1. Recreate the Slack App (if deleted)

1. Go to https://api.slack.com/apps → **Create New App** → **From a manifest**
2. Paste the manifest from `scripts/slack-app-manifest.yaml` (bundled with the `slack-ops` skill)
3. Install to the `wwwcloudlessgr` workspace
2. Copy the **Bot User OAuth Token** (`xoxb-...`) → set as `SLACK_BOT_TOKEN` in `.env`

### 2. Create Incoming Webhooks

1. In the app configuration page → **Incoming Webhooks** → toggle **ON**
2. Click **"Add New Webhook to Workspace"** once per channel:

| Channel | Env Var | Purpose |
|---------|---------|---------|
| `#socialauto` | `SLACK_WEBHOOK_URL` | Daily digest |
| `#socialauto-alerts` | `SLACK_ALERTS_WEBHOOK_URL` | Operational alerts |
| `#socialauto-publishing` | `SLACK_PUBLISHING_WEBHOOK_URL` | Publish summaries |
| `#socialauto-leads` | `SLACK_LEADS_WEBHOOK_URL` | Lead notifications |
| `#socialauto-approvals` | `SLACK_APPROVALS_WEBHOOK_URL` | Approval requests |
| `#socialauto-support` | `SLACK_SUPPORT_WEBHOOK_URL` | Support tickets |
| `#socialauto-billing` | `SLACK_BILLING_WEBHOOK_URL` | Billing digests |

3. Paste each webhook URL into the matching `SLACK_*_WEBHOOK_URL` in `.env`

### 3. Verify

```bash
# Restart social-api to pick up new env
docker compose up -d social-api

# Probe webhooks again — all should show "live"
docker exec social-api python3 /app/scripts/slack_tool.py webhooks
```

## Current Architecture Note

With `SLACK_BOT_TOKEN` set, **digest delivery uses `chat.postMessage` and no longer depends on webhooks at all** — dead webhooks cannot silently kill notifications. The webhook path is tried first, then falls back to bot token.

## Stale-env Pitfall

After changing any `SLACK_*` env var, **recreate every token consumer**:

```bash
docker compose up -d --force-recreate \
  social-api \
  social-worker-publishing \
  social-worker-media \
  social-worker-default \
  social-worker-messenger \
  celery-beat
```

`slack_token_sync.py` (installed as a 2h cron) automates this — it updates `.env`, refreshes the `xoxe` user token via MCP, and recreates all consumers including n8n.

## File Uploads

Webhooks **cannot** upload files. File uploads use the 3-step external upload flow (`files.getUploadURLExternal` → PUT → `files.completeUploadExternal`) which requires `SLACK_BOT_TOKEN` + `files:write` scope. This is handled by `upload_file_to_slack()` in `app/services/slack_notifications.py`.