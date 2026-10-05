# SocialAuto standalone deploy

Runs the full SocialAuto stack from published GHCR images on any Docker host —
no source checkout required. Intended for remote nodes (e.g. omv) or recovery.

## Usage

```bash
cp ../../.env.example .env   # fill in real secrets + platform credentials
# Required: POSTGRES_PASSWORD, N8N_BASIC_AUTH_PASSWORD, SOCIAL_ADMIN_EMAIL,
# SOCIAL_ADMIN_PASSWORD, and whatever platform tokens the deployment needs.
docker compose up -d
```

## Notes

- The browser sidecar images (`cu130-slim-*-sidecar`) are **not** published —
  build them on the target host from the main repo or pre-load them.
- Inline `environment:` entries pin internal service URLs; everything else
  comes from `.env` (see repo-root `.env.example`).
- GPU services need the NVIDIA container toolkit; drop
  `social-media-comfyui-gpu` on CPU-only hosts.
- `social-api-gateway` publishes the API on `:80`; `social-metrics` exposes
  only `/metrics` on `:9390` for Prometheus scrape.
