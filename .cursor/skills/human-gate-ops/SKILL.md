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

## FB login-approval automation (2026-11-13)

Meta's "Was this you?" checkpoint (pushed to the phone FB app AND shown on
facebook.com/notifications in the user's Windows Chrome) now has three
automated lanes:

1. **Detection** — `app/worker/tasks/human_gate.py` (`run_human_gate_watch`,
   30min beat) polls `facebook-sidecar:9226/session/validate`. Since
   2026-11-13 it treats `logged_in:false` / `profile_picker:true` as blocked
   even when `status:"ok"` — the login-wall payload used to score healthy.
   Nudges ops Slack at most once per 6h per gate, "cleared" notice on
   recovery.
2. **Auto-approve** — `~/scripts/fb-approval-watch.sh` (WSL cron `*/5`)
   watches `~/logs/ig-fb-session-heal.log` for approval-wait windows, then
   drives the user's real Windows Chrome via `win_agent`
   (`http://172.22.144.1:8765/exec`) + `C:\tmp\fb_win_ocr.ps1` (UIA+OCR)
   to click "This was me". Deny-pattern words on screen abort the click;
   max 3 attempts per approval window (state `~/.local/state/fb-approval-watch`).
   Log: `~/logs/fb-approval-watch.log`.
3. **Phone fallback** — the checkpoint also lands as a Facebook
   notification on the Galaxy S22+; approving there is the manual path
   (deliberately NOT automated — it's an account-security decision).

Boundary: the OCR clicker only fires inside heal-script approval windows;
it does not approve arbitrary login challenges outside them.
