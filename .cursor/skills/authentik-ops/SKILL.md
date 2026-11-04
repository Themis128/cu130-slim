# authentik-ops

Operate authentik at `https://auth.cloudless.gr` (SocialAuto OIDC via `social_client` + `authentik-outpost`).

## Tokens
- `AUTHENTIK_OUTPOST_TOKEN` (.env) — outpost-scoped, **403 on /api/v3/**. Not usable for admin ops.
- `AUTHENTIK_ADMIN_TOKEN` — create: admin UI → Directory → Tokens & App passwords → Create → Intent: API Token → admin user. Add to `.env`.

## API
- `GET /api/v3/core/applications/` — list (results[]: slug, name, meta_description)
- `PATCH /api/v3/core/applications/<slug>/` — set `meta_description` (ops facts on the app card)
- `Authorization: Bearer <tok>`; never print it.

## Scripts
- `scripts/update_app_meta.py --env <path> --app <slug> --note "..."` — PATCHes meta_description; exits 2 with token-creation instructions when only the outpost token exists.
