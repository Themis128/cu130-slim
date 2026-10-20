# LinkedIn Ad Funnel Ops — outcome-chain discipline for cloudless.gr ads

Adopted from the LinkedIn Marketing Solutions case-study brief
`Kakkava Sofia/Linkedin/1792140163392.pdf` — two B2B SMBs whose measurable
outcomes came from a **funnel discipline, not ad spend alone**.

Use when planning/reviewing LinkedIn ad activity, checking whether paid
spend produces CRM leads, deciding which format to sponsor next, or
asking "are the ads working toward money".

## The paper's playbook

| Case study | Result | What produced it |
|---|---|---|
| Dark Horse (agency, 11-50) | −70% CPL, 23% CVR, 97 leads/4mo | Document Ads (expertise upfront) → Lead Gen Forms + Message Ads (capture) → A/B copy tests → leads flow into marketing-automation nurture |
| Connectd (SaaS) | 2x revenue, −10% cost/conv, 200x ARR | Conversions API + CRM sync (measurement BEFORE scaling) → format diversification (Document/Thought Leader/boosted) → LinkedIn Insights targeting |

The shared chain the paper teaches: **target ICP → diverse formats →
capture surface → CRM nurture → measurement that proves CPL/CVR**. Every
link is checkable — and `scripts/linkedin_ads_funnel_audit.py` checks all
of them live.

## Audit — `scripts/linkedin_ads_funnel_audit.py`

Runs inside `social-api` (`/app/scripts/`, mounted read-only):

```bash
docker compose exec -T social-api python /app/scripts/linkedin_ads_funnel_audit.py          # human report
docker compose exec -T social-api python /app/scripts/linkedin_ads_funnel_audit.py --json   # machine output
```

Exit 1 when real gaps exist. Checks (all live surfaces, no heuristics):

| Paper element | Surface | Gap condition |
|---|---|---|
| Campaign inventory | `rest/adAccounts/512642510/adCampaigns` via `collect_metrics()` | API failure → sidecar fallback flagged |
| Credit safety | `account_spend + resumable caps ≤ credit` | worst-case > credit → **CARD AT RISK** |
| Format diversification | `ad_campaign_snapshots` names/history | single format on active/paused campaigns |
| A/B rotation (Dark Horse) | creatives inventory | **unmeasurable** — `/rest/creatives` unreachable with `r_ads` (probe 2026-10-20: 404/400); verify ≥2 creatives/campaign in CM |
| Lead Gen Forms (Dark Horse) | `rest/leadForms` probe | unreachable via `r_ads` → needs `r_marketing_leadgen_automation` scope or CM check |
| Conversions (Connectd) | `rest/conversions` probe | same scope limitation — honestly reported |
| Leads → CRM nurture | EspoCRM `utmMedium=paid` lead count | 0 = loop never produced a record |
| Paid attribution | `web_analytics_events` `utm_medium=paid` | 0 = paid→site never landed |
| Measurement foundation | Insight Tag (`snap.licdn.com`) on cloudless.gr | absent = conversions invisible to LinkedIn |
| Document-ads stock | `media_assets` PDF count | the sponsor-ready carousel inventory |

## First run (2026-10-20) — real readings

- **CARD AT RISK** — account spend €143.03 > €136.75 credit; worst-case
  exposure €199.38 because two PAUSED campaigns (`857622786` €80 cap,
  `907100946` €100 cap) remain resumable. Paused ≠ safe: any resume drains
  the Visa. Reduce lifetime caps or end-date them before re-activating.
- Campaigns: `907100946` carousel boost paused €90.40/€100 spent,
  `857622786` Shop Online paused, `904916326` completed.
- **0 paid leads** in EspoCRM ever; **0 `utm_medium=paid`** site events —
  the paid→site→CRM loop has never carried traffic.
- **No LinkedIn Insight Tag** on cloudless.gr — ad conversions are
  invisible to LinkedIn optimization (Connectd's foundation step).
- **58 carousel PDFs** in the media library — the Document Ads inventory
  is stocked and ready to sponsor.
- Formats: only `carousel` + one unspecified — Connectd's single-format
  failure mode; Document + Thought Leader + boosted diversity is the fix.
- 168 `utmSource=linkedin` EspoCRM leads exist — all organic (newsletter),
  proving the CRM write path works once traffic arrives tagged.

## Adoption map — what we do with each paper element

| Paper element | SocialAuto adoption | Owner skill |
|---|---|---|
| Document Ads | brand-carousel pipeline makes the PDFs; sponsor the best organic carousel via CM as a Document Ad | `linkedin-ads-ops`, `cloudless-carousel-pipeline` |
| Lead Gen Forms | needs `r_marketing_leadgen_automation` scope (app review) OR manual CM form creation; leads sync to EspoCRM via Lead Sync | `linkedin-api-upgrade`, `developer-apps-ops` |
| A/B testing | 2-5 ad rotation (LinkedIn docs: ~20% CTR lift); two creatives per campaign min | `linkedin-ads-ops` CM flow |
| CRM nurture | EspoCRM TargetLists + email drips — the pipeline exists, zero paid records so far | `espocrm-ops` |
| Conversions/measurement | Insight Tag on cloudless.gr + Conversions API; blocked on scope and site-deploy decision | `developer-apps-ops`, `cloudless.gr` site |
| Format diversity | Document Ad + Thought Leader Ad + boosted organic (real @-mention path already built) | `linkedin-ads-ops` |
| LinkedIn Insights targeting | CM audience insights — manual surface; titles/geo already tuned 2026-09 | `linkedin-ads-ops` |

## Non-negotiables (standing rules, unchanged)

- **Never spend money** — promo credit only; the CARD AT RISK gap is the
  audit's top finding class. Cap = `credit ÷ 1.2`, hard end date, all
  account campaigns counted (credit is account-wide).
- Sponsored organic posts preserve real @-mentions; direct Sponsored
  Content does not (linkedin-ads-ops).
- Every ad CTA carries `utm_medium=paid` + the shared `utm_campaign` slug —
  the cross-channel-ops integration rule applies to ads too.

## Related

- `linkedin-ads-ops` — CM automation, sidecar endpoints, creative pipeline
- `cross-channel-ops` — shared `utm_campaign` discipline across channels
- `funnel-ops` — stage audit where paid leads land
- `espocrm-ops` — the nurture side of the loop
