# viber-ops

Wire and manage the Viber bot channel in SocialAuto.

## The one human input

Viber bot tokens only exist inside **partners.viber.com** with the user's
Viber-registered login (QR scan from the phone app). No API path exists to
create a bot — it's a single paste:

1. partners.viber.com → create/select bot → copy **App Token**
2. `viber_provision.py --token <token>` → validates, stores account, sets webhook

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
