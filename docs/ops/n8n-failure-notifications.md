## n8n failure notifications → Slack

Goal: when an n8n workflow fails, send a short alert to `#socialauto-alerts` without duplicating secrets in workflow JSON.

### Environment variables

- `N8N_ERROR_SLACK_WEBHOOK_URL`: optional Incoming Webhook URL (recommended if you want a dedicated n8n-only webhook)
- `SLACK_ALERTS_WEBHOOK_URL`: SocialAuto alerts Incoming Webhook URL (fallback)

In n8n, prefer using this expression so you can override per environment:

`{{$env.N8N_ERROR_SLACK_WEBHOOK_URL || $env.SLACK_ALERTS_WEBHOOK_URL}}`

### Example n8n flow

1. Add an **Error Trigger** node.
2. Add an **HTTP Request** node configured as:
   - Method: `POST`
   - URL: `={{$env.N8N_ERROR_SLACK_WEBHOOK_URL || $env.SLACK_ALERTS_WEBHOOK_URL}}`
   - Body Content Type: `JSON`
   - JSON Body:

```json
{
  "text": "*n8n workflow failed*\n• workflow: {{$json.workflow.name}}\n• execution: {{$json.execution.id}}\n• error: {{$json.error.message}}"
}
```

This is compatible with Slack Incoming Webhooks.

