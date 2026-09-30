# Project skills & tools

Agent skills live under `.cursor/skills/`. Each skill may include `scripts/` tools.

| Skill | Use when | Tools |
|-------|----------|-------|
| `cloudless-carousel-pipeline` | CF LinkedIn carousel / NLP / CF models | `scripts/run-pipeline.py` |
| `n8n-cloudless` | n8n deploy, webhook, API key | `deploy-workflow.py`, `trigger-webhook.py`, `refresh-api-key.py` |
| `social-stack-ops` | Compose health / restarts | `stack-status.py` |
| `tiktok-console-ops` | TikTok domain verify, audit readiness, sidecar session, console drift | `check-config.py`, `sidecar-session.py`, `console-inspect.py`, `domain-verify.py`, `dns-tiktok-txt.py`, `tiktok-console-mcp-server.py` |
| `linkedin-api-upgrade` | LinkedIn API scopes / use-case | `check-scopes.py`, `generate-use-case.py` |
| `social-profile-update` | Profile/bio updates across platforms | `get-all-profiles.py`, platform update scripts |
| `omv-ha-mail` | Local mail queue / SMTP | `mail-queue.py`, `send-mail.py`, `read-inbox.py` |

Rules (auto context): `.cursor/rules/cloudless-social-stack.mdc` (+ file-scoped rules).

MCP (`.devin/mcp_config.json`): `playwright` (Docker), `socialauto`, `tiktok-console`, `dmr`, `github-local`.

Repo scripts still used by skills:
- `scripts/deploy_n8n_cloudless_carousel.py`
- `scripts/init-n8n-api-key.py`
- `n8n-workflows/cloudless-carousel-pipeline.json`
