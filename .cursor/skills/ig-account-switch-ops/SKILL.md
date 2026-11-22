# ig-account-switch-ops

Switch the **active Instagram account** on the Galaxy S22+ and convert it to
Professional (Business/Creator) over wireless ADB — the only fix for Graph API
`(#10) Application does not have permission` on `/media` + `/insights`, which
means the IG profile is still a consumer account.

## Why this exists

- Instagram API with Facebook Login **cannot access consumer accounts** —
  verified against Meta docs and live probes (`17841463022505300` = @cloudless.gr
  IG, token healthy, `(#10)` on media/insights).
- The account lives in Meta Accounts Center alongside `t_baltzakis` — but IG
  keeps ONE active session and the in-app switcher is not always reachable
  while an account is active (observed 2026-11-23: Settings had no
  "Switch accounts" entry; the switcher only appears on login/add-account
  screens on some builds).
- `ig-personal-privacy` covers the reverse direction (pro → personal) for
  `t_baltzakis`; this skill covers switching + personal → professional.

## Tool

```bash
python3 .devin/skills/ig-account-switch-ops/scripts/ig_acct_drive.py <cmd>
```

| Command | What it does |
|---|---|
| `status` | Preflight: ADB device, IG focus, flip-cover/lockscreen blockers, checkpoint markers on screen |
| `dump [--out F]` | List every text/content-desc node with tap coordinates |
| `find TEXT` / `tap-text TEXT` / `key CODE` / `wait TEXT` | UI primitives |
| `account` | Best-effort read of the active profile username |
| `switch USERNAME` | Live-verified path: profile → header-username tap → switcher sheet → target row → "Instagram from Meta" picker → target row → polls for success / `SESSION_DEAD` (login form = human gate) / checkpoint |
| `convert-pro business\|creator` | Drives Account type and tools → Switch to professional → kind picker → Done/Next chain |
| `verify` | Classifies the Account-type screen: professional vs still-personal |

## Rules (inherited from phone-ui-automation)

- Run `status` before EVERY interaction batch; stop on cover closed,
  lockscreen, or non-IG focus.
- Hard stop on checkpoint markers: SMS/verification code, 2FA, suspicious
  login, password prompts — those are human gates, never type credentials.
- Never log out to force the switcher unless the user approves — logout can
  trigger checkpoint flows on the remaining accounts.
- Prefer `dump` + read-back over blind taps; IG rebuilds layouts per build.

## The account switcher (verified live 2026-11-23)

IG exposes NO "Switch accounts" entry in Settings on this build — the real
path is: **Profile tab → tap the username in the header row** (the wide
clickable node at top, ~y 74-232) → bottom sheet lists linked accounts
(`t_baltzakis`, `cloudless.gr`, FB profile) → tap the target → an
"Instagram from Meta" profile picker appears → tap the target row again →
session restore (~10-20s, shows a Loading modal).

If the target's saved session is dead (e.g. after the Meta password
changes), IG lands on a login form instead — the script reports
`SESSION_DEAD`; credentials are a human gate.

Observed live: `cloudless.gr` row → picker → Loading modal → **silent
revert to t_baltzakis profile** — consistent with a dead saved session
for @cloudless.gr rather than a UI failure. If `switch` ends in TIMEOUT,
check for a login form manually before retrying.

## Resume path for @cloudless.gr (the live case)

1. `status` → must be OK with IG focused (launch via
   `adb shell monkey -p com.instagram.android 1` if not).
2. `switch cloudless.gr` → if `SESSION_DEAD`/TIMEOUT, the saved IG session
   is likely invalidated (Meta password change) → human re-login, or the
   `ig-emaillogin_session` / `ig2_oauth_drive` lanes in
   `ig-business-login-ops`.
3. `account` → confirm `cloudless.gr` is active.
4. `convert-pro business` → `verify` → then Graph probe:
   `GET /{ig-user-id}/media` and `/insights` on `17841463022505300` should
   clear `(#10)`; reconcile legacy row `28747382798219804` via
   `account_truth.py`.

## Related

- `phone-adb-ops` — ADB lanes/devices (`phone_adb.py`)
- `ig-personal-privacy` — professional → personal for `t_baltzakis`
- `instagram-lane-ops` — business_login vs fb_onboarding lane diagnostics
- `instagram-ops` — session/login/sessionid recovery
- `meta-account-lock-ops` — Meta checkpoint/lock escalation
