# The Loop Contract — index

Every department owns closed loops, not features. A loop is live only when **input → action → output → health signal** is verifiable in production data. This page is the contract of record: what each loop must guarantee, where it runs, how we know it's healthy, and its current verified state.

Status verified 2026-10-18: 29/29 containers Up, 43 beat entries scheduled, every row below checked against live state (not prose).

## Contract format

Each loop must answer: **Trigger** (when it fires) · **Code** (where it lives) · **Signal** (the artifact that proves it ran — Slack msg, DB row, NC record, email) · **Escalation** (where a human sees failure).

---

## 1. Executive Office

| Loop | Trigger | Code | Health signal | State |
|---|---|---|---|---|
| Daily strategy brief → playbook JSON | `0 9 * * *` | `worker/tasks/notebook_reports.py` (`daily-strategy-report`) | Slack digest + report row | ✅ live |
| Company brain journal | `45 23 * * *` | `worker/tasks/company_brain.py` | Chroma journal entry | ✅ live |
| n8n digest → Slack + IntraVox news | n8n schedule | n8n `cloudless-daily-digest` workflows | Slack msg + IntraVox feed | ✅ live |
| Weekly CRM-hygiene report | `30 7 * * mon` | `services/crm_hygiene.py` | Slack report w/ quarantine/stale counts | ✅ live (verified today: +1 quarantine, +3 assigned, 0 err) |
| Weekly Slack rollup | `0 9 * * 1` | `worker/tasks/digest.py` | Slack rollup | ✅ live |
| Monthly Slack rollup | `0 9 1 * *` | `worker/tasks/digest.py` | Slack rollup | ✅ live |

## 2. Marketing & Content

| Loop | Trigger | Code | Health signal | State |
|---|---|---|---|---|
| NC News trend ingest | n8n fetch | NC News feeds → n8n | feed rows | ✅ live |
| 13 n8n post workflows | n8n schedules/webhooks | n8n `cloudless-*` | post rows created | ✅ live |
| NLP ≥80 gate (sofia_nlp_score) | pre-publish | `ai/*` quality pipeline | score field on drafts | ✅ enforced |
| Media pipeline (FLUX→brand compose→R2) | per-post | media workers + ComfyUI | `media_assets` rows, R2 objects | ✅ live |
| SKIP-LOCKED publish queue | `30s` + `60s` scans | `worker/tasks/publishing.py` | posts status transitions | ✅ live |
| Drop-folder → draft | file watch | drop-folder ingest | draft rows | ✅ live |
| Carousel pipeline (CF-only) | webhook `cloudless-carousel` | carousel pipeline + n8n | PDF in NC Files + post | ✅ live (asset visible 19:37 today) |
| NC Deck pipeline board sync | `25 */3 * * *` | `services/nextcloud_sync.py` | cards match post states (verified 0/111 drift) | ✅ live |

## 3. Sales & CRM

| Loop | Trigger | Code | Health signal | State |
|---|---|---|---|---|
| Capture (contact/playbook/Messenger) | ingress webhooks | API routes + EspoCRM client | Lead rows | ✅ live |
| Score | contact-form path | `lead-scoring` (cloudless.gr) | score on lead | ⚠️ **partial — non-contact ingress unscored** |
| Hygiene: dedupe/quarantine/route | `30 7 * * mon` | `services/crm_hygiene.py` | Dead+note, assignedUserId set | ✅ live (auto-assign + self-domain quarantine added) |
| Lead research sweep | `40 8 * * *` | `services/lead_research.py` | dedup'd research notes | ✅ live |
| Daily email drip (first-approach + renewal) | `20 8 * * *` | `worker/tasks/email_drip.py` | sent notes on leads | ✅ live |
| AC (ActiveCampaign) enroll | drip send | enroll client | contact in list | ✅ live |
| Lifecycle convert → Contact/Account/Opp | — | — | — | ❌ **missing — no convert step exists** |

## 4. Revenue & Monetization

| Loop | Trigger | Code | Health signal | State |
|---|---|---|---|---|
| Polar checkout → webhook | purchase | `api/v1/billing` + `polar_api.py` | order/subscription rows | ✅ live |
| Creator-program threshold tracker | `30 8 * * *` | `services/monetization.py` | daily monetization report | ✅ live |
| LinkedIn credit-guarded ads (÷1.2) | campaign mgmt | `worker/tasks/linkedin_ads_report.py` `0 10` | daily ads report, spend ≤ credit/1.2 | ✅ live |
| UTM funnel loop | post CTA | platform_specific UTM + web_analytics | attributed sessions | ✅ live |
| Promo scan | `0 8 * * *` | `worker/tasks/promo_scan.py` | new-credit alerts | ✅ live |

