# LinkedIn Access-Request Forms

Drives the LinkedIn developer-portal product access surveys — the Qualtrics
"Access request form" and the in-portal "Request access" buttons — for both
Cloudless apps (264925843 org/CM/ads; 265077368 `cloudless-gr-member` DMA).

## The form inventory (as of 2026-11-05)

| Form | Product | Type | Gate |
|------|---------|------|------|
| Access request form (Qualtrics `SV_3Q1SbLSZoImyw6O`) | Community Management — Standard Tier | multi-page survey | screen recording + registered address |
| Request access (in-portal) | Member DP (Member) | self-serve modal | business-email verify |
| Request access (in-portal) | Member DP (3rd Party) | self-serve modal | business-email verify |
| Request access (in-portal) | Pages DP | self-serve modal | business-email verify |
| Access request form | Advertising API | Qualtrics | use case |
| Request access | Lead Sync / Matched Audiences / Events | self-serve modal | none |

## Driving the Qualtrics survey (playwright MCP)

The forms are third-party `linkedincustomerops.qualtrics.com` pages — no
LinkedIn login required, so the **playwright MCP** drives them directly (the
dev-portal Cloudflare block doesn't apply).

```text
browser_navigate -> <form url>
browser_evaluate -> survey_fill.js FILLER + CM_FORM (fills text/radio/check/select)
browser_evaluate -> Next-button click -> repeat for each page
browser_file_upload -> the recorded integration mp4 (the recording gate)
```

- `scripts/survey_fill.js` — generic field-driver (ordinal text fields + named
  radio/checkbox groups + select text-match). The `CM_FORM` map carries the
  Cloudless answers; `<REGISTERED_STREET>` / `<ZIP>` need the real registered
  address.
- Multi-page forms: re-run `FILLER` after each Next click — the new page's
  QIDs differ.
- `browser_file_upload` accepts the mp4 on the upload question — the one hard
  human-substitute step is now a real recording, not fabricated.

## The screen recording (`record_integration.sh`)

Captures the user's REAL Chrome (win_agent `:8765` /screenshot) sitting on the
live SocialAuto LinkedIn surfaces, stitches `social-api`'s ffmpeg into an mp4:

```bash
# open social.cloudless.gr in the user's Chrome first (or let the script
# capture whatever tab is focused — the LinkedIn integration view)
./record_integration.sh --frames 30 --fps 1 --out /tmp/li-integration.mp4
```

Then `browser_file_upload` the mp4 on the survey's recording question.

## The in-portal "Request access" buttons

Self-serve modals on the Products page — `cdp_eval.py` clicks them the same
way as the earlier product adds (see `win-bridge-ops`). They stay `disabled`
until business-email verification completes — now possible since the
mail-ingest route fix delivers the verify link.

## Order of operations

1. Email verify (`tbaltzakis@cloudless.gr` link — now landing via the fixed
   mail-ingest route) → Member DP buttons unlock.
2. Drive the Member DP modals via `cdp_eval.py` (no survey needed).
3. Run the CM Qualtrics survey via playwright + `survey_fill.js` (the
   recording + registered address are the remaining inputs).
4. Same for the Advertising request form (its own survey).
5. `linkedin_scope_audit.py` verifies the resulting grants.
