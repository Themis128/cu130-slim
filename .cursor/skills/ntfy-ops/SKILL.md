# ntfy-ops

Server-side operations for the self-hosted ntfy (k3s `ntfy` namespace on
`github-omv`, NodePort `80:30080`, `auth_default_access: deny-all`). Pairs
with **ntfy-phone-ops** (device side).

## When to use

- A publisher's alerts aren't arriving (URL/auth/ACL problem)
- Granting a topic to a user, rotating a password, minting a publish token
- Auditing which services publish to ntfy and whether their path works
- Checking topic health end-to-end (publish → reachable subscribers)

## Tool

`scripts/ntfy_ops.py` — talks to the pod over SSH (`ssh github-omv`, kubectl
exec) and to the HTTP API for verification:

```bash
python ntfy_ops.py users                 # list users + ACLs
python ntfy_ops.py grant phone cloudless-alerts rw
python ntfy_ops.py passwd phone          # rotate pw (NTFY_PASSWORD env)
python ntfy_ops.py token cloudless-app   # mint non-expiring Bearer token
python ntfy_ops.py publish nextcloud-ops "test" --token tk_...
python ntfy_ops.py audit                 # scan repo for ntfy URLs + check each
```

## Traps (learned 2026-10-29)

- **`ntfy.cloudless.gr` is Cloudflare-Access gated** — interactive SSO a
  machine client can never answer; every `*.cloudless.gr` tunnel hostname is
  too. Publishers MUST use `http://<tailnet-or-lan>:30080` + auth, never the
  public hostname.
- `deny-all` means a topic needs BOTH a granted user AND credentials —
  "Connection error"/"Login required" = ACL, not network.
- `ntfy user change-pass` takes `NTFY_PASSWORD` env, NOT `--password`
  (hangs on stdin otherwise).
- Non-expiring publish tokens: `ntfy token add <user>` → `tk_…`;
  send as `Authorization: Bearer tk_…`.

## Alert fan-out (2026-11-12)

Every severity-routed alert now ALSO publishes to `cloudless-alerts` on
`https://ntfy.cloudless.gr` — the phone's ntfy app is the third always-on
lane alongside Telegram + Slack. Wired at the two choke points:

- `tg_alert.py send()` → `_ntfy_publish()` (all ~28 home-ops watchers)
- `slack_notifications.send_telegram_alert()` + `post_alert_to_slack()`
  + `post_digest_text_to_slack()` → `_post_ntfy_alert()` (backend Celery)

Severity → ntfy `Priority` header: critical=5, ops=4, intel=3, digest=2.
Publisher identity: ntfy user `alert-relay` — **write-only** on
`cloudless-alerts`, `nextcloud-ops`, `cloudless-ops`; never-expiring
Bearer token lives in repo `.env` as `NTFY_ALERT_TOKEN`
(+ `NTFY_ALERT_BASE_URL`, optional `NTFY_ALERT_TOPIC` override).

Corrections to the 2026-10-29 traps (verified live 2026-11-12):

- `ntfy.cloudless.gr` is **NOT Access-gated for API/subscription
  traffic** — machine publishes (Bearer) and the Android app's
  websocket both work over the public hostname. The earlier "must use
  tailnet :30080" guidance is obsolete for this hostname.
- **Cloudflare 403s bare python User-Agents** (urllib `Python-urllib`,
  httpx default) — every publisher must send a browser-ish UA header.
