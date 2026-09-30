# Project skills & tools

Agent skills live under `.devin/skills/`. Each skill may include `scripts/` tools.

| Skill | Use when | Tools |
|-------|----------|-------|
| `cloudless-carousel-pipeline` | CF LinkedIn carousel / NLP / CF models | `scripts/run-pipeline.py` |
| `n8n-cloudless` | n8n deploy, webhook, API key | `deploy-workflow.py`, `trigger-webhook.py`, `refresh-api-key.py` |
| `social-stack-ops` | Compose health / restarts | `stack-status.py` |
| `linkedin-ads-ops` | LinkedIn Campaign Manager ads, promo credit, sponsored posts | `scripts/linkedin_cm.py` |

Rules (auto context): `.cursor/rules/cloudless-social-stack.mdc` (+ file-scoped rules).

Repo scripts still used by skills:
- `scripts/deploy_n8n_cloudless_carousel.py`
- `scripts/init-n8n-api-key.py`
- `n8n-workflows/cloudless-carousel-pipeline.json`
