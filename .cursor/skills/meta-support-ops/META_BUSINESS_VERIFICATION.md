# Meta Business Verification + App Review checklist

Run ONLY after: (1) IG `cloudless.gr` login restored, (2) Meta Account
suspension lifted/expired, (3) FB session healthy on debug Chrome :9229.
Submitting while flagged risks auto-rejection.

## Prerequisites (verify before starting)

- [ ] `browser_auth_probe.py` → facebook.com AUTHENTICATED on :9229
- [ ] IG session live (`meta_cookie_sync.py --check` → instagram OK)
- [ ] Meta Account not suspension-flagged (check Gmail for newer alerts;
      `meta_reply_watch.py` log clean)
- [ ] Business portfolio exists: business.facebook.com → Business settings
      shows portfolio (asset id seen in support flow: FB account 1134463867,
      Page `cloudless.gr`)
- [ ] Business email `baltzakis.themis@gmail.com` reachable; domain
      `cloudless.gr` email working (Google Workspace / Zoho?) for
      business-email verification

## Step 1 — Business verification (business.facebook.com/business-settings)

Security Centre → Start verification. Meta asks for:

- [ ] Legal business name — must match a public record or domain WHOIS
- [ ] Business address + phone (use a real, consistent address — mismatches
      are the #1 rejection cause)
- [ ] Website `https://cloudless.gr` — must be live, show the business name,
      and have a matching domain email OR phone on the page
- [ ] Documents (upload only if asked): business registration (ΓΕΜΗ/ΓΕΜΗ-
      equivalent Greek registry doc), utility bill or bank statement showing
      name+address, domain invoice. Keep PDFs under 5MB, English or with
      certified translation
- [ ] Verify business email/phone/domain: prefer **domain verification**
      (add TXT/HTML file/meta-tag to cloudless.gr DNS — Cloudflare) over
      email code; domain verify is instant and strongest signal

Common rejection reasons: name mismatch vs website/WHOIS, PO-box/virtual
address, website lacks business name, document older than 3 months, mixing
personal + business names.

## Step 2 — App Review → Advanced Access (developers.facebook.com)

App: the app behind `FACEBOOK_CLIENT_ID` (Meta app used by SocialAuto).

- [ ] App → App Review → Permissions and Features — request Advanced
      Access for each needed permission:
      - `instagram_basic`, `instagram_content_publish`,
        `instagram_manage_insights`
      - `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
        `pages_manage_engagement`, `read_insights`
      - `whatsapp_business_messaging` (+ `whatsapp_business_management`
        for WABA assets)
      - Threads: `threads_basic`, `threads_content_publish`,
        `threads_manage_insights` (separate Threads API product)
- [ ] Each permission needs a screencast + description of use. Prepare
      demo videos showing SocialAuto publishing/analytics flows BEFORE
      submitting — weak demos are the top rejection cause.
- [ ] Business verification must be approved first for most advanced
      permissions.

## Step 3 — WABA / WhatsApp Business (fixes the +**38 delivery issue)

The missing-code problem may partly be an unverified/misconfigured WABA:

- [ ] Business Settings → WhatsApp accounts → confirm WABA exists, display
      name approved, `+**38` number registered to it
- [ ] If number isn't on WABA: add it (SMS/voice verification to the phone)
- [ ] Resolve the known WABA system-user issue (escalated Nov 22) —
      system user must have `whatsapp_business_messaging` asset access
- [ ] After verification: Meta login codes may route through WhatsApp
      Business instead of consumer WhatsApp — test delivery

## Step 4 — IG Professional conversion (post-login, pre-API)

- [ ] cloudless.gr → Settings → Account type → Switch to professional
      (Business or Creator — Business preferred for Graph API)
      - Driver: `ig_acct_drive.py` after session confirmed live
- [ ] Link IG professional account to the FB Page `cloudless.gr` and to
      the Business portfolio

## Step 5 — Post-verification validation

- [ ] `meta_tool.py debug-token --account instagram` → scopes include
      advanced permissions, `expires_at` long-lived
- [ ] `ig_lane_doctor.py` → lane healthy
- [ ] `devapps-sync` run → `social_accounts` rows refresh, health=ok
- [ ] `browser_auth_probe.py` → all Meta surfaces AUTHENTICATED
- [ ] Update AGENTS.md credential-state section + close related backlog
      items

## Escalation contacts if rejected

- Business verification rejection → Request review in Business Settings
  (one appeal per rejection; fix the cited reason first)
- App Review rejection → appeal via App Dashboard → Alerts; cite the
  specific policy, attach better screencast
- Account-level issues → `fb_escalate.py` issue keys already staged;
  Meta support case pending manual finish (business-support surface needs
  Meta Account login — see meta-support-ops SKILL.md)
