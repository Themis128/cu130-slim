# human-gate-ops — drive the remaining consent gates programmatically

One dispatcher for every gate that's left after the money-loop automation:
each gate has a lane-check (is its session/credential live?), a driver
(automated action), and a store (where the result lands). Runs from
`.devin/skills/human-gate-ops/scripts/gate_drive.py`.

## Usage

    python3 scripts/gate_drive.py all           # status of every gate
    python3 scripts/gate_drive.py fb-reset      # fire one gate

## Gates

| Gate | Lane | Driver |
|---|---|---|
| `fb-sidecar` | `FACEBOOK_USERNAME` + `FACEBOOK_PASSWORD` in env/k8s | POST /login on the sidecar |
| `fb-reset` | **unattended** — request reset on sidecar, self-read code off dovecot IMAP, set new password, `k8s_secret_sync push` | full forgot-password chain |
| `linkedin-sidecar` | `LINKEDIN_USERNAME` + `LINKEDIN_PASSWORD` | POST /login on the sidecar |
| `x-oauth` / `tiktok-oauth` | bridge session + admin token | mint authorize URL → bridge navigate → callback |
| `wa-verify` | whatsapp bridge session | wa_verify_daemon retries hourly |
| `totp` | manual | `totp-ops/scripts/totp_setup.py` |
| `meta-submit` | manual | `meta_app_review_draft.py` prints paste text |
| `infisical` | manual | dashboard → Machine Identities → `k8s_secret_sync push` |

## Companion

- `oauth_reauth_watch` Celery beat (5min) — durable version of the
  OAuth gates; fires the same bridge-drive when a session appears.
- `credential-reset-ops` — the forgot-password primitives `fb-reset` uses.
- `k8s_secret_sync` — every minted credential pushes to cloudless-secrets.