## 5. Operations & SRE

| Loop | Trigger | Code | Health signal | State |
|---|---|---|---|---|
| Session healer ladder | `20 * * * *` | `worker/tasks/session_healer.py` | platform session states | ✅ live |
| IG session check | `30 */6 * * *` | `tasks/instagram_session_check.py` | session alerts only when needed | ✅ live |
| LinkedIn session check/refresh | `45 */12` + weekly | `tasks/linkedin_session_*.py` | session state | ✅ live |
| Wedge/DMR watchdog | `90s`+`300s`+`30m` | `tasks/dmr_health.py`, `dodo_live_check.py` | DMR real inference verified | ✅ live |
| Container health (29/29 Up) | Docker | compose stack | `docker ps` | ✅ 29/29 today |
| Token refresh | `15,45 * * * *` | `tasks/token_refresh.py` | token expiry > horizon | ✅ live |
| TikTok inbox reconcile | `20 */6 * * *` | `tasks/tiktok_inbox_reconcile.py` | stuck inbox drained | ✅ live |
| Tailnet policy-as-code | GH workflow | `.github/workflows/tailscale-ops.yml` | audit reports | ✅ live |

## 6. Data & Analytics

| Loop | Trigger | Code | Health signal | State |
|---|---|---|---|---|
| Analytics sync → snapshots | `1800s` | `tasks/analytics.py` | `follower_snapshots` rows | ✅ live |
| Plausibility guard | in-sync | `analytics_sync._plausible_*` | no absurd deltas in digests | ✅ live |
| Best-window computation | in strategy | `strategy_report` | playbook JSON window | ✅ live |
| ETL → R2 datalake | `10 */6 * * *` | `tasks/datalake_export.py` | R2 objects | ✅ live |
| NC Tables follower metrics | `25 */3` | `nextcloud_sync` Tables | table rows | ✅ live |
| Metabase | on demand | metabase-mcp | dashboards | ✅ live |

## 7. Customer Communications

| Loop | Trigger | Code | Health signal | State |
|---|---|---|---|---|
| Messenger polls (personal/IG/Threads/TikTok/X/LinkedIn) | `120s–21600s` | `tasks/*_messenger.py` | polled conversations | ✅ live |
| Telegram channel agent + group digests | `0 11` + `5 * * * *` | `tasks/telegram_*` | channel posts, digests | ✅ live |
| WhatsApp verify + sends | `*/30` verify | `tasks/whatsapp_verify.py` | verified number | ✅ live |
| omv mail stack | SMTP | postfix/dovecot | transactional mail | ✅ live |
| Slack escalation backbone | every loop | slack notify | alert msgs | ✅ live (webhook→bot-token fallback) |
| Viber bot | webhook | `viber` client | replies | ✅ live |

## 8. Platform & Security

| Loop | Trigger | Code | Health signal | State |
|---|---|---|---|---|
| CI gauntlet | per PR | GH workflows | checks green | ✅ live |
| Trivy/Dependabot | scheduled/PR | GH security | no crit CVEs on images | ✅ live (post-slim rebuild) |
| Secrets-sync workflows | GH workflow | `sync-*-secrets.yml` | secrets in sync | ✅ live |
| Tailscale ACLs | tailnet policy repo | tailscale-ops workflow | policy applied | ✅ live |
| CF Access / Zero Trust | always-on | CF config | protected paths gated | ✅ live |
| Media dedup (phash) | pre-publish | `media_dedup` | no duplicate media ships | ✅ live |
| Publish guard: media-required | pre-publish | publishing pre-flight | no media-less posts | ⚠️ media gate live; **empty-`content_text` publish slipped through** (`f6255dd9`) |

---

## Open gaps (priority order)

1. **Lead→Contact/Account/Opportunity conversion missing** — nothing ever converts a hot lead; renewal loop starved. Proposed: score-gated auto-convert, skipping synthetic/test records.
2. **Lead scoring coverage** — only contact-form path scores; playbook/Messenger/manual enter unscored.
3. **Empty-text publish guard** — post `f6255dd9` published with `content_text` = 0 (media-only). Publish gate needs a non-empty-caption rule or explicit media-only flag.
4. **EspoCRM ACL gaps** — `socialauto` API user can't read `/User` or `/TargetList` (segmentation + human-assignee options blocked).
5. **Engagement write-back** — opens/replies don't return to the CRM record.
6. **NC Collectives not wired** — this index lives in-repo; Collectives page sync would need a new client path in `nextcloud_sync`.
