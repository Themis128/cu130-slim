---
name: linkedin-portal-ops
description: Automate the LinkedIn Developer Portal (developers.linkedin.com/developers/apps/<id>) via CDP on the user's logged-in browser — redirect-URL edits, client-secret reads, product "Request access" modals. Use when consent fails on redirect_uri, when a client secret is needed, or when a product must be requested without a human click.
---

# LinkedIn Developer Portal automation (CDP)

## Prereq
User's Edge on Windows with `--remote-debugging-port=9222` and a logged-in
LinkedIn session that administers the apps. All driving is Runtime.evaluate
against a fresh tab via `li_portal_edit.py`.

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

## Scripts

- `li_portal_edit.py` — `auth <APP_ID> add-redirect <URL>`,
  `auth <APP_ID> get-secret`, `products <APP_ID> request "<product name>"`.
