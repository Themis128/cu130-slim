---
name: linkedin-devapp-ops
description: >
  LinkedIn developer-app lifecycle automation via the CDP lane (:9229) —
  create apps, associate/verify the company page (generate URL → approve
  in-session), business-email verify, request products, read sole-product
  tooltip gates, submit access-request forms. Proven end-to-end on
  cloudless-gr-live (265081467) and cloudless-gr-dma (265077464).
---

# LinkedIn Dev-App Ops

Everything the products page makes you do by hand — automated.

## Rules learned the hard way

- **CDP lane is `:9229`** (`chrome-debug-profile`), never `:9222` (Lenovo
  Vantage WebView2 claims it). All scripts take `$CDP_URL`.
- **Sole-product rule applies to more than DP**: Live Events + Community
  Mgmt + all Data Portability products require the app to have NO other
  products. Read the disabled-button tooltip (`aria-describedby` →
  hoverable element) — it names the exact gate ("must be the only
  product…", "company association must be verified").
- **Create-a-new-app for any sole-product product.** One product per app.
- **Email verification does NOT persist across modal opens.** The request
  flow must run email→Send→code→Confirm→ToS checkbox→Request access in
  ONE modal session or it restarts the email step every time.
- **Logo gate**: minimum 100×100px; a generated solid-color PNG works.
- **Company typeahead**: native setter types the text but the Ember list
  needs a real `Input.dispatchMouseEvent` click on the option rect —
  synthetic `option.click()` doesn't commit.
- **Q_DL binding**: a product's Qualtrics form MUST be opened via the
  app's own products-page link (the `Q_DL` param binds the response to
  the app). Bare `SV_*` links orphan submissions. Qualtrics dedupe
  (intro-only render, 0 questions) means the survey already registered a
  response for that session — clear `Storage.clearDataForOrigin` for the
  qualtrics origin to re-open.
- **Access request forms stay visible after submission** until LinkedIn
  reviews — link presence ≠ unsubmitted. "Review in progress" on the
  products page is the true state.

## Tools (all CDP :9229)

`li_product_state.py <app_id> [<app_id>…]` — per-app product state table
(added / Access request form / Review in progress / Request access /
Access denied). Authoritative for what still needs action.

`li_devapp_ops.py` — full lifecycle driver:
- `create <name>` — /apps/new form: name, cloudless.gr company
  (typeahead + real-mouse), privacy URL, 256px logo via
  `DOM.setFileInputFiles`, ToS, Create → lands on the new app.
- `verify-company <app_id>` — Settings → Verify → Generate URL →
  opens the URL in-session and clicks Verify (page-admin session).
- `request <app_id> <product-heading>` — card Request access → email
  verify (fetches the 6-digit code from mail via fetch-code.py) →
  ToS checkbox → Request access, one modal session.
- `fill-form <qdl_url>` — opens a form via its app-bound Q_DL link,
  fills via the JFE API (`QuestionData.getInstance` → setChoiceValue /
  setTextValue → `clickNextButton()`), closes the tab on Thank-you.
- `tooltip <app_id> <product-heading>` — prints the disabled-button's
  gate reason verbatim.

Company/email values are constants at the top of the script — edit for
another org.

## Product ↔ app map (2026-11-05)

| App | Products |
|---|---|
| 265077368 `cloudless-gr-member` | Share, OIDC, Events, CM/MA/Conv/LeadSync/AdLib/Adv in review |
| 264925843 `Cloudless Analytics` | +CM provisioned, AdLib provisioned; same reviews |
| 265077464 `cloudless-gr-dma` | Member DP (3rd Party) — sole-product, in review |
| 265081467 `cloudless-gr-live` | Live Events — sole-product, form submitted |
| 227354605 | AdLib/MA/Adv/Events ok; Conversions+LeadSync **denied** |
