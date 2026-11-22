# viber-ops

Wire and manage the Viber bot channel in SocialAuto.

## The one human input

Viber bot tokens only exist inside **partners.viber.com** with the user's
Viber-registered login (QR scan from the phone app). No API path exists to
create a bot — it's a single paste:

1. partners.viber.com → create/select bot → copy **App Token**
2. `viber_provision.py --token <token>` → validates, stores account, sets webhook

## Status 2026-11-08 — partners.viber.com is RETIRED

Verified live (browser + ADB on the S22+):

- `partners.viber.com/account/edit-bot-account` → "This site is no longer
  available, please refer to your app to see your bots details."
- `webadmin.viber.com` → redirects to Google. Dead.
- Viber app (com.viber.voip) on the phone: **no bot exists** — chat search
  for "cloudless" returns nothing, and Settings has **no Bots section**
  (checked Settings → Account and General; the documented
  "More > Settings > Bots > Edit Info > Your app key" path only appears for
  accounts that already administer a bot).
- Since 2024-02-05, new Viber bots are **commercial-only** — self-serve
  creation is closed. Apply via Rakuten Viber / an official partner
  (respond.io docs point at cecile.tizon@viber.com); approval issues the
  token. Legacy bots (pre-2024-02) still show the token in-app under
  Settings → Bots — this account has none.
- "Business Account" in the app (More tab) creates a *business profile*,
  not a REST-API bot — it does not yield an auth token.

**Bottom line:** the Viber gate is a commercial application, not a
credential lookup. Once Rakuten/partner issues a token, the rest is
already automated: `viber_token_fetch.py` (CDP+validate+store) →
`viber_provision.py` → webhook live.

## Tools

```bash
# wire a fresh token (connect + validate + webhook)
python3 .devin/skills/viber-ops/scripts/viber_provision.py --token <TOKEN>

# check account + webhook state
python3 .devin/skills/viber-ops/scripts/viber_provision.py --verify

# backend diagnostics
docker exec social-api python /app/scripts/viber_tool.py accounts|info|webhook|verify-sig
```

## Notes

- `POST /api/v1/viber/connect` takes `{auth_token, set_webhook}` — team comes
  from the caller's auth, not the body.
- Webhook lands at `/api/v1/viber/webhook/{account_id}` — signature-verified.
- `social_accounts` may carry a `revoked` personal row — safe to purge.
