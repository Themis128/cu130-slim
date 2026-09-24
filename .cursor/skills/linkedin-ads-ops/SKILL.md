---
name: linkedin-ads-ops
description: >-
  Run LinkedIn ads for cloudless.gr through SocialAuto: Campaign Manager
  browser automation via the LinkedIn sidecar (port 9225), promotional-credit
  spend safety, sponsored organic posts (real @-mentions) vs direct Sponsored
  Content, audience targeting, UTM conventions, and the branded creative
  pipeline (FLUX → brand-compose → upscale → media library). Use for LinkedIn
  ad campaigns, coupon/promo credit redemption, boosting posts, ad creatives,
  targeting changes, or any Campaign Manager work.
allowed-tools:
  - read
  - exec
  - grep
  - web_search
  - webfetch
triggers:
  - user
  - model
---

# LinkedIn Ads Operations

Drive LinkedIn Campaign Manager through the LinkedIn browser sidecar and
SocialAuto. **Always consult LinkedIn's official docs before changing ad
decisions** (standing user rule) and **never spend money** — promotional
credit only, no payment details.

## When to use

- Create/edit/launch LinkedIn ad campaigns or ad sets
- Redeem ad-coupon / promotional-credit offers
- Sponsor (boost) an existing organic post — incl. real @-mentions
- Change targeting (job titles, functions, geo, expansion, LAN)
- Upload ad creative (branded image, carousel PDF)
- Check ad spend / budget / billing state
- Verify a promo campaign cannot bill a card

## Known IDs (cloudless.gr)

| Thing | Value |
|---|---|
| Ad account | `512642510` ("Baltzakis Ad Account") |
| Coupon campaign/ad set | `907024926` — "Cloudless boost - Sep 2026 - coupon" |
| Creative (direct ad) | `1573649544` — "Ad_1_24Sep2026" |
| Company page (numeric) | `108614163` |
| Company page SocialAuto account | `9c4451bb-e820-489f-8676-76ddbc788ffe` |
| Sofia Kakkava (coach) | `/in/sofia-kakkava/` → person URN id `ACoAABDTOOoBDAatzpCy4xvznaTHILTrteq8JCg` |

Campaign Manager URL pattern:

```
https://www.linkedin.com/campaignmanager/accounts/{acct}/campaigns/{adset}/creatives?businessId=personal
```

## Sidecar API (port 9225)

Only these endpoints exist — no `/session/upload`, no tab enumeration:

| Endpoint | Use |
|---|---|
| `POST /debug/navigate {url}` | Go to URL (SPA; wait ~8-10s after) |
| `POST /debug/eval {script}` | Page JS — clicks, fills, DOM reads |
| `GET  /debug/page-text` | Full rendered text (best for state) |
| `GET  /debug/form-html` | Form markup for field discovery |
| `GET  /debug/screenshot` | PNG screenshot |
| `POST /session {storage_state}` | Inject cookies (see linkedin-sidecar-ops) |
| `POST /login {username,password}` | Fresh credential login (2FA is manual) |
| `POST /session/clear-rate-limit` | Clear in-memory 429 circuit |

Helper script wraps all of these:

```bash
scripts/linkedin_cm.py nav '<url>'
scripts/linkedin_cm.py text                 # page text
scripts/linkedin_cm.py eval 'js...'
scripts/linkedin_cm.py click 'Visible text' # click button/link by text
scripts/linkedin_cm.py fill 'label' 'value' # React-safe input fill
scripts/linkedin_cm.py upload /path/img.jpg # DataTransfer file injection
scripts/linkedin_cm.py shot /tmp/cm.png
```

### Gotchas (learned Sep 2026)

- **SPA bounces**: CM pages re-navigate during hydration; evals throw
  "Execution context destroyed" — retry 3-4× with 2.5s waits. If a nav lands
  on the personal profile, just re-navigate to the CM URL.
