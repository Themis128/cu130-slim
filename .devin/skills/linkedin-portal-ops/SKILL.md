---
name: linkedin-portal-ops
description: Automate the LinkedIn Developer Portal (developers.linkedin.com/developers/apps/<id>) via CDP on the user's logged-in browser — redirect-URL edits, client-secret reads, product "Request access" modals. Use when consent fails on redirect_uri, when a client secret is needed, or when a product must be requested without a human click.
---

# LinkedIn Developer Portal automation (CDP)

## Prereq
Debug Chrome on Windows with remote debugging and a logged-in LinkedIn
session that administers the apps. All driving is Runtime.evaluate against
a fresh tab via `li_portal_edit.py`.

**Port: 9229, not 9222** — the Lenovo Vantage WebView2 on this host claims
127.0.0.1:9222 at boot (respawns on kill; `DebuggingPortEnabled` reg flag +
webview default per MS docs). Our lane uses `start-chrome-debug.bat` →
:9229; every script takes `$CDP_URL` (default http://localhost:9229).

## What the portal really looks like (verified 2026-11-05)

- **Auth tab** (`/developers/apps/<APP_ID>/auth`): client_id input
  (`#clientIdInput`), primary secret (`#primaryClientSecret`, type=password —
  click the section "Show" button first to read .value), "Authorized redirect
  URLs" block renders EMPTY inputs until you click the section's **pencil**
  (`.editable-block__edit-btn button`). Pencil → "Add redirect URL" button →
  input appears → fill via native setter + char-by-char input/keydown/keyup
  events + change + blur → "Update". A plain value-set + single input event
  gives "Please enter a valid redirect URL" — Ember needs the full event storm.
- **Products tab**: each gated product row has a "Request access" button that
  opens a modal (business-email verify, then the form). Only clickable when the
  app is company-verified.
- Page verification: requires a Page admin confirming in the portal — NOT
  automatable from the app admin side.

## OAuth consent error classes (why probes lie)

- **authorize-load rejection**: the authorize URL renders an error page —
  `li_scope_probe.py` catches this reliably.
- **consent-submit rejection**: authorize looks fine, LinkedIn redirects to
  your callback with `?error=unauthorized_scope_error` AFTER click. Probes
  CANNOT see this class without a live consent. Remedy:
  `li_consent_trim.py` — strip the named scope from the staged URL's scope=
  param and re-click; iterate per error_description until it lands.
- LinkedIn grants REPLACE the whole set on every consent — always send the
  full intended union minus the submit-rejected scopes.

## Hard-won portal behavior (verified 2026-11-05)

- Product cards render `Request access` **class-disabled** (`artdeco-button
  --disabled`, `disabled` attr stays null) — hover the button's
  `aria-describedby` element to read the real gate. Gates seen:
  - *"requires that it be the only product on the application"* → DP products
    need a dedicated app (create at `/developers/apps/new`; fields
    `createAppNameInput`, `associateCompanyTypeaheadInput` — Ember typeahead
    commits on **real mouse event** at the option's rect, not `.click()`;
    a `https://linkedin.com/company/<id>` Page URL also validates; logo upload
    required — `DOM.setFileInputFiles` with a generated PNG works).
  - *"company association to be verified"* → Settings tab → Verify →
    Generate URL → open `/developers/apps/verification/<uuid>` in the same
    browser and click Verify → Confirm (page admin is already logged in).
- Modal dialogs are `div.artdeco-modal--layer-default`, NOT `[role=dialog]` —
  probes for `[role=dialog]`/`dialog` return empty while the modal is open.
- Business-email code modal: `input#businessEmailInput` (gmail/personal
  domains rejected) → send code → fetch from Maildir → fill + Confirm.
- `Input.dispatchMouseEvent` at the button's `getBoundingClientRect`
  viewport coords opens modals reliably; OS-level `mouse_event` via win_agent
  needs browser-chrome height (`outerHeight-innerHeight`) added to y and a
  truly foregrounded window — and clicks land on whatever window is on top
  (Zoom nearly ate them). Prefer CDP Input events.
- Submitting the request modal needs `legalTermsCheckbox` checked first;
  after submit the product card flips to an "Access request form" link
  (Qualtrics `linkedincustomerops.qualtrics.com/jfe/form/SV_*`) →
  `qualtrics-ops` fills it.
- Tabs wedge after heavy navigation — `Runtime.evaluate` then times out.
  Fix: close the target via browser ws and open a fresh tab.

## Scripts

- `li_portal_edit.py` — `auth <APP_ID> add-redirect <URL>`,
  `auth <APP_ID> get-secret`, `products <APP_ID> request "<product name>"`.
