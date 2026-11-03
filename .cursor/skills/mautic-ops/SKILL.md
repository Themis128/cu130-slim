# mautic-ops — operate the Mautic instance (mautic.cloudless.gr)

Self-hosted Mautic 6 in compose (`mautic` + `mautic-db` + `mautic-cron`), fronted
by Cloudflare Tunnel + SSO at `mautic.cloudless.gr`.

## Critical: two ways in, only one works for API

- **External `https://mautic.cloudless.gr`** → Cloudflare Access/SSO gate → API
  calls redirect-loop to the SSO login. UI access only.
- **Container-direct `http://mautic:80`** → reaches the API cleanly. This is the
  ONLY API path. Requires headers `Host: mautic.cloudless.gr` +
  `X-Forwarded-Proto: https` (canonical-host check — Mautic 301s/500s without).

## API enablement gotchas (all hit + fixed 2026-11-02)

Mautic's API is OFF until configured — the UI toggle writes these to
`/var/www/html/config/local.php` (a flat `return [...]` array, insert before `);`):

```php
'api_enabled' => true,
'api_enable_basic_auth' => true,           // needed for basic auth
'trusted_proxies' => ['0.0.0.0/0'],         // honors X-Forwarded-* → stops http→https 301s
```

Then: `docker exec mautic php /var/www/html/bin/console cache:clear --no-interaction`.

**Failure symptoms → cause:**
- `403 api_disabled` → api_enabled missing
- `301` loop on container-direct → trusted_proxies missing
- `500` on POSTs → `var/cache/prod/jms_serializer_default` root-owned after
  cache:clear — `chown -R www-data:www-data /var/www/html/var/cache /var/www/html/var/logs`
- "alias already in use" → segment exists (idempotent-safe, fetch its id)

## API surface (Mautic 6, base `http://mautic:80/api`)

| Op | Route |
|---|---|
| List/create contacts | `GET/POST /contacts`, `POST /contacts/new` |
| Upsert contact | `PUT /contacts/new` (upsert semantics) or POST with existing email |
| Segments | `GET /segments`, `POST /segments/new`, `POST /segments/{id}/contact/{cid}/add` |
| Emails | `GET /emails`, `POST /emails/new` (needs name+subject+language+lists+emailType) |
| Campaigns | `GET /campaigns`, `POST /campaigns/new`, `POST /campaigns/{id}/contact/{cid}/add` |
| Send email to contact | `POST /emails/{id}/contact/{cid}/send` |
| Stats | `GET /emails/{id}` → sentCount/readCount |

Auth: HTTP Basic with a Mautic admin user (`MAUTIC_USERNAME`/`MAUTIC_PASSWORD`).
OAuth2 is the recommended alternative (API Credentials in UI) — basic is what
the stack uses today.

## The client

`app/services/mautic_client.py` already wraps this correctly: `_cfg()` reads
MAUTIC_BASE_URL/USERNAME/PASSWORD/PUBLIC_HOST, `_headers()` sends Host +
X-Forwarded-Proto, `mautic_post()`, `upsert_contact()`, `create_segment()`,
`add_to_segment()`, `funnel_state()`. Reuse it — do NOT call the external URL.

## Funnel wiring already live

`scripts/mautic_sync.py --apply` (hourly `funnel-sync` beat): EspoCRM `/Lead`
→ Mautic contacts + `crm-sync` + `crm-hot/warm/cold` tier segments; Postgres
`leads` hot (interest=audit / thread / 7d) → hot tier. Junk filter skips
example/test/.local emails.

## Tool

`scripts/mautic_tool.py` — run inside social-api:
`status | segments | contacts [limit] | create-segment NAME ALIAS |
 upsert EMAIL FIRST LAST COMPANY | add-to-segment SEGMENT_ID EMAIL |
 campaigns | emails | send EMAIL_ID CONTACT_EMAIL`

## Gotchas

- Never call `https://mautic.cloudless.gr/api/*` from code — SSO redirects.
- `funnel_state()` (`--status`) is the quick health check: contacts+segments count.
- Mautic must be `healthy` before API calls; `cache:clear` after any local.php edit.