- **Row menus**: use the `⋯` on the ad row, not the global menu (which
  navigates to the profile).
- **"Show in feed" opens a new tab** — the sidecar tracks ONE page and it
  silently no-ops. Use `⋯ → View` for the in-page preview instead.
- **Filling inputs**: React needs the native setter + `input` event (see
  `linkedin_cm.py fill`).
- **File upload**: `/debug/eval` is page-JS only (no `setInputFiles`).
  Inject via base64 → `File` → `DataTransfer` → `input[type=file]`.
  Payloads >~100KB blow the shell arg limit — `linkedin_cm.py upload`
  sends the JSON body via a temp file.

## Promotional-credit safety (from LinkedIn docs)

Standing rule: **never spend money, never enter payment details.**

1. Credit is **account-wide** and deducts before the payment method — but
   every ACTIVE campaign draws from it. Check all campaigns, not just yours.
2. Campaigns **do not auto-stop** when credit exhausts → the card is billed.
   Controls = **lifetime budget** + **hard end date** (not daily budgets).
3. LinkedIn may overshoot a configured budget **up to ~20%** briefly →
   cap the lifetime budget at `credit_balance / 1.2`, not the full credit.
4. Before launch verify: current credit balance, currency, expiry, other
   active campaigns' caps, combined exposure.
5. Audience Expansion + LinkedIn Audience Network increase reach but
   accelerate spend — same cap applies.

## Direct ad vs sponsored organic post (mentions)

| | Direct Sponsored Content | Sponsored organic post |
|---|---|---|
| Clickable @-mention | **Not supported** (text-only) | ✅ preserved when sponsored |
| Hashtags | clickable | clickable |
| Create via | CM → Create ad | SocialAuto post → CM "Browse existing content" |

For a real mention: publish an organic **company-page** post via SocialAuto
(`/api/v1/content/*`, org account `9c4451bb-…`) with the mention markup
`@[Name](urn:li:person:{fsd_profile_id})`, then attach it in CM via
"Browse existing content → Company page". This also gives a 2-ad A/B
rotation (LinkedIn docs: 2-5 ads lift CTR ~20%).

### Getting a person's mention URN

Navigate to their profile → the Message link contains
`profileUrn=urn%3Ali%3Afsd_profile%3A{ID}` — that `{ID}` is the
`urn:li:person:` value for API mentions.

## Creative pipeline (all via SocialAuto)

1. Generate: `POST /api/v1/ai/...` FLUX path (Cloudflare fallback — DMR
   diffusers is unavailable on this WSL2 host).
2. Brand: `compose_branded_slide()` in
   `backend/app/services/carousel_pipeline.py` — logo header, tagline,
   comparison infographic, brand palette (`#0f0f17` bg, `#00fff5` cyan).
   Run inside `social-api` container.
3. Enhance: `POST /api/v1/media/enhance/assets/{id}/upscale` returns image
   bytes directly (not JSON).
4. Store: `POST /api/v1/media/upload` — **downscales to 768px max**; check
   stored dimensions before using as ad creative.

## Targeting notes

- Attribute search returns `facet-browser__suggestion-button` chips —
  click them to add titles (Founder, Owner, CEO, coaches…).
- Chips remove via their `×`; verify the final list with page-text.
- Job Titles (current) work better than Job Functions for SMB/founder
  audiences. 100-300k audience ≈ healthy for €90 lifetime.
- UTMs (ad-set level, URL tracking params field — it's a placeholder, not
  a saved value): `account_id={{ACCOUNT_ID}}&utm_source=linkedin&
  utm_medium=paid&utm_campaign=<slug>&utm_content={{CAMPAIGN_ID}}`

## Related skills

- `linkedin-sidecar-ops` — session injection, login/2FA, profile ops
- `socialauto-publish` — create the organic post to sponsor
- `socialauto-media` — media library upload/view/enhance
- `cloudless-carousel-pipeline` — branded carousel generation
