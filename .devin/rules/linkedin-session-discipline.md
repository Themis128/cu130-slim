# LinkedIn session discipline

Rule adopted 2026-11-21 after a same-day incident: three login variants tested
in a few hours on the :9229 debug lane got the session credential set flagged
server-side — every authenticated request was refused at HTTP level while
anonymous browsing on the same IP/profile kept working.

## The rule

- **Every fresh LinkedIn login is a flagging event.** Never run login-form
  experiments, probes, or "just to see" logins against `chrome-debug-profile`
  or any sidecar session. Capturing cookies is safe; minting sessions is not.
- **Long-lived `li_at` + a quiet browser is the durable state.** Do not
  re-login to "refresh" a working session — `li_at` is ~365d.
- **Flagged-credential signature**: anonymous nav to linkedin.com renders but
  any request carrying the session cookies is refused
  (`net::ERR_HTTP_RESPONSE_CODE_FAILURE`, often mislabeled as a Cloudflare
  challenge). `cf_clearance` is a separate layer — a fresh one does NOT heal
  a flagged session.
- **Cookie heal is useless once credentials are flagged** — the flagged pair
  follows the cookies across browsers/profiles. Only a fresh login mints a
  clean `li_at`+`bcookie`.
- **To heal**: wipe the origin storage on the debug profile
  (`Storage.clearDataForOrigin` + `Network.clearBrowserCookies`), confirm the
  login page renders anonymously, then ONE credential login — never a loop.
  `li_session_heal.py heal` does this and now caps at 3 credential logins/day;
  past the cap it alerts for a manual login instead.
- **Prefer the patchright sidecars** (`linkedin-browser-sidecar` :9225 etc.)
  for LinkedIn automation — they survive bot defense that flags the vanilla
  headless debug lane. New LinkedIn automation should not add more traffic
  through `:9229` than the existing healer needs.
