# x-oauth-wire

Reauthorize the X/Twitter account after pasting real `TWITTER_CLIENT_ID`/`TWITTER_CLIENT_SECRET` into `.env`. One-shot script.

## Usage

```bash
python .devin/skills/x-oauth-wire/scripts/wire.py <client_id> <client_secret>
```

Writes env, restarts `social-api`, prints the reauth URL for the user to click, verifies the token lands.

## What it does

1. Backs up `.env`, sets `TWITTER_CLIENT_ID` + `TWITTER_CLIENT_SECRET`
2. `docker compose up -d social-api` to reload env
3. Calls `GET /api/v1/oauth/twitter/authorize` → prints the auth URL (user clicks → callback stores token)
4. Polls `social_accounts` for a fresh twitter token + verifies decrypt works
