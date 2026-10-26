# Human-Gate Ops — work the 5 owner-blocked items as far as tools allow

Standing leftovers that need a human action. Each has a script or playbook
below so the blocker is surfaced, nudged, and consumed the moment the human
acts — instead of waiting for the owner to remember.

## 1. LinkedIn 2FA push (#794 gate)

The `pending_2fa` checkpoint lives in the linkedin-sidecar session; the push
is already on the owner's phone.

- `scripts/linkedin-2fa-watch.sh` — polls the sidecar session state; posts
  to the Slack ops channel when the checkpoint clears (or re-alerts after
  6h still pending). Run in a tmux/background session:
  `nohup .devin/skills/human-gate-ops/scripts/linkedin-2fa-watch.sh &`
- If the push expired (LinkedIn prompts time out), trigger a re-heal:
  `POST /api/v1/ops/session-heal` (admin API) — the sidecar re-sends it.

## 2. WhatsApp SMS code (#786)

Beat auto-retries `request_code` inside the ~1h cooldown window — nothing
to re-trigger manually. The owner only supplies the code.

- `scripts/whatsapp-verify.sh CODE [PIN]` — one-shot finish:
  `POST /register/verify-code` with the SMS code, then `POST /register/register`
  with the 6-digit PIN (defaults to `WHATSAPP_PIN` env or prompts-free usage).
  Uses the running social-api admin session — no container secrets printed.

## 3. Meta in-app confirmation + restriction (#785)

No API writes account-status — must be done in Accounts Center on the
phone/desktop session. The agent part is *watching*, not clicking:

- `scripts/meta-account-status.sh` — drives the FB sidecar session (via
  `browser_profile`/Playwright MCP `browser_navigate` on
  `accountscenter.facebook.com/personal_information` +
  `.../account_status`) to read whether the restriction lifted; posts the
  result to Slack ops. Once clear → connect `cloudless.gr` portfolio →
  `leads_retrieval` App Review (issue #785 has the checklist).
- Run on a headed profile (the persistent `browser-novnc` session), never
  headless-parallel — Meta flags the second session.

## 4. LinkedIn page specialties (manual UI edit)

16 headless paths exhausted (ArtDeco pill input stays `display:none`).
Last remaining automated try: **Playwright MCP `browser_navigate` against
the persistent headed session** (`browser-novnc`, the same profile the
sidecar keeps warm) — a real pointer-click path, not DOM injection.

- `scripts/linkedin-specialties.sh` — opens
  `linkedin.com/company/…/edit/` via the headed profile, runs keyboard-first
  entry (Tab→type→Enter on the pill field), screenshots before+after to
  Slack. If it fails again, the human does the 60-second edit; the script
  exists so re-trying after LinkedIn ships a UI change is one command.

## 5. #793 partner approvals + #795 pricing decision

- `scripts/partner-digest.sh` — pulls the `Partners & influencers`
  TargetList from EspoCRM (`ESPOCRM_BASE_URL`/`ESPOCRM_API_KEY` env) and
  posts a Slack digest: name · platform · proposed touch · link to the
  EspoCRM record for one-click approve/drop. Owner replies on the list or
  in-thread — no new approval plumbing needed.
- #795: draft positioning already posted as issue comment (€1,600 +
  €400/mo retainer). Owner replies "approve" → build service page +
  catalog entry via the agency pipeline.

## Cross-cutting

- Every script posts to the ops channel via `_post_slack_text`
  (`SLACK_CHANNEL_ID` + bot token) — `SLACK_ALERTS_CHANNEL_ID` is unset.
- Never print secrets; read them from `.env` or env vars only.
- Anything that mutates an external platform stays `review` tier per
  ABPM — these scripts read state or send the owner's own confirmation,
  they don't impersonate the owner.

## Scripts — one per gate

- `x-reconnect.sh` — mints the X OAuth2 authorize URL (login → team → /connect/twitter). Click output to reconnect.
- `x-status.sh` — counts refresh-400s in worker logs; 0 = reconnected.
- `cmapi-check.sh` — probes `organizationAcls`; 200 = Community Management approved.
- `check-inbox.sh` — mailbox triage (MAILBOX_PASSWORD env); filters system senders, shows real unread.
