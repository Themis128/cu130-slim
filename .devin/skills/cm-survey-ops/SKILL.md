---
name: cm-survey-ops
description: >-
  LinkedIn Community Management Standard-tier access survey — fully
  automated end to end (verified submitted 2026-11-12). The one human step
  (the ~2 min screen recording) already exists as li-integration.mp4;
  cm_survey_drive fills the single-page Qualtrics form via the JFE engine
  API, links the recording hosted on the SocialAuto media library (public
  R2 URL), clicks the real #next-button, and confirms Thank-you.
  cm_survey_watch is the cron drop-consumer for future recordings.
  Tools: scripts/cm_survey_drive.py, scripts/cm_survey_watch.py.
---

# CM survey ops

The Qualtrics form `SV_3Q1SbLSZoImyw6O` ("Community Management API -
Standard Tier Access Form") is **single-page**: 16 MC questions + 12 text
fields, one `#next-button` submits to a Thank-you page. No LinkedIn login
needed; no file upload — QID43 wants a **link** to the recording.

**Status 2026-11-12: SUBMITTED** with li-integration.mp4 (consumed →
`li-integration.mp4.consumed` in Downloads) and recording URL
`https://pub-9dff040689a94928938b78da104c1ec4.r2.dev/2026/11/12/7b85c1f50cb648d3.mp4`.
LinkedIn review takes days–weeks; watch the portal for the tier flip.

## Verified mechanics (this JFE theme — differs from qualtrics-ops notes)

- **MC questions commit ONLY via the engine API** — DOM clicks don't
  register: `Qualtrics.SurveyEngine.QuestionData.getInstance('QID9')`
  → `setChoiceValue('<1-based-choice-id>', true)`; verify with
  `getChoiceValue`. Country = QID9 choice `67` (Greece, 193 options).
- **TE questions have NO `setTextValue`** on this version — write via
  native value setter + input/change/blur inside `#question-<qid>`;
  responses are harvested at nav time.
- **Navigation = `#next-button` / `.navigation-button`** — NEVER the
  "Go to next issue" banner (chat widget; caused the old page-11 loop).
- **The relay kills websockets** whose buffers fill — `CDP` class uses a
  background reader + transparent re-attach to the same tab id.
- Open via `/json/new` (HTTP), never `Page.navigate` on an existing tab.
- Q_DL binding could NOT be scraped — the :9229 browser is NOT logged
  into LinkedIn (`/developers/login` redirect). Bare `SV_` URL used;
  if LinkedIn reports an orphaned response, re-login on :9229 and set
  `CM_FORM_URL` to the app's products-page link.

## Answer map (verified live)

TE: QID1 legal name `Themis Baltzakis`, QID45 website, QID5/6/8/46
address (Thermopilon 15 / Koropi / Attica / 19441), QID26 about-business,
QID15 use-case detail, QID33 industries, QID35 privacy URL, QID43
recording link, QID44 test login (`CM_TEST_LOGIN` env override).
MC: see `CHOICE` dict in the script — alt-name No, another-app No,
why=3, use-case=3 (Direct Advertiser), Europe, 1-50 clients, 75%+ B2B,
Small-Medium, Brand-managing-execs, privacy Yes/Yes, ads No, sharing No,
partners FB+IG+X+TikTok. Conditional questions (QID3/22/39/40/48) skipped.

## Recording URL chain (`recording_url()`)

1. `CM_RECORDING_URL` env wins.
2. **SocialAuto media library** — uploads via `/api/v1/media/upload`
   (SOCIAL_ADMIN_* login) and returns the public R2 `public_url`
   (`pub-*.r2.dev` — GET works, HEAD 403 is normal).
3. Nextcloud public share fallback — currently BROKEN: cloud.cloudless.gr
   times out / 502 (omv tunnel down; see omv-nextcloud-ops).

## Watcher

`cm_survey_watch.py` (cron */15) consumes the newest unconsumed mp4 in
`~/Downloads` or `/tmp`, runs the drive, renames to `.consumed`, TG #ops
on submit/fail. State: `~/.local/state/cm-survey-watch/`.

## After submit

LinkedIn review takes days–weeks. `member_dp_probe.py`
(linkedin-portal-gates-ops) shows product state on the portal — needs a
LinkedIn login on the :9229 browser first (currently logged out).
