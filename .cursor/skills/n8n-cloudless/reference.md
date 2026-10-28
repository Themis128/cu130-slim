# Cloudless n8n fine-tune notes

## Product defaults baked into the workflow

| Setting | Value |
|---------|--------|
| Timezone | `Europe/Athens` |
| Schedule | every **2 days at 19:00** local |
| Author | Company Page `9c4451bb-e820-489f-8676-76ddbc788ffe` |
| Slides | 7 (override via env/body) |
| Models | CF Llama + FLUX schnell + FLUX.2 klein-4b |
| `wait_for_publish` | `false` by default (webhook returns after queue) |

## Topic rotation

If webhook body has no `topic` and `CLOUDLESS_CAROUSEL_TOPIC` is empty, n8n rotates daily through cloudless.gr themes (serverless, Workers, Greek/EU SMEs, plain English).

## Webhook body

```json
{
  "topic": "optional override",
  "num_slides": 7,
  "publish": true,
  "wait_for_publish": false,
  "tone": "clear and friendly",
  "target_account_id": "9c4451bb-e820-489f-8676-76ddbc788ffe"
}
```

## Success response shape

```json
{
  "ok": true,
  "brand": "cloudless.gr",
  "post_id": "...",
  "status": "scheduled",
  "platform_url": null,
  "nlp_fixed": true
}
```

## Instance AI vs carousel

n8n Instance AI may still use Ollama for editor help. **Carousel generation always goes through social-api → Cloudflare Workers AI** — never Ollama/ComfyUI on this path.
