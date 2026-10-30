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
