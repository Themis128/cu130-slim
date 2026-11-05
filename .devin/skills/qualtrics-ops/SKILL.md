---
name: qualtrics-ops
description: Map and fill LinkedIn Qualtrics access-request forms (linkedincustomerops.qualtrics.com/jfe/form/SV_*) via CDP on the user's Edge — section.question→QID discovery, text/textarea/radio/checkbox fill, custom Country dropdown, Next/Submit. No LinkedIn login needed.
---

# Qualtrics ops (LinkedIn access-request forms)

## Structure (verified on Lead Sync SV_cJ7GzOa8NFg7z8i + Matched Audiences SV_8CUnOsYiyMj0Qd0)

- Single-page JFE: questions are `section.question` with id `question-QID<n>`;
  radios are `mc-choice-input-QID<n>-<k>`; text inputs have no id — target by
  question section. Conditionally-required fields (alt legal name, other-app
  client id) are safe to leave blank when the triggering radio = No.
- Country field is a custom dropdown — click inside the section, then click
  the `[role=option]` matching the country.
- Ember/React inputs need native setter + input + change + blur events.
- Greek text in scripts: use `\uXXXX` escapes — Windows cp1252 consoles die on
  literal Greek.

## Script
`scripts/qualtrics_fill.py <tid-file|url>` — opens/finds the tab, dumps the
question map (`--map`), or fills from a JSON answer file (`--answers a.json`
where keys are QIDs). Then `--submit` clicks Next. CDP via `$CDP_URL`
(default http://localhost:9229 — 9222 is Vantage WebView2 on this host).

## Answer conventions used for Cloudless
Legal name "Cloudless"; alt=No; website https://cloudless.gr; addr
Koropi/Attiki/19400/Greece; Platform use case; new functionality; Bridge
service; not-only-own-marketing; customers Europe/1-50/75%+ B2B/Small-Medium;
privacy-policy link https://cloudless.gr/privacy; compliance owner Yes;
partners Facebook/Instagram/Tiktok.

## Verified 2026-11-05 (8 LinkedIn product forms filled end-to-end)

- **Use `li_batch_fill.py`** for access forms — multi-round fill→Next→rescan
  until "Thank you". Single-pass always leaves required fields: conditional
  questions only flag errors AFTER a failed submit, so budget ≥2 rounds.
- **Error detection**: `.ValidationError` alone matches NOTHING on this theme —
  query `.ValidationError,[class*=error-message],.QuestionError`.
- **Radio semantics**: Yes=[0], No=[1]; "%B2B 75%+" is the LAST option [3].
- **Q_DL binds the app**: open the form via the app's OWN products-page link
  (`&Q_DL=EMD_<app>_<sv>_<tok>`). A bare `SV_*?Q_CHL=gl` submits an orphan.
- **display-wrapper-/display- prefixed question nodes are responsive
  duplicates** — filter them or every field gets double-processed.
- Fills that always worked: text/textarea via native value setter + input/
  change/blur; radios via `closest('label').click()`; Country via the custom
  dropdown then option click (Greece).
- Survey-specific asks: Community-Mgmt wants client-id verbatim, screen
  recording link, test-login details — the script answers with the documented
  social.cloudless.gr demo values (never invent data).

## JFE API submission (verified 2026-11-05 — THE unlock)

- DOM writes + real Input.dispatchMouseEvent both fail to commit on
  linkedincustomerops JFE forms. The working path is the Question API:
    `const q = Qualtrics.SurveyEngine.QuestionData.getInstance('QID25')`
    `q.getQuestionInfo()` → real Choices map {id:{Text,RecodeValue}}
    `q.setChoiceValue(choiceId, true)`  // radios + checkboxes, use STRING id
    `q.setTextValue(text)`              // text entries (fallback: container
                                       // input + native setter + input/blur)
    `q.getChoiceValue(id)` → verify the model actually holds it (stale
    "Response required" banners persist visually even after the model is set)
- **Submit = `q.clickNextButton()`** — NOT the "Go to next issue" banner
  button, and `#NextButton` may not exist in the DOM on this theme.
- Reopen each form via the app's own products-page Q_DL link.
