---
name: linkedin-graph-ops
description: LinkedIn ops tooling via the official linkedin-api-client — OAuth2 token introspection (real scopes/expiry), the live endpoint access matrix per account, and ad-account discovery. Use for LinkedIn token health, follower-statistics endpoint truth checks, Marketing API reads, or verifying which tier features a token can reach.
---

# LinkedIn Graph Ops (official linkedin-api-client)

Official `linkedin-api-client` (github.com/linkedin-developers/linkedin-api-python-client,
in pyproject deps) installed in the backend image.

## Tool: `social-automation/backend/scripts/linkedin_tool.py`

Complements `scripts/linkedin_cli.py` (content gen/publish/followers — unchanged).
Runs inside social-api; never prints tokens.

```bash
docker exec social-api python3 /app/scripts/linkedin_tool.py accounts
docker exec social-api python3 /app/scripts/linkedin_tool.py token-inspect --account <uuid>
docker exec social-api python3 /app/scripts/linkedin_tool.py access-matrix --account <uuid>
docker exec social-api python3 /app/scripts/linkedin_tool.py ad-accounts  --account <uuid>
```

- `token-inspect` — `AuthClient.introspect_access_token` (needs
  `LINKEDIN_CLIENT_ID`/`LINKEDIN_CLIENT_SECRET`): returns `active`, `status`,
  full `scope` list, `expires_at`/`created_at`/`authorized_at` epoch, `client_id`,
  `auth_type`. The authoritative token-health check — `/v2/userinfo` (what
  `LinkedInAPIClient.validate_token` uses) proves the token works but hides
  scopes and expiry.
- `access-matrix` — probes the endpoint variants we depend on and prints
  `endpoint \t status \t hint`. This is the live version of the
  follower-analytics-guard truth table: `userinfo`, `networkSizes` (org
  followers), `organizationalEntityFollowerStatistics`, `organizationAcls`,
  `organizations`, `socialMetadata`. A 403 on socialMetadata = Community
  Management API tier not granted yet (see `linkedin-api-upgrade` skill).
  Person-type accounts correctly fail the org endpoints (id isn't a Long).
- `ad-accounts` — Marketing API `adAccounts?q=search`. Verified: reads the
  `512642510` ad account via API (r_ads scope granted) — prefer this over
  Campaign Manager sidecar automation for read-only checks.

## Rest.li / versioned-API gotchas (learned live)

- `LinkedIn-Version` is a **request header** — passing it as a query param
  gives `400 VERSION_MISSING` on every `/rest/` endpoint.
- URNs in path segments must be percent-encoded
  (`urllib.parse.quote(urn, safe='')`) — colons break path parsing
  (`400 ILLEGAL_ARGUMENT Syntax exception in path variables`).
- Rest.li finder params are parenthesized strings:
  `?q=search&search=(status:(values:List(ACTIVE,DRAFT)))` — don't let httpx
  re-encode them into JSON-ish dicts.
- `AuthClient` method is `introspect_access_token`, response fields are
  attributes (`.active`, `.scope`, …), not `.entity`.

## Repo notes

- `linkedin-developers/linkedin-api-python-client` — installed.
- `linkedin-developers/linkedin-api-js-client` — JS equivalent if a sidecar
  ever needs it.
- `tomquirk/linkedin-api` (unofficial Voyager client) — NOT installed:
  cookie-auth private API, ToS risk; browser sidecars already cover
  non-API paths. Reference only.
- LinkedIn's org (`github.com/linkedin`) is infra (Kafka, Pinot, etc.) —
  nothing applicable to SocialAuto.
