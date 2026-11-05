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
