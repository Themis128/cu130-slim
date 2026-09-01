# Social Profile Secrets & App-Mediated Login

## Goal

Store all social platform credentials in a Cloudflare-first secret store so
SocialAuto can log in and update profiles without requiring manual `.env`
editing. Provide local PostgreSQL and `.env` failover, encrypted-at-rest
storage, and platform-specific login paths for Instagram, Facebook, LinkedIn,
Twitter/X, and TikTok.

## Architecture

```mermaid
graph LR
    A[SocialAuto Frontend] -->|POST /api/v1/secrets| B(Social API)
    B --> C{Cloudflare D1}
    B --> D[PostgreSQL local]
    B --> E[.env read-only fallback]
    C -- unavailable --> D
    D -- unavailable --> E
    B --> F[Instagram Private API sidecar]
    B --> G[Playwright browser]
    B --> H[Twitter/X tweepy]
    B --> I[TikTok private API]
    F --> J[Instagram]
    G --> K[Facebook Web]
    G --> L[LinkedIn Web]
    H --> M[Twitter API v1.1]
    I --> N[TikTok]
```

## Credential write flow

```mermaid
sequenceDiagram
    participant U as User
    participant FE as ProfileEditor
    participant API as /api/v1/secrets
    participant SS as secret_store
    participant D1 as Cloudflare D1
    participant PG as PostgreSQL

    U->>FE: enter username / password
    FE->>API: POST /secrets/{key}
    API->>SS: set(key, value)
    SS->>D1: try write
    D1--xSS: unavailable
    SS->>PG: write encrypted
    SS->>U: {key, sources: {d1:false, postgres:true, env:false}}
```

## Login flow

```mermaid
sequenceDiagram
    participant U as User
    participant FE as ProfileEditor
    participant API as /api/v1/profile/{id}/login
    participant SS as secret_store
    participant Platform as Platform adapter
    participant Ext as External service

    U->>FE: click Log in
    FE->>API: POST /login {verification_code?}
    API->>SS: get(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    SS-->>API: username, password
    API->>Platform: login(username, password, code?)
    Platform->>Ext: authenticate
    Ext-->>Platform: session / 2FA required
    Platform-->>API: {logged_in, two_factor_required, session_id}
    API-->>FE: status
```

## Secret store fallback chain

| Store | Role | Write | Read fallback |
|-------|------|-------|---------------|
| Cloudflare D1 | Primary | Yes | First |
| PostgreSQL | Local failover | Yes (when D1 down) | Second |
| `.env` | Static fallback | No (read-only) | Third |

## Per-account credentials

Global keys (e.g. `INSTAGRAM_USERNAME`) can be overridden with per-account keys:

```text
INSTAGRAM_USERNAME_{account_id}
INSTAGRAM_PASSWORD_{account_id}
FACEBOOK_USERNAME_{account_id}
FACEBOOK_PASSWORD_{account_id}
LINKEDIN_USERNAME_{account_id}
LINKEDIN_PASSWORD_{account_id}
```

## Supported secret keys

| Key | Used for | Provider |
|-----|----------|----------|
| `INSTAGRAM_USERNAME` / `INSTAGRAM_PASSWORD` | `aiograpi-rest` login | Instagram private API |
| `INSTAGRAM_PROXY` | optional mobile/residential proxy | Instagram private API |
| `FACEBOOK_USERNAME` / `FACEBOOK_PASSWORD` | Playwright login | Facebook web |
| `LINKEDIN_USERNAME` / `LINKEDIN_PASSWORD` | Playwright login | LinkedIn web |
| `TWITTER_API_KEY` / `TWITTER_API_SECRET` | `tweepy` v1.1 | Twitter/X API |
| `TWITTER_ACCESS_TOKEN` / `TWITTER_ACCESS_TOKEN_SECRET` | `tweepy` v1.1 | Twitter/X API |
| `TIKTOK_PRIVATE_API_KEY` | signing server | TikTok private API |

## Security notes

- Values are encrypted at rest with `ENCRYPTION_KEY` before writing to D1 or Postgres.
- List/get raw responses require authentication. List responses mask values as `***`.
- `hide_parameters=True` on the SQLAlchemy engine prevents secret values from appearing in DB logs.
- `.env` is read-only from the `social-api` container; writes go through the API.

## Operational scripts

```bash
# List all secret keys (values masked)
.devin/skills/social-profile-secrets/scripts/list-secrets.sh

# Save platform credentials
.devin/skills/social-profile-secrets/scripts/set-instagram.sh <username> <password>
.devin/skills/social-profile-secrets/scripts/set-facebook.sh <email> <password>
.devin/skills/social-profile-secrets/scripts/set-linkedin.sh <email> <password>
.devin/skills/social-profile-secrets/scripts/set-twitter.sh <key> <secret> <token> <token_secret>
.devin/skills/social-profile-secrets/scripts/set-tiktok.sh <api_key>

# Log in to a connected account
.devin/skills/socialauto-profile/scripts/login.sh <account-id> [2fa-code]

# List connected account IDs
.devin/skills/socialauto-accounts/scripts/list-accounts.sh
```

## Platform login requirements

| Platform | Credentials | 2FA/challenge | Notes |
|----------|-------------|---------------|-------|
| Instagram | username, password | SMS/email/TOTP | Often needs a residential/mobile proxy to avoid `challenge_required` |
| Facebook | email/phone, password | SMS/email/approvals | Headless browser may need proxy and correct cookie consent handling |
| LinkedIn | email/phone, password | SMS/email | Headless browser may be blocked by bot detection; proxy recommended |
| Twitter/X | API v1.1 key/secret + tokens | - | Requires paid Basic/Pro tier for profile writes |
| TikTok | private API signing key | - | Username/password not currently used; signing key is required |
