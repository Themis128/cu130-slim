# SocialAuto Application Architecture (Complete)

## Overview

SocialAuto is a full-stack social media automation platform built on a
free-first, open-source-first, self-hosted philosophy. It manages six social
platforms (Facebook, Instagram, LinkedIn, Twitter/X, TikTok, Threads) with
AI content generation, scheduling, analytics, brand management, and
Messenger integration.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SocialAuto Platform                              │
│                                                                         │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│   │  Frontend    │  │  Backend API │  │  MCP Server  │  │  n8n       │ │
│   │  (Next.js)   │  │  (FastAPI)   │  │  (35 tools)  │  │  Workflows │ │
│   │  64 pages    │  │  560 routes  │  │              │  │            │ │
│   │  78 comp.    │  │  43 modules  │  │  AI agents   │  │  No-code   │ │
│   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘ │
│          │                 │                  │                │       │
│          └────────┬────────┴──────────────────┴────────────────┘       │
│                   │                                                     │
│          ┌────────┴──────────────────────────────────────────┐        │
│          │              Infrastructure                        │        │
│          │  Redis · PostgreSQL · ChromaDB · MinIO · WARP       │        │
│          │  ComfyUI · DMR · LanguageTool · noVNC · Sidecars     │        │
│          └────────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Frontend | Next.js (App Router) | 16.3 |
| Frontend | React + TypeScript | React 18, TS 5.3 |
| Frontend | TanStack Query | (data fetching) |
| Frontend | Tailwind CSS + shadcn/ui | (styling) |
| Frontend | Recharts | (charts) |
| Backend | FastAPI (Python) | 0.115+ |
| Backend | SQLAlchemy 2.0 (async) | (ORM) |
| Backend | Pydantic v2 | (validation) |
| Backend | Celery 5 | (task queue) |
| Backend | httpx | (async HTTP) |
| Database | PostgreSQL 16 | (primary) |
| Cache | Redis 7 | (broker + cache) |
| Vector | ChromaDB 1.5 | (local embeddings) |
| Storage | MinIO | (local S3) |
| AI | Cloudflare Workers AI | (free primary) |
| AI | Docker Model Runner | (local fallback) |
| Proxy | Cloudflare WARP | (free SOCKS5) |
| Tunnel | Cloudflare Tunnel | (cloudless.gr) |

## Frontend Architecture

### Route Groups (64 pages — 62 in groups + root landing + public brand share)

```
app/
├── page.tsx                          # Marketing landing (redirect)
├── layout.tsx                         # Root layout
│
├── (auth)/                            # Authentication (5 pages)
│   ├── login/                         # Sign in (+ TOTP step when 2FA enabled)
│   ├── register/                      # Sign up (+ optional discount code)
│   ├── forgot-password/               # Request reset
│   ├── reset-password/                # Set new password
│   └── accept-invite/                 # Team invite acceptance
│
├── (public)/                          # Public marketing & legal (11 pages)
│   ├── about/                         # About page
│   ├── features/                      # Feature highlights
│   ├── pricing/                       # Pricing tiers
│   ├── api-docs/                      # Swagger UI
│   ├── contact/                       # Contact / support channel
│   ├── terms/                         # Terms of service
│   ├── privacy/                       # Privacy policy
│   ├── refund/                        # Refund policy (MoR requirement)
│   ├── acceptable-use/                # Acceptable use policy
│   ├── data-deletion/                 # GDPR/Meta data deletion instructions
│   └── card/[token]/                  # Public digital card view
│
├── (dashboard)/                       # Main app (46 pages)
│   ├── dashboard/                     # Overview + KPIs
│   ├── accounts/                      # Connected accounts
│   ├── analytics/                     # Performance metrics
│   ├── inbox/                         # Unified inbox (Messenger/IG/WhatsApp)
│   ├── calendar/                      # Content calendar
│   ├── campaigns/                     # Email campaigns (EspoCRM audiences)
│   ├── ops/                           # Ops console (queue, loops, health)
│   ├── reports/                       # Generated report history/viewer
│   ├── card/                          # Digital card builder
│   ├── content/                       # Content management
│   │   ├── new/                       # Multi-platform composer
│   │   ├── article/new/               # LinkedIn article
│   │   ├── carousel/new/              # Carousel creator
│   │   ├── poll/new/                  # Poll creator
│   │   ├── story/new/                 # Story composer
│   │   ├── thread/new/               # Thread composer
│   │   ├── linkedin/                  # LinkedIn AI post
│   │   └── [id]/edit/                 # Post editor
│   ├── media/                         # Media library
│   │   ├── generate/                  # AI image generator
│   │   └── enhance/[id]/              # Enhancement studio
│   ├── messenger/                     # Messenger inbox
│   ├── whatsapp/                      # WhatsApp Business (setup, profile, send, bot)
│   ├── telegram/                      # Telegram bot/channel management
│   ├── tiktok/                        # TikTok account & posting
│   ├── brand/                         # Brand management
│   │   ├── identity/                  # Brand identity
│   │   ├── voice/                     # Voice & tone
│   │   ├── visual/                    # Visual identity
│   │   ├── guidelines/                # Shareable guidelines
│   │   ├── assets/                    # Brand assets
│   │   ├── health/                    # Brand health score
│   │   ├── competitors/               # Competitor tracking
│   │   ├── monitoring/                # Mention monitoring
│   │   ├── autopilot/                 # Auto-fill calendar
│   │   └── onboarding/                # Brand kit wizard
│   ├── workflows/                     # n8n workflow management
│   ├── team/                          # Team management
│   ├── settings/                      # User settings
│   │   ├── ai-providers/              # AI provider config
│   │   ├── ai-providers/usage/        # AI usage stats
│   │   ├── audit-logs/                # Audit trail
│   │   └── billing/                   # Plan, checkout, portal, Discount tab
│   ├── mcp-stack/                    # MCP sidecar status
│   ├── browser-login/                # Visual browser login
│   └── onboarding/                   # 4-step wizard
│
└── brand/guidelines/share/[token]/   # Public brand guidelines
```

### Page Inventory

#### Dashboard Pages

| Page | Path | Primary API | Features |
|------|------|-------------|----------|
| Dashboard | `/dashboard` | overview, top posts, scheduled | KPI cards, week calendar, heatmap, quick actions, onboarding checklist |
| Accounts | `/accounts` | accounts, connect/disconnect | Platform list, connect/disconnect, profile editor, business sync, setup guides |
| Analytics | `/analytics` | overview, platform, trends, export | KPI cards, engagement chart, follower growth, top posts, CSV export, sync |
| Calendar | `/calendar` | scheduled posts, pillars | Month/week views, drag reschedule, platform filters, pillar colors |
| Campaigns | `/campaigns` | email-campaigns API | EspoCRM audience sends, suppression list, per-recipient unsubscribe + survey tokens, "Draft with AI" (Visibility Era voice) |
| Ops | `/ops` | ops API | Ops console — publish queue state, loop health, digests |
| Reports | `/reports` | reports API | Notebook-generated report history/viewer (strategy brief, ops health, monetization) |
| New Post | `/content/new` | create post, upload, AI generate | Multi-platform composer, char limits, previews, media, AI caption, SEO |
| New Article | `/content/article/new` | create post, upload | LinkedIn article, cover image, title/body/tags, AI expand |
| Carousel | `/content/carousel/new` | create post, AI carousel | Topic-driven AI slides, theme, AI background, reorder, export PNG |
| New Poll | `/content/poll/new` | create post, publish | Twitter/LinkedIn poll, question/options/duration, preview |
| New Story | `/content/story/new` | create post, upload | Instagram/Facebook story, image/video, text overlay, link sticker |
| New Thread | `/content/thread/new` | create post, AI generate | Twitter/Threads thread, AI split, manual split, reorder, preview |
| LinkedIn Post | `/content/linkedin` | AI generate, publish | Topic-to-post AI, tone/length, improve, hashtags, best time |
| Edit Post | `/content/[id]/edit` | post CRUD, review, analytics | Edit content/media, approval workflow, comments, schedule, analytics |
| Media Library | `/media` | media CRUD, AI alt text | Grid, search, filters, upload, AI alt text, bulk delete, viewer |
| AI Image Gen | `/media/generate` | generate image | Text-to-image, aspect ratio, style presets, quality tuning, history |
| Enhance Studio | `/media/enhance/[id]` | enhance APIs | Resize, upscale, remove-bg, smart-crop, convert, compress, watermark |
| Messenger | `/messenger` | messenger, sidecar | Page/personal inbox, conversations, messages, auto-reply, sidecar status, E2EE support, noVNC session recovery, **Bot Builder** |
| Workflows | `/workflows` | templates, generate, deploy | Prompt gallery, n8n list, AI workflow gen, seed templates, executions |
| Team | `/team` | teams, invite, roles | List teams, create, switch, members, invite, remove, role change |
| Settings | `/settings` | auth, notifications, 2FA | Profile, password, 2FA, sessions, notifications, theme, export, delete |
| AI Providers | `/settings/ai-providers` | provider catalog | Browse, configure API keys, enable/disable, test, model browser |
| AI Usage | `/settings/ai-providers/usage` | usage stats | Calls, cost, latency, provider breakdown, Cloudflare quota, circuit breaker |
| Audit Logs | `/settings/audit-logs` | audit logs | Filter by action, paginated list, user/resource details |
| Billing | `/settings/billing` | billing config, plans, subscription, checkout, portal, discount | Plan cards, hosted checkout, portal, cancel, sync, **Discount tab** (Polar code save/validate/remove) |
| Inbox | `/inbox` | unified inbox | Cross-platform conversations (Page Messenger, personal Messenger, IG DMs, WhatsApp) |
| Telegram | `/telegram` | telegram bot/channel | Bot config, channel posting, group watch |
| TikTok | `/tiktok` | tiktok account/publish | Account status, video/photo posting, publish limits |
| Digital Card | `/card` | digital card CRUD | Card builder, public share link |
| MCP Stack | `/mcp-stack` | mcp stack | Sidecar status, capabilities, tools, screenshots, sessions |
| Browser Login | `/browser-login` | browser bridge | Visual login for Instagram/TikTok, noVNC, cookie import |
| Onboarding | `/onboarding` | auth, accounts | 4-step wizard: profile, connect, brand, first post |

#### Brand Pages

| Page | Path | Primary API | Features |
|------|------|-------------|----------|
| Brand Home | `/brand` | brand, extract | AI brand extraction from URL, manual create, overview |
| Identity | `/brand/identity` | brand update | Name, industry, tagline, positioning, mission, audience, values |
| Voice | `/brand/voice` | brand voice, analyze | AI voice analysis, tone sliders, pillars, banned/preferred phrases |
| Visual | `/brand/visual` | brand visual, generate | Colors, typography, AI logo/favicon, image style, photography |
| Guidelines | `/brand/guidelines` | compile | Shareable guidelines, public link, brand/voice/visual sections |
| Assets | `/brand/assets` | brand assets CRUD | List/add/delete assets (logo, favicon, og_image, templates) |
| Health | `/brand/health` | brand health | Health score, sentiment, reach, share-of-voice, engagement |
| Competitors | `/brand/competitors` | competitors | Snapshot list, new snapshot by name/platform |
| Monitoring | `/brand/monitoring` | mentions | Collect mentions, list by platform/sentiment/engagement |
| Autopilot | `/brand/autopilot` | trends, autopilot | Fill empty calendar slots, compliance score, trend scout |
| Brand Wizard | `/brand/onboarding` | brand wizard | 3-step brand kit setup wizard |

#### Auth Pages

| Page | Path | API | Features |
|------|------|-----|----------|
| Login | `/login` | auth.login | Email/password, redirect to dashboard |
| Register | `/register` | auth.register | Name/email/password, validation |
| Forgot Password | `/forgot-password` | auth.forgot | Email submission, reset token |
| Reset Password | `/reset-password` | auth.reset | Token validation, new password |
| Accept Invite | `/accept-invite` | teams.accept, auth | Decode JWT, register + accept |

#### Public Pages

| Page | Path | API | Features |
|------|------|-----|----------|
| About | `/about` | none | Company description, mission, contact |
| Features | `/features` | none | Feature grid, AI content, publishing, analytics |
| Pricing | `/pricing` | none | Pricing tiers, FAQ |
| API Docs | `/api-docs` | Swagger | Interactive API reference, JWT auth |
| Contact | `/contact` | none | Support channel (MoR requirement) |
| Terms | `/terms` | none | Terms of service |
| Privacy | `/privacy` | none | Privacy policy |
| Refund | `/refund` | none | Refund policy (MoR requirement) |
| Acceptable Use | `/acceptable-use` | none | Acceptable use policy |
| Data Deletion | `/data-deletion` | none | GDPR/Meta data-deletion instructions |
| Digital Card | `/card/[token]` | digital card | Public card view by token |

### Frontend Services

```
src/services/api.ts
├── authApi          — login (+ otp), register, refresh, forgot/reset, 2FA, profile
├── accountsApi      — list, connect, disconnect, sync, business accounts
├── billingApi       — config, plans, subscription, checkout, portal, cancel, sync, discount
├── contentApi       — posts CRUD, schedule, publish, review, comments
├── mediaApi         — upload, list, delete, generate image
├── mediaEnhanceApi  — resize, upscale, remove-bg, smart-crop, alt text
├── aiApi            — generate content, hashtags, carousel, image, SEO
├── aiProvidersApi   — provider catalog, config, test, usage
├── analyticsApi     — overview, platform, trends, export, sync
├── brandApi         — brand CRUD, voice, visual, guidelines, assets, health
├── messengerApi     — Page + personal, setup, send, conversations, auto-reply
├── whatsappApi      — WhatsApp Cloud API, setup, profile, bot
├── telegramApi      — Telegram bot/channel management
├── tiktokApi        — TikTok account status, publish
├── threadsApi       — Threads posting
├── linkedinApi      — LinkedIn AI post, publish
├── profileApi       — profile read/update across platforms
├── inboxApi         — unified inbox across platforms
├── digitalCardApi   — digital card CRUD, share links
├── workflowApi      — templates, generate, deploy, executions
├── teamsApi         — list, create, invite, members, roles, switch
├── publishingApi    — queue, schedule, publish now
├── secretsApi       — Cloudflare-first secret store
├── auditApi         — audit logs
└── browserApi       — noVNC sessions, cookie import
```

### Frontend Hooks

| Hook | Purpose |
|------|---------|
| `useAuth` | Session, login, register, logout, profile, 2FA |
| `useQueries` | React Query wrappers for all API calls |
| `useTheme` | Dark/light mode |
| `useNotifications` | Toast notifications |
| `useAdvisor` | AI advisor suggestions |
| `useUndoDelete` | Undo delete with timeout |
| `useTour` | Guided tour |

## Backend Architecture

### API Modules (572 decorated endpoints across 43 route files — 560 in the OpenAPI schema; the remaining 12 are `email_public_router` public unsubscribe/track surfaces mounted with `include_in_schema=False`)

```
app/api/
├── auth.py          (23) — login (+otp/lockout), register (+discount), refresh (jti rotation), 2FA, profile, password
├── accounts.py      (16) — connect, disconnect, sync, business accounts
├── billing.py       (13) — config, plans, subscription, checkout, portal, cancel, sync, webhooks, discount get/set/clear
├── content.py       (25) — posts CRUD, schedule, publish, review, comments
├── media.py         (22) — upload, list, delete, generate, view
├── media_enhance.py (13) — resize, upscale, remove-bg, smart-crop, alt text
├── ai.py            (43) — generate content, hashtags, carousel, image, SEO
├── ai_providers.py  (8)  — provider catalog, config, test, usage
├── analytics.py     (20) — overview, platform, trends, export, sync
├── web_analytics.py (6)  — site/web analytics events
├── brand.py         (29) — brand, voice, visual, guidelines, assets, health
├── bluesky.py       (2)  — AT Protocol account connect/status
├── digital_cards.py (10) — digital card CRUD, public share view
├── email_campaigns.py (16) — campaign create/audience/send + public
│                             unsubscribe/track router (out-of-schema)
├── viber.py         (13) — Viber bot webhook, send, broadcast
├── publishing.py    (8)  — queue, schedule, publish now, recurring
├── meta_growth.py   (2)  — free Meta readiness and media-backed organic campaigns
├── messenger.py     (34) — Page + personal, setup, send, auto-reply, webhook, E2EE, bot builder
├── messenger_api.py (14) — Graph API client for Page Messenger
├── inbox.py         (1)  — unified inbox across platforms
├── leads.py         (3)  — lead capture, webhook, CRM sync
├── support.py       (1)  — support contact endpoint
├── profile.py       (29) — profile read/update across platforms
├── linkedin.py      (19) — LinkedIn AI post, improve, hashtags, publish
├── reports.py       (4)  — report manifest list/detail for /reports page
├── service_feed.py  (7)  — service feed aggregation for ops surfaces
├── talk_bot.py      (1)  — Nextcloud Talk bot webhook
├── instagram.py     (9)  — Instagram private API, session, profile
├── threads.py       (16) — Threads post, reply, profile
├── twitter_tiktok.py (8) — Twitter/X and TikTok posting
├── tiktok.py        (7)  — TikTok account status, publish limits, cancel
├── whatsapp.py      (36) — WhatsApp Cloud API, setup, profile, register, bot
├── whatsapp_flows.py (14) — WhatsApp Flows CRUD, validate, publish, send
├── telegram.py      (38) — Telegram bot, channel posting, group watch, chatbot
├── newsletter.py     (2) — POST /api/v1/newsletter/cover: branded issue-cover PNG
│                           (1200x675 PIL render), and
│                           POST /api/v1/newsletter/linkedin/issue: publishes a
│                           LinkedIn newsletter issue via the personal-profile
│                           article editor. Both require X-Service-Token ==
│                           NEWSLETTER_SERVICE_TOKEN.

Integration flow (newsletter covers): cloudless.gr's weekly cron
(`/api/cron/newsletter-auto`, Mondays 09:00 Athens) POSTs the issue title to
this endpoint, uploads the returned PNG to R2 (`newsletter/covers/<slug>.png`,
served at `/api/newsletter/covers/<slug>.png`) and embeds it in the email +
article. SocialAuto owns generation; cloudless.gr owns delivery. Shared
secret: NEWSLETTER_SERVICE_TOKEN on both sides.

Integration flow (LinkedIn newsletter issues — "The Cloudless Build Log"):
n8n workflow `linkedin-newsletter-weekly` (Monday 11:00 Athens) resolves or
generates the weekly issue → `POST /newsletter/linkedin/issue` → the route
renders the branded cover, fills SEO title/description, and publishes.
`browser` picks the driver — `auto` (default): LinkedIn sidecar `:9225`
first, then `linkedin_newsletter_bridge` on the shared `browser-novnc`
bridge when the sidecar errors/times out (its Chromium profile loops on
LinkedIn's anti-bot; the bridge's persistent profile hydrates the editor).
`schedule_iso` schedules in LinkedIn's own publish dialog (real calendar
cell + timepicker selection — display-only inputs don't commit); `dry_run`
stops at the publish dialog. Personal-profile-bound = documented exception
to the business-accounts rule.
├── workflows.py     (18) — templates, generate, deploy, executions
├── teams.py         (10) — teams, members, invite, roles, switch
├── secrets.py       (5)  — Cloudflare-first secret store
├── cf_db.py         (6)  — D1/KV/Vectorize health, sync, tables
├── ops.py           (13) — health, system info, browser orchestrator,
│                           POST /session-heal (on-demand healer run),
│                           POST /daily-digest (n8n-triggered digest)
├── audit.py         (1)  — audit logs
├── mcp.py           (5)  — MCP stack status, sessions, screenshots
└── usage.py         (2)  — quota usage, history
```

### Data Models (19 model files)

```
app/models/
├── user.py          — User, Team (incl. polar_discount_code), TeamMember, AuditLog
├── social_account.py — SocialAccount (platform credentials, meta_data)
├── content.py       — Post, PostTarget, PostComment, MediaAsset,
│                      MediaCollection, Pillar, ContentBrief
├── brand.py         — Brand, BrandVoice, BrandVisual, BrandGuidelines,
│                      BrandAsset
├── brand_monitoring.py — BrandMention, CompetitorSnapshot
├── analytics.py     — AnalyticsEvent, PostAnalyticsSnapshot, FollowerSnapshot
├── web_analytics.py — Web analytics event models
├── queue.py         — PublishQueue
├── billing.py       — BillingEvent (webhook idempotency)
├── digital_card.py  — DigitalCard (public share cards)
├── lead.py          — Lead (CRM captures)
├── email_campaign.py — EmailCampaign, EmailCampaignRecipient (EspoCRM sends)
├── linkedin_ads.py  — LinkedIn ad campaign snapshots
├── whatsapp_message.py — WhatsAppMessage (unified-inbox persistence)
├── workflow.py      — PromptTemplate, GeneratedWorkflow, ContentPromptTemplate
├── ai_provider.py   — AIProvider
├── ai_usage.py      — AIUsageLog
├── social_secret.py — SocialSecret
└── email_log.py     — EmailLog
```

### Service Layer (137 services)

```
app/services/
├── ── Platform APIs ──────────────────────────────────────────
│   ├── facebook_api.py        — Graph API client
│   ├── instagram_api.py       — Graph API client
│   ├── instagram_private_api.py — instagrapi wrapper
│   ├── instagrapi_client.py   — aiograpi REST client
│   ├── free_instagram_client.py — free Instagram client
│   ├── linkedin_api.py        — LinkedIn API client
│   ├── linkedin_ai.py        — LinkedIn AI post generation
│   ├── threads_api.py        — Threads API client
│   ├── tiktok_api.py         — TikTok Content API client
│   ├── tiktok_browser.py     — TikTok browser automation
│   ├── tiktok_profile.py     — TikTok profile management
│   ├── tiktok_bio_update.py  — TikTok bio update
│   ├── tiktok_captcha*.py    — TikTok captcha solving (api, analyze, mobile, debug variants)
│   ├── tiktok_xbogus.py      — TikTok X-Bogus signing
│   ├── twitter_api.py        — Twitter/X API v2 client
│   ├── twitter_profile.py    — Twitter profile management
│   ├── x_web.py              — Free X web fallback (tweety post/read, twscrape read; cookie auth, breaker + limits)
│   ├── telegram_api.py       — Telegram Bot API client
│   ├── telegram_agent.py     — Channel agent (pillar cadence, polls, welcomes, UTM CTAs)
│   ├── whatsapp_api.py       — WhatsApp Business helpers
│   ├── whatsapp_cloud_client.py — WhatsApp Cloud API client
│   ├── meta_graph.py         — Meta Graph URL builder
│   └── hikerapi_client.py    — HikerAPI Instagram client
│
├── ── Messenger ─────────────────────────────────────────────
│   ├── messenger_api.py      — Page Messenger Graph API
│   ├── messenger_sidecar.py  — Webhook sidecar (AI auto-reply)
│   ├── messenger_chatbot.py  — Chatbot service (memory, RAG, intent, cooldown)
│   │   ├── DMR-first intent detection (Qwen3 8B)
│   │   ├── ChromaDB conversation memory (last 10 msgs/thread)
│   │   ├── ChromaDB brand knowledge RAG
│   │   ├── Redis DB 1 cooldowns (5 min/conversation)
│   │   ├── Redis DB 1 per-thread pause (human handoff)
│   │   └── Redis DB 1 per-thread config overrides
│   ├── whatsapp_chatbot.py   — WhatsApp chatbot (DMR-first inference)
│   ├── telegram_chatbot.py   — Telegram chatbot replies
│   ├── telegram_group_watch.py — Telegram group keyword watch
│   └── browser_bridge.py     — Personal Messenger (CDP + noVNC)
│       ├── E2EE + regular thread support
│       ├── ensure_session() — auto-recover + cookie extraction;
│       │   a Redis verified-dead marker (browser_bridge:dead:*, 30 min)
│       │   makes repeat polls on a logged-out platform skip in ~1s
│       │   instead of re-burning ~200s of busy-hold — keeps the bridge
│       │   free for the hourly session healer
│       └── _navigate_to_thread() — SPA-safe navigation
│
├── ── Browser Automation ────────────────────────────────────
│   ├── browser_profile.py    — Browser session management
│   ├── browser_orchestrator.py — Redis-lock browser session scheduler
│   ├── facebook_sidecar.py    — Facebook browser sidecar
│   └── linkedin_sidecar.py  — LinkedIn browser sidecar
│
├── ── AI / Inference ────────────────────────────────────────
│   ├── inference.py          — Multi-provider inference router
│   ├── dmr.py                — Docker Model Runner client
│   ├── cf_models.py          — Cloudflare Workers AI client
│   ├── media_ai.py           — AI image generation
│   ├── carousel_pipeline.py  — LinkedIn carousel pipeline
│   ├── linkedin_ai.py        — LinkedIn AI post generation
│   └── brand_agent.py        — Brand AI agent
│
│   generate-content pipeline (POST /api/v1/ai/generate-content):
│     chroma query_similar → inference → plain-English → brand
│     compliance → quality pipeline → response.
│     The caller's ``provider`` is threaded through EVERY stage
│     (generation, rewrite, tone-match, quality loop) so e.g.
│     ``provider: cloudflare`` never queues behind the local DMR
│     slot; the interactive frontend defaults to ``cloudflare``
│     (api.ts generateContent — the DMR chain exceeds the nginx
│     gateway's 300s proxy_read_timeout on CPU-bound hardware),
│     while n8n/celery callers hit social-api directly and keep
│     ``dmr``. Compliance/quality calls pass db+team_id so
│     team-stored provider keys resolve. Chroma indexing of the
│     final (post-quality) draft runs as a bounded background
│     task (_MAX_PENDING_INDEX_TASKS=2 — drops instead of
│     queuing ahead of later query_similar reads on the shared
│     DMR slot). Cloudflare failures still fall back through
│     Groq/HF per inference.py allow_fallback semantics.
│
├── ── Media ─────────────────────────────────────────────────
│   ├── media_storage.py      — Storage abstraction (R2→MinIO→disk); enqueues the
│   │                           nextcloud export task when NEXTCLOUD_* is configured
│   ├── nextcloud_export.py   — WebDAV mirror of assets to the omv Nextcloud workspace
│   │                           (cloud.cloudless.gr, SocialAuto/media/<Y>/<m>/<d>/);
│   │                           shares via OCS: create_public_share takes export-root-
│   │                           relative paths, create_share_for_recorded takes recorded
│   │                           root-included paths (nextcloud_path meta)
│   ├── r2_storage.py         — Cloudflare R2 storage
│   ├── r2_presigned.py       — R2 presigned URLs
│   ├── minio_storage.py      — MinIO S3 storage
│   ├── image_enhance.py      — Image enhancement
│   ├── image_transform.py    — Image transforms
│   ├── infographic_renderer.py — Procedural infographic renderer
│   └── content_renderer.py   — Content rendering
│
├── ── CRM & Leads ───────────────────────────────────────────
│   ├── espocrm_client.py     — EspoCRM REST client (X-Api-Key → internal
│   │                           NodePort 192.168.1.200:30700; public host is
│   │                           behind CF Access): leads/contacts/accounts/
│   │                           targetlists reads, notes, convert action;
│   │                           socialauto role has User + TargetList read
│   │                           (see also leads.py / lead_capture.py under
│   │                            Analytics & Reports below)
│   ├── crm_hygiene.py        — Weekly dedupe/quarantine (losers → Dead +
│   │                           audit note; never deletes)
│   ├── crm_lifecycle.py      — Deterministic lead scoring (hot ≥65 / warm
│   │                           ≥35, mirrors cloudless.gr lead-scoring.ts)
│   │                           + score-gated Lead→Contact/Account/
│   │                           Opportunity convert
│   ├── crm_engagement.py     — Dovecot IMAP reply poll → CRM note +
│   │                           status bump + Slack
│   ├── lead_research.py      — Lead enrichment research sweep
│   ├── email_drip.py         — First-approach + renewal drip via omv SMTP
│   ├── promo_scan.py         — Promo/newsletter IMAP scan (proves the
│   │                           dovecot credential chain crm_engagement reuses)
│   └── company_brain.py      — Daily company_brain journal to NC surfaces
│
├── ── Quality ───────────────────────────────────────────────
│   ├── media_quality.py      — Media text quality pipeline (concurrent
│   │                           per-field spellcheck + NLP + SEO +
│   │                           gibberish flag on caption/alt_text)
│   ├── media_spellcheck.py    — Media spellcheck
│   ├── spellcheck.py          — LanguageTool spellcheck; auto_correct_detailed
│   │                           is category-aware (error rules apply, style
│   │                           classes skip) + overlap-safe + fix report
│   ├── plain_english.py       — NLP plain-English check/fix
│   ├── content_scorer.py      — Content SEO scoring
│   ├── seo.py                 — SEO optimization
│   ├── quality_pipeline.py    — Full quality pipeline +
│   │                           apply_publish_gate (PUBLISH_MIN_SEO_SCORE=80
│   │                           go-live bar; verdict cached per content hash;
│   │                           fails open on infra errors)
│   ├── duplicate_detector.py  — Duplicate content detection
│   └── url_safety.py          — URL safety check
│
├── ── Brand ─────────────────────────────────────────────────
│   ├── brand_voice.py        — Brand voice analysis
│   ├── brand_assets.py       — Brand asset management
│   ├── brand_compliance.py   — Brand compliance checking
│   ├── brand_extractor.py    — Brand extraction from URL
│   ├── brand_monitoring.py   — Brand mention monitoring
│   └── trend_scout.py        — Trend scouting
│
├── ── Billing / MoR ─────────────────────────────────────────
│   ├── polar_api.py          — Polar.sh client (checkouts, subs, portal, discounts)
│   ├── polar_digest.py       — Polar billing digest
│   ├── dodo_api.py           — Dodo Payments client (code-ready)
│   ├── dodo_digest.py        — Dodo billing digest
│   ├── paddle_api.py         — Paddle client (legacy provider)
│   └── paddle_digest.py      — Paddle billing digest (provider dispatch)
│
├── ── Database / Storage ────────────────────────────────────
│   ├── db_router.py          — D1→PostgreSQL dual-write router
│   ├── db_sync.py            — D1↔Postgres sync
│   ├── d1_client.py          — Cloudflare D1 client
│   ├── kv_client.py          — Cloudflare KV client
│   ├── vectorize_client.py   — Cloudflare Vectorize client
│   ├── chroma_client.py     — ChromaDB local client
│   └── secret_store.py      — Cloudflare-first secret store
│
├── ── Infrastructure ───────────────────────────────────────
│   ├── rate_limiter.py       — Rate limiting
│   ├── usage_tracker.py      — Usage tracking
│   ├── email_templates.py    — Transactional email
│   ├── email_digest.py       — Email digest
│   ├── slack_digest.py       — Slack digest
│   ├── slack_notifications.py — Slack event notifications
│   │                           Slack delivery: dead webhooks (app uninstall →
│   │                           404 no_service) fall back to SLACK_BOT_TOKEN +
│   │                           channel id via chat.postMessage. The bot token
│   │                           is kept fresh by the slack-ops skill
│   │                           (.devin/skills/slack-ops/scripts/slack_token_sync.py,
│   │                           2h cron): it rotates the mcp-remote xoxe user
│   │                           token, mirrors it into .env, and recreates the
│   │                           token consumers (social-api + 4 workers +
│   │                           celery-beat + n8n — n8n only once its running
│   │                           executions drain, since EXECUTIONS_MODE=regular
│   │                           shares the container: a graceful
│   │                           `compose stop -t` (SIGTERM refuses new triggers,
│   │                           drains in-flight runs) precedes the recreate, a
│   │                           failed executions probe defers unless docker
│   │                           reports the container stopped, and deferred
│   │                           services wait in a per-service pending marker
│   │                           for the next run).
│   │                           post_publishing_to_slack shares the fallback
│   │                           (webhook → bot token + SLACK_PUBLISHING_CHANNEL_ID).
│   ├── analytics_sync.py     — Analytics synchronization. LinkedIn member
│   │                           path: sidecar activity scrape (primary) +
│   │                           memberCreatorPostAnalytics when the token
│   │                           carries r_member_postAnalytics — skipped via
│   │                           _member_post_analytics_scope_missing when the
│   │                           recorded scopes prove it absent; follower
│   │                           writes guarded by _plausible_follower_count
│   ├── cf_analytics.py       — Cloudflare analytics client
│   ├── web_analytics.py      — Web analytics aggregation
│   ├── support_report.py     — Support report builder
│   ├── leads.py              — Lead management
│   ├── lead_capture.py       — Shared DM lead-capture FSM (Messenger/IG/
│   │                           Telegram; Redis session, snapshot/restore)
│   ├── whatsapp_flows.py     — WhatsApp Flows service
│   └── publishing.py         — Publishing service
│
├── ── Agency Pipeline ────────────────────────────────────────
│   ├── agency_catalog.py    — 5-service fixed-scope catalog + lead classifier
│   ├── agency_pipeline.py   — triage/audit/proposal/PM/engineer sweeps
│   ├── site_audit.py        — Architect: SSRF-guarded site audit (headers/TTFB/stack)
│   ├── influence_stage.py   — Cheng et al. 5-stage influencer model
│   └── agency API           — /api/v1/agency/* (audits, proposals, projects, catalog)
```

### MCP Server (35 tools)

```
app/mcp/server.py
├── ── Core (12) ─────────────────────────────────────────────
│   ├── list_accounts         — List connected social accounts
│   ├── get_account           — Get account details
│   ├── create_post           — Create a new post
│   ├── list_posts            — List posts
│   ├── publish_post          — Publish a post
│   ├── generate_content      — AI content generation
│   ├── suggest_hashtags      — AI hashtag suggestions
│   ├── score_content         — Content SEO scoring
│   ├── get_analytics         — Analytics overview
│   ├── list_media            — List media library
│   ├── get_profile           — Get profile info
│   └── get_brand             — Get brand info
│
├── ── Page Messenger (9) ───────────────────────────────────
│   ├── messenger_setup              — Set up Page Messenger
│   ├── messenger_get_profile        — Get Messenger profile
│   ├── messenger_update_profile     — Update profile
│   ├── messenger_send_message       — Send message
│   ├── messenger_list_conversations — List conversations
│   ├── messenger_get_messages       — Get thread messages
│   ├── messenger_get_auto_reply     — Get auto-reply config
│   ├── messenger_set_auto_reply     — Set auto-reply config
│   └── messenger_unsubscribe        — Remove subscription
│
└── ── Personal Messenger (6) ───────────────────────────────
    ├── messenger_list_all_accounts        — List all accounts
    ├── messenger_personal_conversations    — List personal convos (E2EE + regular)
    ├── messenger_personal_messages         — Read personal messages (E2EE + regular)
    ├── messenger_personal_send             — Send personal message (E2EE + regular)
    ├── messenger_personal_get_auto_reply   — Get personal auto-reply
    └── messenger_personal_set_auto_reply   — Set personal auto-reply

└── ── Telegram (8) ─────────────────────────────────────────
    ├── telegram_list_channels            — List channels/groups the bot is in
    ├── telegram_chat_info                — Get chat metadata
    ├── telegram_chat_admins              — List chat administrators
    ├── telegram_member_count             — Member count for a chat
    ├── telegram_set_chat_description     — Set channel/group description
    ├── telegram_create_invite_link       — Create named invite link
    ├── telegram_send_message             — Send message to a chat
    └── telegram_pin_message              — Pin a message

(Bot Builder lives in the REST API + `/messenger` UI, not in MCP tools.)
```

### Celery Tasks (46 task modules, 45 beat schedules)

```
app/worker/tasks/
├── publishing.py           — process_publish_queue, check_scheduled, publish_now
├── analytics.py            — sync_all_analytics, sync_team_analytics
├── media.py                — auto_tag_asset
├── media_enhance.py        — batch_enhance
├── token_refresh.py        — refresh_expiring_tokens
├── recurring.py            — process_recurring_posts
├── digest.py               — send_daily_slack_digest, send_weekly_slack_digest,
│                             send_daily_strategy_report (code-path fallback)
├── notebook_reports.py     — run_notebook_report: papermill-executes
│                             notebooks/reports/*.ipynb in the worker env and
│                             emails the rendered manifest (see "Notebook-
│                             generated reports" under Jupyter below)
├── datalake_export.py      — export_datalake: JSON snapshots → R2 lake/*
│                             + edge-metrics (Tempo TraceQL aggregates),
│                             report-manifest index, ops-health snapshot
├── linkedin_ads_report.py  — send_linkedin_ads_report: Campaign Manager
│                             scrape → ad_campaign_snapshots → Slack/email
├── linkedin_ads_control.py — linkedin_ads_control: Slack pause/resume/status
├── linkedin_invites.py     — send_linkedin_invites: Page invitation batches
├── instagram_session_check.py — check_instagram_sessions (business_login
│                             accounts validate the Graph OAuth token first;
│                             sidecar probe is fallback only)
├── instagram_token_refresh.py — refresh_instagram_tokens
├── linkedin_session_check.py  — check_linkedin_sessions
├── linkedin_session_refresh.py — refresh_linkedin_sessions
├── session_healer.py       — heal_sessions: hourly self-heal sweep —
│                             LinkedIn sidecar (429-circuit clear →
│                             credential login → app-push 2FA wait),
│                             Facebook sidecar (deep validate → stored
│                             storage_state re-inject), and every bridge
│                             platform (persistent-profile detect →
│                             cookie inject / credential login / Threads
│                             IG-SSO bootstrap). Persists fresh session
│                             material to meta_data + secret store;
│                             clears the pollers' verified-dead marker on
│                             recovery; Slack-alerts only when human
│                             action needed (24h per-platform cooldown).
│                             On-demand: POST /api/v1/ops/session-heal
├── workflows.py            — execute_workflow, deploy_workflow
├── telegram_digest.py      — send_telegram_group_digests
├── paddle_digest.py        — send_paddle_slack_digest (billing digest)
├── dodo_live_check.py      — check_dodo_live (billing live-check)
├── whatsapp_verify.py      — check_whatsapp_verification
├── tiktok_inbox_reconcile.py — reconcile_tiktok_inbox
├── nextcloud.py            — export_media_to_nextcloud (WebDAV PUT → omv)
├── nextcloud_sync.py       — sync_nextcloud_surfaces: posts→Deck board +
│                             follower_snapshots→Tables (3h)
├── company_brain.py        — write_company_brain_journal: daily ops journal →
│                             Notes/Collectives surfaces (23:45)
├── lead_emails.py          — send_playbook_email (lead-magnet drip)
├── crm_hygiene.py          — run_crm_hygiene: weekly dedupe/quarantine —
│                             duplicate losers marked Dead + audit note on the
│                             keeper (never deletes; psid.*@messenger.local and
│                             test/self mailboxes never quarantined)
├── crm_lifecycle.py        — run_crm_lifecycle_sweep: score every lead
│                             ingress path (contact form, playbook, Messenger,
│                             manual) with the deterministic cloudless.gr
│                             model (hot ≥65, warm ≥35; `Lead score:` note =
│                             dedupe, newer reply re-scores); hot real-email
│                             leads → EspoCRM POST /Lead/action/convert
│                             (Contact+Account+Opportunity, CRM_CONVERT_ENABLED)
├── crm_engagement.py       — run_crm_engagement_sweep: 30-min IMAP reply poll
│                             on the self-hosted Dovecot mailbox (promo_scan
│                             credential chain); sender→Lead/Contact match →
│                             `Reply received` note (msg-id dedupe) +
│                             New/Assigned→In Process + Slack alert on hits
├── email_drip.py           — run_email_drip_sweep: daily first-approach +
│                             renewal drip via omv SMTP (08:20, before digest)
├── email_campaigns.py      — email campaign send orchestration (EspoCRM
│                             audiences, per-recipient unsubscribe)
├── lead_research.py        — run_lead_research_sweep: daily enrichment of
│                             unscored/unresearched leads (08:40)
├── promo_scan.py           — run_promo_scan: daily 08:00 promo/newsletter
│                             IMAP scan → research/send queue
├── monetization.py         — monetization readiness tasks
├── telegram_agent.py       — run_channel_agent_daily: managed-channel
│                             autopilot (pillar posts Mon/Wed/Fri, Sunday
│                             poll, DM lead funnel)
├── skool_watch.py          — watch_skool_vec: Sofia Kakkava Skool community
│                             scrape via browser bridge → new DAY lessons
│                             auto-draft LinkedIn posts + Slack alert
├── dmr_health.py           — check_dmr_health, keep_warm_dmr_models
├── personal_messenger.py   — poll_personal_messenger (auto-reply, E2EE + regular, 20 convos/poll)
├── instagram_messenger.py  — poll_instagram_messenger (browser bridge fallback, orchestrator,
│                               Redis breaker on Meta app-level rate limits: Graph code 4/32/613
│                               or subcode 1349210 opens a 30-min breaker — Graph skipped, bridge only;
│                               per-conversation fetch is skipped when the inbox-embedded latest
│                               message shows nothing new — idle polls drop to ~3 Graph calls)
├── threads_messenger.py    — poll_threads_messenger (browser bridge, orchestrator)
├── linkedin_messenger.py   — poll_linkedin_messenger (browser sidecar, 6h —
│                             LinkedIn rate-limits DM polling hard)
├── twitter_messenger.py   — poll_twitter_messenger (browser bridge, orchestrator)
└── tiktok_messenger.py     — poll_tiktok_messenger (browser bridge, orchestrator)

Worker async model (`app/worker/_async.py`): each prefork child runs ONE
asyncio event loop on a daemon thread, started by `worker_process_init`
in celery_app.py (after the fork — never shared across it). Tasks submit
coroutines via `run_async()` and get DB sessions from `task_session()`
(or `get_session_factory()` when a factory is needed) on a real pooled
engine created inside that loop. This replaced the per-task
`asyncio.run()` + NullPool pattern — the old model crashed with "Future
attached to a different loop" whenever a second task reused a pooled
connection bound to a dead loop, and paid a full reconnect per task.
Never combine `asyncio.run()` with `async_session_maker()` in a task.

Beat Schedule:
┌──────────────────────────┬────────────────────────────────┬──────────┐
│ Name                     │ Task                           │ Schedule │
├──────────────────────────┼────────────────────────────────┼──────────┤
│ process-publish-queue    │ publishing.process_publish_queue│ 30s      │
│                          │  (claims via FOR UPDATE SKIP     │          │
│                          │   LOCKED; stale locks >15min     │          │
│                          │   auto-reclaimed; dedupe via     │          │
│                          │   ux_publish_queue_active_target │          │
│                          │   + already-published guard)     │          │
│ check-scheduled-posts    │ publishing.check_scheduled_posts│ 60s      │
│ cleanup-publish-queue    │ publishing.cleanup_publish_queue│ Sun 03:00│
│ sync-analytics           │ analytics.sync_all_analytics   │ 1800s    │
│ process-recurring-posts  │ recurring.process_recurring    │ 300s     │
│ export-datalake          │ datalake_export.export_datalake │ 6h :10   │
│ daily-strategy-report    │ notebook_reports.               │ daily    │
│                          │  run_notebook_report            │ 10:30    │
│                          │  (papermill → email; falls back │ EEST     │
│                          │   to code-path brief on failure)│          │
│ daily-ops-health         │ notebook_reports.               │ daily    │
│                          │  run_notebook_report            │ 10:45    │
│                          │  (ops_health notebook — queue,  │ EEST     │
│                          │   failures, follower anomalies, │          │
│                          │   sync freshness; no fallback)  │          │
│ daily-paddle-digest      │ paddle_digest.send_paddle_      │ daily    │
│                          │  slack_digest                   │          │
│ linkedin-ads-daily-report│ linkedin_ads_report             │ 10:00    │
│ send-linkedin-invites    │ linkedin_invites                │ 10:30    │
│ reconcile-tiktok-inbox   │ tiktok_inbox_reconcile          │ 6h :20   │
│ poll-personal-messenger  │ personal_messenger.poll         │ 120s     │
│                         │  (bot: memory+RAG+intent+cooldown)│          │
│ poll-instagram-messenger │ instagram_messenger.poll       │ 180s     │
│ poll-threads-messenger   │ threads_messenger.poll          │ 180s     │
│ poll-twitter-messenger   │ twitter_messenger.poll         │ 300s     │
│ poll-tiktok-messenger    │ tiktok_messenger.poll          │ 300s     │
│ poll-linkedin-messenger  │ linkedin_messenger.poll        │ 6h       │
│                          │  (LinkedIn rate-limits browser  │          │
│                          │   DM polling hard)              │          │
│ refresh-expiring-tokens  │ token_refresh.refresh           │ :15/:45  │
│ check-instagram-sessions │ instagram_session_check        │ 6h :30   │
│ refresh-instagram-tokens │ instagram_token_refresh        │ weekly   │
│ check-linkedin-sessions  │ linkedin_session_check          │ 12h :45  │
│ refresh-linkedin-sessions│ linkedin_session_refresh        │ weekly   │
│ check-whatsapp-          │ whatsapp_verify                 │ 30min    │
│  verification            │                                 │          │
│ telegram-channel-agent   │ telegram_agent.run_channel_     │ daily    │
│                          │   agent_daily                   │ 11:00    │
│                          │  (managed-channel autopilot:    │          │
│                          │   pillar posts Mon/Wed/Fri,     │          │
│                          │   Sunday poll, offer CTAs →     │          │
│                          │   DM lead funnel)               │          │
│ telegram-group-digests   │ telegram_digest                 │ hrly :05 │
│ dmr-health-check         │ dmr_health.check_dmr_health     │ 300s     │
│ keep-warm-dmr-models     │ dmr_health.keep_warm_dmr_models │ 90s      │
│                          │  (keep_alive refresh on warm    │          │
│                          │   tier; skips while GPU busy)   │          │
│ dodo-live-check          │ dodo_live_check                 │ 1800s    │
│ heal-sessions            │ session_healer.heal_sessions    │ hourly   │
│                          │  (:20 — all browser transports, │          │
│                          │   auto-recover + persist + alert)│         │
│ weekly-slack-rollup      │ digest.send_weekly_slack_digest │ Mon 9am  │
│ monthly-slack-rollup     │ digest.send_weekly_slack_digest │ 1st 9am  │
│                          │   (days=30 → MoM + forecast +   │          │
│                          │    funnel, Slack + email)       │          │
│ requeue-capacity-stuck-x │ publishing.requeue_capacity_    │ 30min    │
│                          │  stuck_x_targets                │          │
│ skool-vec-watch          │ skool_watch.watch_skool_vec     │ 3h       │
│ sync-nextcloud-surfaces  │ nextcloud_sync.sync_nextcloud_  │ 3h :25   │
│                          │  surfaces (posts→Deck,          │          │
│                          │  followers→Tables)              │          │
│ company-brain-journal    │ company_brain.write_company_    │ daily    │
│                          │  brain_journal                  │ 23:45    │
│ daily-promo-scan         │ promo_scan.run_promo_scan       │ daily    │
│                          │  (send=true)                    │ 08:00    │
│ email-drip-sweep         │ email_drip.run_email_drip_sweep │ daily    │
│                          │  (first-approach + renewal)     │ 08:20    │
│ daily-monetization-report│ notebook_reports.               │ daily    │
│                          │  run_notebook_report            │ 08:30    │
│                          │  (monetization_daily notebook)  │          │
│ lead-research-sweep      │ lead_research.run_lead_         │ daily    │
│                          │  research_sweep                 │ 08:40    │
│ crm-lifecycle-sweep      │ crm_lifecycle.run_crm_          │ daily    │
│                          │  lifecycle_sweep (score all     │ 08:50    │
│                          │   ingress; hot→convert)         │          │
│ crm-engagement-sweep     │ crm_engagement.run_crm_         │ 30min    │
│                          │  engagement_sweep (IMAP reply   │          │
│                          │   → note + status + Slack)      │          │
│ crm-hygiene              │ crm_hygiene.run_crm_hygiene     │ Mon      │
│                          │  (dedupe/quarantine, Dead mark) │ 07:30    │
└──────────────────────────┴────────────────────────────────┴──────────┘

> The **daily Slack digest is NOT a beat entry** — the n8n workflow
> `socialauto-daily-slack-digest` (cron `0 9 * * *`) calls
> `POST /api/v1/ops/daily-digest` instead, so its schedule is edited in n8n.
```

## Platform Support

| Platform | OAuth | Publishing | Analytics | Profile | Messenger | Special |
|----------|-------|-----------|-----------|---------|-----------|---------|
| Facebook | ✓ | ✓ | ✓ | ✓ (Graph) | ✓ Page + Personal (E2EE) + Bot Builder | Page sidecar, browser bridge |
| Instagram | ✓ | ✓ (Business Login + FB Login) | ✓ | ✓ (Graph + private) | ✓ DM (Messenger Platform) | Private API sidecar, Web API |
| LinkedIn | ✓ | ✓ | ✓ | ✓ (API + browser) | — | Company Page, browser sidecar |
| Twitter/X | ✓ (OAuth 2.0 PKCE) | ✓ (API v2 → x_web → browser bridge) | ✓ (API v2 → x_web) | ✓ (API) | ✓ DM (API v2) | API v2; on 402 credits-depleted the opt-in `x_web` fallback (tweety/twscrape, cookie auth, 5 posts/day, 10–30 min gap, 6h breaker) — see [guide 17](superpowers/guides/17-x-web-fallback.md) |
| TikTok | ✓ | ✓ (Direct Post + Upload) | ✓ | ✓ (API + browser) | ✓ DM (Business Messaging) | Browser sidecar, domain verification, 6-scope OAuth |

> **TikTok ops tooling** (`.devin/skills/`): `check-scopes.py` audits granted
> OAuth scopes, `tiktok-reconnect.py` re-runs consent for scope upgrades via
> the stored web session, `cp-audit-application.py` drives the Content Posting
> API audit wizard. See guide `docs/superpowers/guides/10-tiktok-content-posting.md`.
>
> **TikTok analytics sources**: account-level stats (followers, following,
> likes, video count) come from the official `/v2/user/info/` endpoint when
> `user.info.stats` is granted — the event is marked `api_source: user.info`.
> With only `user.info.profile` granted, the API refreshes bio/verified/deep-
> link but the yt-dlp profile scrape still records follower counts. The scrape
> remains the fallback and the only source covering MEDIA_UPLOAD inbox posts
> and videos published directly from the phone.
| Threads | ✓ | ✓ (text, image, video, carousel) | ✓ | ✓ (API) | — | Instagram-based, v1.0 |
| WhatsApp | ✓ | — | — | ✓ (Cloud API) | ✓ Cloud API | WABA, phone verification |

> **LinkedIn analytics — two developer apps.** The main "Cloudless API App"
> (227354605) carries Share on LinkedIn + Advertising API and provisions
> org/company-page analytics (`r_organization_social`). **Member post
> analytics** (`memberCreatorPostAnalytics`, `r_member_postAnalytics`) is a
> Community Management API member permission — CMA must be the only product
> on its app, so a dedicated "Cloudless Analytics App" (264925843) holds the
> request, submitted 2026-09-25 and **pending LinkedIn review**. Until
> approved, `sync_linkedin_account` marks member posts
> `member_postAnalytics_scope_missing` — checked proactively against the
> recorded OAuth scope list (`_member_post_analytics_scope_missing`), so no
> doomed API calls are made — and the daily brief renders
> `— (member metrics scope missing)`. The browser sidecar's activity-page
> scrape covers impressions/reactions/comments in the meantime. Never add
> the scope to `LINKEDIN_EXTRA_SCOPES` on the main app — OAuth rejects it
> with `unauthorized_scope_error`. Runbook: `.devin/skills/developer-apps-ops/linkedin-api-upgrade/`.

### API Versions (as of September 2026)

| Platform | API Version | Base URL | Version Header |
|----------|------------|----------|----------------|
| Facebook Graph | v26.0 | `graph.facebook.com` | URL path |
| Instagram Graph | v26.0 | `graph.facebook.com` | URL path |
| Instagram Business Login | v26.0 | `graph.instagram.com` | URL path |
| LinkedIn REST | 202608 | `api.linkedin.com/rest` | `Linkedin-Version` header |
| Twitter/X | v2 | `api.x.com/2` | URL path |
| TikTok | v2 | `open.tiktokapis.com/v2` | URL path |
| Threads | v1.0 | `graph.threads.net/v1.0` | URL path |
| WhatsApp Cloud | v26.0 | `graph.facebook.com` | URL path |
| Messenger | v26.0 | `graph.facebook.com` | URL path |

See `docs/api-integration-audit.md` for the full endpoint-by-endpoint crosscheck against official documentation.

## Docker Compose Stack (38 services + 1 blue/green slot)

> **Base-image registry:** all `FROM` lines and CI service images pull via
> `mirror.gcr.io/library/*` (Google's free Docker Hub mirror), not
> `public.ecr.aws`. ECR Public rate-limits anonymous pulls from GitHub
> Actions runners (HTTP 429), which flaked `container-scan` and backend CI
> repeatedly (fixed 2026-10-13, PR #596 + follow-up). Trivy DB mirrors
> prefer GHCR with ECR as fallback (`TRIVY_*_REPOSITORY` in security.yml
> and trivy-scan.yml).

### Core Application

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Core Application Services                         │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ social-api  │  │ social-     │  │ social-     │  │ social-    │ │
│  │ :8083       │  │ frontend    │  │ worker-     │  │ worker-    │ │
│  │             │  │ :8082       │  │ publishing  │  │ media      │ │
│  │ FastAPI     │  │ Next.js     │  │             │  │            │ │
│  │ 560 routes  │  │ 64 pages    │  │ Celery      │  │ Celery     │ │
│  │ MCP server  │  │             │  │ publishing  │  │ media      │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────┬──────┘ │
│         │                │                │                │        │
│  ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐               │
│  │ social-     │  │ celery-beat │  │ social-     │               │
│  │ worker-     │  │             │  │ postgres    │               │
│  │ default     │  │ Scheduler   │  │ :5433       │               │
│  │             │  │             │  │             │               │
│  │ Celery      │  │43 schedules │  │ PostgreSQL  │               │
│  │ default     │  │             │  │ 16-alpine   │               │
│  └─────────────┘  └─────────────┘  └─────────────┘               │
└─────────────────────────────────────────────────────────────────────┘
```

### Infrastructure

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Infrastructure Services                           │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ redis       │  │ minio       │  │ chroma      │  │ language-  │ │
│  │ :6379       │  │ :9100/9101  │  │ :8001       │  │ tool :8010 │ │
│  │             │  │             │  │             │  │            │ │
│  │ Broker +    │  │ S3 storage  │  │ Vector DB   │  │ Spellcheck │ │
│  │ cache       │  │ fallback    │  │ fallback    │  │ server     │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ comfyui     │  │ local-      │  │ warp-proxy  │  │ cloudflared│ │
│  │ :8000       │  │ diffusers   │  │ :1080       │  │            │ │
│  │             │  │             │  │             │  │ social-    │ │
│  │ GPU image   │  │ retired     │  │ WARP SOCKS5 │  │ cloudless  │ │
│  │ workflows   │  │ (SD 1.5)    │  │ free proxy  │  │ tunnel     │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ stack-ops (:8787 ctl) — idle-sleep + wake-on-connect proxy    │   │
│  │ fronts all idle-tolerant services; sleeps them after an idle │   │
│  │ TTL, `docker start`s on connect, publishes their host ports. │   │
│  │ See docs/ops/stack-ops-idle-sleep.md                         │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘

Three remotely-managed Cloudflare Tunnels (as of 2026-10-29):
`social-cloudless` (on this host) serves `social.cloudless.gr` only;
`omv-cloudless` (`cloudflared-omv` on omv, compose mirror
`deploy/omv-workspace/cloudflared/`) serves `cloud`/`office`/`signal`/
`push.cloudless.gr`; and **`cloudless`** (id `e977a490-…`, connector on
omv) serves the omv ops hostnames — ntfy, ntfy-ws, kuma, grafana, meili,
docs, espocrm, n8n, postiz, omv, webmail, ftp, manage, pi-origin,
appflowy, logs. Remote-managed tunnels share ONE ingress config across
all replicas — never join a second replica to a tunnel unless it can
resolve every origin in that ingress (a rogue omv replica on the social
tunnel 502'd `/api` traffic until removed).
```

**Observability (self-hosted, on omv):** Cloudflare zone tracing exports
per-stage edge spans (`edge_processing`, `origin_fetch` — firewall,
cache, workers_routing, upstream) via a logpush destination →
`otel.cloudless.gr` → a token-auth nginx proxy (`tempo-proxy`) →
**Tempo** on omv (`:4318` OTLP ingest, `:3200` query API, 7-day
retention). Zone-level config covers every `*.cloudless.gr` hostname at
10% baseline sampling; inbound `traceparent` from untrusted clients is
rejected. `social-api` exports FastAPI/httpx spans to the same Tempo
when `OTEL_ENABLED=true` (`OTEL_EXPORTER_OTLP_ENDPOINT=http://
192.168.1.200:4318`). Known CF beta gap: edge and origin halves of a
request are stored under different trace IDs — both browsable, not
joined. Grafana (`grafana.cloudless.gr`) auto-provisions a Tempo
datasource. Since Tempo retention is 7 days, `export_datalake` persists
24h per-hostname aggregates (sampled trace count, p50/p95/max latency,
4xx/5xx counts — TraceQL `status_code` is indexed numerically and lives
on a different span than `url.full`, hence structural `&&` spanset
queries) to `lake/socialauto-edge-metrics/daily.json` — this is what
surfaced the broken Nextcloud SFTP external-storage mount (9% 5xx on
`cloud.cloudless.gr`).

**Prometheus metrics plane (kube-prom on omv k3s, `monitoring` ns):**
`social-api` serves `/metrics` (`app/services/metrics.py`) — HTTP counters/
latency per route plus business gauges rebuilt per scrape: users, teams,
accounts, posts, per-platform queue depth (`socialauto_publish_queue_platform`),
per-platform publish outcomes (`socialauto_posts_published_7d` /
`socialauto_publish_failures_7d` — counted from `post_targets` outcome rows,
not queue rows: a skipped target is queue-`completed` but never published),
`followers_latest` and `token_expires_in_seconds` per connected account.
The scrape reaches the workstation via a headless Service/Endpoints →
`192.168.1.23:9390` (`deploy/monitoring/socialauto/`). EspoCRM (PHP, no
/metrics) gets a direct-MariaDB exporter in its own namespace
(`deploy/monitoring/espocrm/`): read-only `metrics_reader` user, Bearer-token
gated `/metrics`, fresh registry per scrape so failures only export
`espocrm_up 0`. Grafana dashboards are ConfigMaps labelled
`grafana_dashboard=1`, applied by each area's `apply.sh`.

### Browser Sidecars

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Browser Automation Sidecars                      │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ browser-    │  │ facebook-   │  │ linkedin-   │  │ tiktok-    │ │
│  │ novnc       │  │ browser-   │  │ browser-    │  │ browser-   │ │
│  │ :6080 VNC   │  │ sidecar     │  │ sidecar     │  │ sidecar    │ │
│  │ :9223 CDP   │  │ :9226       │  │ :9225       │  │ :9224      │ │
│  │             │  │             │  │             │  │            │ │
│  │ Chromium +  │  │ Facebook    │  │ LinkedIn    │  │ TikTok     │ │
│  │ noVNC login │  │ Playwright  │  │ Playwright  │  │ Playwright  │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │ messenger-  │  │ instagram-  │  │ linkedin-   │                 │
│  │ sidecar     │  │ private-api │  │ mcp-server  │                 │
│  │ :9230       │  │ :8011       │  │ :9227       │                 │
│  │             │  │             │  │             │                 │
│  │ Webhook AI  │  │ aiograpi    │  │ LinkedIn MCP│                 │
│  │ auto-reply  │  │ REST wrapper│  │ server      │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘                 │
└─────────────────────────────────────────────────────────────────────┘
```

### Management & Analytics

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Management & Analytics                           │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ n8n         │  │ n8n-sandbox │  │ metabase    │  │ postgres   │ │
│  │ :5678       │  │             │  │ :3000       │  │             │ │
│  │             │  │             │  │             │  │            │ │
│  │ Workflow    │  │ AI code     │  │ BI dashb.   │  │ Metabase   │ │
│  │ automation  │  │ sandbox     │  │             │  │ DB         │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │ portainer   │  │ env-manager-│  │ env-manager-│                 │
│  │ :9000       │  │ backend     │  │ frontend    │                 │
│  │             │  │ :8081       │  │ :8080       │                 │
│  │ Container   │  │ .env editor │  │ .env UI     │                 │
│  │ management  │  │ backend     │  │             │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘                 │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ airbyte-mcp │  │ api-gateway │  │ flower      │  │ jupyter    │ │
│  │ :9228/9229  │  │ (internal)  │  │ :5555       │  │ :8888      │ │
│  │             │  │             │  │             │  │            │ │
│  │ Airbyte MCP │  │ nginx       │  │ Celery      │  │ scipy      │ │
│  │ connector   │  │ stable /api │  │ monitor     │  │ notebook   │ │
│  │             │  │ upstream    │  │             │  │ scratchpad │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐                                   │
│  │ social-     │  │ dmr-        │  Host-level (not Compose):        │
│  │ metrics     │  │ watchdog    │  ┌──────────────────────────┐     │
│  │ :9390       │  │             │  │ Docker Model Runner      │     │
│  │             │  │ docker CLI  │  │ :12435                   │     │
│  │ nginx       │  │ restarts    │  │ Text: qwen3:8b           │     │
│  │ /metrics    │  │ DMR on fail │  │ Vision: qwen3-vl         │     │
│  │ exporter    │  │             │  │ Embeddings: qwen3-embed. │     │
│  └─────────────┘  └─────────────┘  └──────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
```

**Jupyter (`social-jupyter`)** — two roles:

- **Ad-hoc analytics** against `social-postgres` via the injected
  `DATABASE_URL` (psycopg2). Bound to `127.0.0.1:8888` with `JUPYTER_TOKEN`
  auth — reach it over SSH tunnel or Tailscale only; it is remote code
  execution and must never be exposed publicly. `jupyter/start-notebook.d/`
  installs DB drivers at container start; notebooks live in `./notebooks/`
  (bind-mounted). The stack `.env` is intentionally **not** mounted —
  inject specific env vars in compose instead.
- **Notebook-generated reports** — `notebooks/reports/*.ipynb` are the
  report templates. Celery beat triggers
  `notebook_reports.run_notebook_report`, which executes the notebook via
  **papermill inside `social-worker-default`** (the `./notebooks` dir is
  mounted at `/notebooks` in workers too). The kernel inherits the worker
  environment — `DATABASE_URL`, AI/Slack secrets, and `import app.*` all
  work — so notebooks reuse the real service layer instead of duplicating
  logic. `notebooks/_lib.py` is the shared helpers pack (`db()` → pandas
  DataFrame, `slack()` → ops channel via env-injected bot token, `espo()`,
  `dmr()`, `sql()` magic) — new analysis notebooks start from it.
  `scripts/run-notebook.sh` executes any repo notebook inside the jupyter
  container via papermill (host-cron / manual path, output →
  `notebooks/output/`). The notebook writes `report.html`/`report.txt` + chart PNGs +
  a `*.manifest.json` into `notebooks/output/` (visible in Jupyter at
  `work/output/`); the task then emails each manifest report with charts
  as `cid:` inline images (renders in Gmail, unlike base64 data-URIs).
  On notebook failure the task falls back to the code-path report
  (`digest.send_daily_strategy_report`) so a brief is never silently
  skipped. The daily strategy brief (10:30 EEST) and the ops-health
  report (10:45 EEST — queue state, failed targets, follower-series
  anomalies, sync freshness, content scoreboard; `fallback_to_code=False`
  so a failure never substitutes an unrelated brief) are generated this
  way. Two workers complement this: `tasks/notebooks.py`
  (`weekly-analytics-digest`, Mondays 08:00 — publish/agency/audit KPIs →
  Slack, direct queries rather than papermill) and `tasks/human_gate.py`
  (`human-gate-watch`, every 30 min — sidecar checkpoint polling for
  LinkedIn 2FA / Meta restriction, Slack nudges deduped 6h per gate in
  Redis). Both registered in `workforce_audit.py`.

**Growth initiatives & ad-campaign analytics** — off-platform growth
pushes (e.g. LinkedIn's monthly Page invitation credits) are recorded as
`analytics_events` rows (`post_id=NULL` → exported to the datalake as
account-events). `GET /analytics/initiatives` rolls up sent/accepted/
pending/declined + follower delta from `follower_snapshots` + LinkedIn
credit math (accepted invites refund their credit); `POST
/analytics/initiative-events` logs a batch (auto-resolves the org page
account). `GET /analytics/ad-campaigns` serves the LinkedIn Ads dashboard
with per-day max aggregation so a zeroed scrape can't erase cumulative
spend. Both power cards on the `/analytics` page and sections of the
daily brief.

## Data Flow Architecture

### Content Publishing Flow

```
User creates post         Frontend             Backend              Worker
     │                       │                   │                    │
     │  1. Write caption      │                   │                    │
     │──────────────────────▶│                   │                    │
     │                       │  2. POST /content │                    │
     │                       │──────────────────▶│                    │
     │                       │                   │  3. Save to DB     │
     │                       │                   │  4. AI quality     │
     │                       │                   │     (spell, SEO,   │
     │                       │                   │      NLP, brand)   │
     │                       │                   │  5. Schedule       │
     │                       │  6. Return post   │                    │
     │                       │◀──────────────────│                    │
     │                       │                   │                    │
     │                       │                   │  7. Beat triggers  │
     │                       │                   │     at scheduled   │
     │                       │                   │     time           │
     │                       │                   │───────────────────▶│
     │                       │                   │                    │ 8. Quality gate:
     │                       │                   │                    │    spellcheck→NLP→
     │                       │                   │                    │    SEO≥80 else skip
     │                       │                   │                    │ 9. Publish
     │                       │                   │                    │    to platform
     │                       │                   │                    │    (OAuth API)
     │                       │                   │ 10. Update status  │
     │                       │                   │◀───────────────────│
     │                       │                   │ 11. Sync analytics │
     │                       │                   │    (next cycle)    │
```

### AI Content Generation Flow

```
User requests AI           Frontend             Backend              AI Provider
     │                       │                   │                    │
     │  1. Topic + platform   │                   │                    │
     │──────────────────────▶│                   │                    │
     │                       │  2. POST /ai/     │                    │
     │                       │     generate      │                    │
     │                       │──────────────────▶│                    │
     │                       │                   │  3. Load brand     │
     │                       │                   │     voice/pillars  │
     │                       │                   │  4. Build prompt    │
     │                       │                   │  5. AI generate    │
     │                       │                   │───────────────────▶│
     │                       │                   │                    │ 6. CF Workers AI
     │                       │                   │                    │    (free, primary)
     │                       │                   │                    │    OR DMR (local)
     │                       │                   │  7. Response       │
     │                       │                   │◀───────────────────│
     │                       │                   │  8. Quality check  │
     │                       │                   │     (spell, NLP,   │
     │                       │                   │      SEO, brand)   │
     │                       │  9. Return content│                    │
     │                       │◀──────────────────│                    │
     │  10. Review + edit    │                   │                    │
     │◀──────────────────────│                   │                    │
```

### Personal Messenger Flow (E2EE + Regular)

```
User opens /messenger      Frontend             Backend              Browser Bridge
     │                       │                   │                    │
     │  1. Select account    │                   │                    │
     │──────────────────────▶│                   │                    │
     │                       │  2. GET /messenger │                    │
     │                       │     /personal/     │                    │
     │                       │     conversations  │                    │
     │                       │──────────────────▶│                    │
     │                       │                   │  3. ensure_session │
     │                       │                   │     (extract       │
     │                       │                   │      cookies)      │
     │                       │                   │───────────────────▶│
     │                       │                   │                    │ 4. Check FB login
     │                       │                   │  5. Navigate to   │
     │                       │                   │     about:blank    │
     │                       │                   │     then /messages/│
     │                       │                   │───────────────────▶│
     │                       │                   │                    │ 6. Extract convos
     │                       │                   │                    │    (E2EE + regular)
     │                       │                   │  7. Return convos  │
     │                       │                   │◀───────────────────│
     │                       │  8. Return list   │                    │
     │                       │◀──────────────────│                    │
     │  9. Display convos    │                   │                    │
     │     with E2EE badges  │                   │                    │
     │                       │                   │                    │
     │ 10. Click thread      │                   │                    │
     │──────────────────────▶│                   │                    │
     │                       │ 11. GET messages  │                    │
     │                       │     ?is_e2ee=true │                    │
     │                       │──────────────────▶│                    │
     │                       │                   │ 12. Navigate to   │
     │                       │                   │     about:blank    │
     │                       │                   │     then thread    │
     │                       │                   │───────────────────▶│
     │                       │                   │                    │ 13. Extract msgs
     │                       │                   │ 14. Filter noise   │
     │                       │                   │     (timestamps,   │
     │                       │                   │      UI artifacts) │
     │                       │ 15. Return msgs   │                    │
     │                       │◀──────────────────│                    │
     │ 16. Display msgs      │                   │                    │
     │◀──────────────────────│                   │                    │
```

### Personal Messenger Auto-Reply Flow (Celery Polling)

```
Celery Beat (120s)        Worker               Browser Bridge        AI Provider
     │                       │                     │                    │
     │  1. Trigger poll       │                     │                    │
     │──────────────────────▶│                     │                    │
     │                       │  2. Load accounts   │                    │
     │                       │     with auto-reply │                    │
     │                       │     enabled         │                    │
     │                       │  3. ensure_session │                    │
     │                       │───────────────────▶│                    │
     │                       │                     │  4. Check cookies  │
     │                       │  5. Get convos      │                    │
     │                       │───────────────────▶│                    │
     │                       │                     │  6. Navigate +     │
     │                       │                     │     extract       │
     │                       │  7. For each thread │                    │
     │                       │     (up to 20):     │                    │
     │                       │  8. Read messages   │                    │
     │                       │───────────────────▶│                    │
     │                       │                     │  9. Navigate to   │
     │                       │                     │     about:blank   │
     │                       │                     │     then thread   │
     │                       │                     │ 10. Extract msgs  │
     │                       │ 11. Find last       │                    │
     │                       │     inbound msg     │                    │
     │                       │ 12. Check seen state │                    │
     │                       │ 13. Generate reply  │                    │
     │                       │─────────────────────────────────────────▶│
     │                       │                     │                    │ 14. CF Workers AI
     │                       │                     │                    │     (primary)
     │                       │                     │                    │     OR DMR (local)
     │                       │                     │                    │     OR static text
     │                       │ 15. Send reply      │                    │
     │                       │───────────────────▶│                    │
     │                       │                     │ 16. Navigate +    │
     │                       │                     │     type + Enter  │
     │                       │ 17. Mark as seen    │                    │
     │                       │ 18. Persist state    │                    │
     │                       │     (flag_modified)  │                    │
```

### Bot Builder Flow (Create + Activate)

```
User creates bot           Frontend             Backend              Services
     │                       │                   │                    │
     │  1. Select account    │                   │                    │
     │  2. Choose personality │                   │                    │
     │  3. Set language/hours│                   │                    │
     │──────────────────────▶│                   │                    │
     │                       │  4. POST /bot/    │                    │
     │                       │     create        │                    │
     │                       │──────────────────▶│                    │
     │                       │                   │  5. Build prompt   │
     │                       │                   │     from preset   │
     │                       │                   │  6. If Page:       │
     │                       │                   │     subscribe +   │
     │                       │                   │     setup profile  │
     │                       │                   │───────────────────▶│ Messenger API
     │                       │                   │  7. If personal:  │
     │                       │                   │     set auto-reply│
     │                       │                   │     config        │
     │                       │                   │  8. Index brand   │
     │                       │                   │     knowledge     │
     │                       │                   │───────────────────▶│ ChromaDB
     │                       │                   │  9. Store bot     │
     │                       │                   │     config in    │
     │                       │                   │     meta_data     │
     │                       │ 10. Return config │                    │
     │                       │◀──────────────────│                    │
     │ 11. Show bot status   │                   │                    │
     │◀──────────────────────│                   │                    │
     │                       │                   │                    │
     │ 12. Activate bot      │                   │                    │
     │──────────────────────▶│  13. POST /bot/  │                    │
     │                       │     activate      │                    │
     │                       │──────────────────▶│                    │
     │                       │                   │  14. Set enabled  │
     │                       │                   │      = true       │
     │                       │                   │  15. Sync auto-   │
     │                       │                   │      reply config │
     │                       │ 16. Return status │                    │
     │                       │◀──────────────────│                    │
     │ 17. Bot is live       │                   │                    │
     │◀──────────────────────│                   │                    │
```

### Bot Reply Decision Flow (per inbound message)

```
Inbound message           Polling/             Chatbot Service       AI Provider
     │                    Webhook                │                    │
     │───────────────────▶│                     │                    │
     │                    │  1. Check bot config │                    │
     │                    │     (enabled?)       │                    │
     │                    │  2. Check business  │                    │
     │                    │     hours (UTC)     │                    │
     │                    │  3. Check cooldown  │                    │
     │                    │     (Redis, 5 min)  │                    │
     │                    │  4. Check paused   │                    │
     │                    │     threads list   │                    │
     │                    │  5. Check seen     │                    │
     │                    │     state          │                    │
     │                    │  6. If all pass:   │                    │
     │                    │     detect_intent()│                    │
     │                    │───────────────────▶│                    │
     │                    │                     │  7. DMR classify  │
     │                    │                     │───────────────────▶│ Qwen3 8B
     │                    │                     │  8. Retrieve RAG  │
     │                    │                     │     brand context  │
     │                    │                     │  9. Get memory    │
     │                    │                     │     (last 10 msgs)│
     │                    │                     │ 10. Build enhanced│
     │                    │                     │     prompt        │
     │                    │                     │ 11. Generate reply│
     │                    │                     │───────────────────▶│ DMR (primary)
     │                    │                     │                    │  CF Workers AI
     │                    │                     │                    │  (fallback)
     │                    │                     │ 12. Return reply  │
     │                    │◀───────────────────│                    │
     │                    │ 13. Send reply      │                    │
     │                    │     (Graph API or  │                    │
     │                    │      browser bridge)│                    │
     │                    │ 14. Store in memory│                    │
     │                    │ 15. Set cooldown   │                    │
     │                    │ 16. Mark as seen   │                    │
```

### Bot Builder Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Messenger Bot Builder                             │
│                                                                     │
│  ┌─────────────┐                                                    │
│  │ Frontend    │  BotBuilder.tsx                                    │
│  │             │  ├── Create form (personality, language, hours)   │
│  │             │  ├── Config editor (prompt, model, temp, cooldown)│
│  │             │  ├── Activate/Deactivate toggle                    │
│  │             │  └── Per-thread pause/resume (human handoff)       │
│  └──────┬──────┘                                                    │
│         │                                                           │
│  ┌──────┴──────┐                                                    │
│  │ Backend API │  /api/v1/messenger/{id}/bot/*                     │
│  │             │  ├── POST /bot/create (personality preset)        │
│  │             │  ├── GET /bot (status + config)                   │
│  │             │  ├── PUT /bot (update config)                     │
│  │             │  ├── POST /bot/activate                           │
│  │             │  ├── POST /bot/deactivate                         │
│  │             │  ├── POST /bot/pause-thread/{tid}                 │
│  │             │  ├── POST /bot/resume-thread/{tid}                │
│  │             │  └── GET /bot/personalities (5 presets)           │
│  └──────┬──────┘                                                    │
│         │                                                           │
│  ┌──────┴──────────────────────────────────────────────────────┐   │
│  │ Bot Config (stored in social_accounts.meta_data.messenger_bot)│   │
│  │ ├── name, enabled, system_prompt, model, fallback_text       │   │
│  │ ├── max_tokens, temperature, cooldown_seconds                │   │
│  │ ├── business_hours_start/end (UTC, optional)                │   │
│  │ ├── paused_threads[] (human handoff list)                    │   │
│  │ ├── reply_to_spam, reply_to_greetings (intent filters)       │   │
│  │ └── quick_replies[] (suggestion buttons)                     │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Personality Presets (5)                                       │   │
│  │ ├── professional_friendly — balanced, brand-aware             │   │
│  │ ├── casual              — fun, approachable, emojis           │   │
│  │ ├── formal              — precise, complete sentences        │   │
│  │ ├── support             — patient, helpful, escalates        │   │
│  │ └── sales               — enthusiastic, qualifying questions │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Language Enforcement                                          │   │
│  │ ├── auto  — match incoming message language (default)         │   │
│  │ ├── en    — force English replies                             │   │
│  │ ├── el    — force Greek replies (Ελληνικά)                    │   │
│  │ └── *     — any ISO code, appended to system prompt           │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Account Type Routing                                          │   │
│  │ ├── Page (business) → Messenger Platform API (Graph API)     │   │
│  │ │   ├── Subscribe Page to webhooks                            │   │
│  │ │   ├── Set Messenger Profile (greeting, Get Started, menu)  │   │
│  │ │   └── Send via Graph API /me/messages                      │   │
│  │ └── Personal (user) → Browser Bridge (CDP + noVNC)           │   │
│  │       ├── Sync personal_messenger_auto_reply config          │   │
│  │       ├── Index brand knowledge (ChromaDB)                   │   │
│  │       └── Send via browser bridge (E2EE + regular)           │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Bot Infrastructure (Dedicated Resources)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Bot Infrastructure                                │
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ social-     │  │ Redis       │  │ ChromaDB    │  │ DMR        │ │
│  │ worker-     │  │ DB 1        │  │             │  │ (host)     │ │
│  │ messenger   │  │             │  │ Collections:│  │            │ │
│  │             │  │ Keys:       │  │ • memory_*  │  │ Models:    │ │
│  │ Queue:      │  │ • cooldown  │  │ • brand_know │  │ • qwen3:8B │ │
│  │ messenger   │  │ • paused    │  │             │  │ • qwen3-emb│ │
│  │             │  │ • config    │  │ Embeddings:  │  │            │ │
│  │ conc=2      │  │             │  │ DMR-first   │  │ Primary    │ │
│  │ recycle=50  │  │ Isolated    │  │ CF fallback │  │ inference  │ │
│  │             │  │ from DB 0   │  │             │  │            │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
│                                                                     │
│  ┌─────────────┐                                                    │
│  │ Flower      │  Celery monitoring dashboard (:5555)              │
│  │             │  Auth-protected, monitors all 4 worker queues     │
│  └─────────────┘                                                    │
└─────────────────────────────────────────────────────────────────────┘
```

### Database Fallback Chain

```
Write Request             DB Router            Cloudflare D1        PostgreSQL
     │                       │                     │                    │
     │  1. Write data        │                     │                    │
     │──────────────────────▶│                     │                    │
     │                       │  2. Try D1 first    │                    │
     │                       │────────────────────▶│                    │
     │                       │                     │  3. Write to D1   │
     │                       │  4. Success         │                    │
     │                       │◀────────────────────│                    │
     │                       │  5. Also write to   │                    │
     │                       │     Postgres       │                    │
     │                       │─────────────────────────────────────────▶│
     │                       │                     │                    │ 6. Write
     │  7. Return success    │                     │                    │
     │◀──────────────────────│                     │                    │
     │                       │                     │                    │
     │                       │  If D1 fails (3x):  │                    │
     │                       │  Circuit opens →   │                    │
     │                       │  Route to Postgres │                    │
     │                       │  Queue for replay  │                    │
     │                       │  Circuit closes   │                    │
     │                       │  after 60s        │                    │
```

### Storage Fallback Chain

```
Media Upload              Storage Service       Cloudflare R2       MinIO        Disk
     │                       │                     │                 │            │
     │  1. Upload file        │                     │                 │            │
     │──────────────────────▶│                     │                 │            │
     │                       │  2. Try R2 first    │                 │            │
     │                       │────────────────────▶│                 │            │
     │                       │                     │  3. Store       │            │
     │                       │  4. Success         │                 │            │
     │                       │◀────────────────────│                 │            │
     │  5. Return URL         │                     │                 │            │
     │◀──────────────────────│                     │                 │            │
     │                       │                     │                 │            │
     │                       │  If R2 fails:      │                 │            │
     │                       │  Try MinIO ────────────────────────▶│            │
     │                       │  If MinIO fails:   │                 │            │
     │                       │  Try disk ─────────────────────────────────────▶│
```

### Inference Fallback Chain

```
AI Request                Inference Router      CF Workers AI       DMR          Fallback
     │                       │                     │                 │            │
     │  1. Generate text     │                     │                 │            │
     │──────────────────────▶│                     │                 │            │
     │                       │  2. Try CF first   │                 │            │
     │                       │────────────────────▶│                 │            │
     │                       │                     │  3. llama-3.1   │            │
     │                       │  4. Response       │                 │            │
     │                       │◀────────────────────│                 │            │
     │  5. Return text       │                     │                 │            │
     │◀──────────────────────│                     │                 │            │
     │                       │                     │                 │            │
     │                       │  If CF fails:      │                 │            │
     │                       │  Try DMR ──────────────────────────▶│            │
     │                       │                     │                 │  qwen3:8b │
     │                       │  If DMR fails:      │                 │            │
     │                       │  Return fallback   │                 │            │
     │                       │  text (static)     │                 │            │
```

## Mail & Notification Infrastructure

### Self-hosted mail (OMV-HA, `192.168.1.130`)

The mail server runs on the user's OMV-HA (OpenMediaVault) box — not in the
Compose stack. It hosts the `cloudless.gr` virtual mailbox domain and provides
the email client mailboxes.

```
Outbound : app/client → Postfix :587 (submission, SASL) → relay smtp.resend.com:587
Inbound  : internet → Cloudflare Email Routing (route*.mx.cloudflare.net)
           → Postfix :25 → virtual_transport lmtp:unix:private/dovecot-lmtp
           → Dovecot → Maildir /var/mail/vhosts/cloudless.gr/<user> (uid/gid 5000)
Client   : IMAP 143 (STARTTLS) / 993 (IMAPS) — cleartext auth refused (TLS required)
```

| Component | Detail |
|-----------|--------|
| MTA | Postfix — `virtual_mailbox_domains = cloudless.gr`, LMTP delivery to Dovecot, outbound relay via Resend |
| MDA/IMAP | Dovecot 2.4 — `protocols = imap lmtp`, passwd-file users in `/etc/dovecot/users` (keyed `user@domain`) |
| Virtual users | `/etc/dovecot/users` → `tbaltzakis@cloudless.gr`, `polar@cloudless.gr`; `first_valid_uid = 5000` |
| Mailboxes | `/var/mail/vhosts/cloudless.gr/{tbaltzakis,polar}` (Maildir) |
| Sieve | Default script `/etc/dovecot/sieve/default/phishing.sieve` (file driver — path must be the script file, not the directory) |
| SSH admin | `ssh tbaltzakis@192.168.1.130` — key-based (`OMV_SSH_*` in `.env`) |

**Key config gotcha**: `conf.d/20-lmtp.conf` must keep
`auth_username_format = %{user}` (full address). Debian's default
`%{user|username|lower}` strips the domain → LMTP resolves `user@cloudless.gr`
to the *system* user (uid 1000) → `first_valid_uid=5000` rejects → 451
deferred queue. Do not re-enable domain stripping while the passwd-file
userdb is keyed by full email.

- `polar@cloudless.gr` — dedicated mailbox for the Polar SocialAuto app
  (credentials in `.env` as `POLAR_MAILBOX_*`).

### Slack notification channels (`socialauto` workspace)

| Channel | Env vars | Purpose |
|---------|----------|---------|
| `#socialauto` | `SLACK_WEBHOOK_URL`, `SLACK_CHANNEL_ID` | Daily digest |
| `#socialauto-alerts` | `SLACK_ALERTS_WEBHOOK_URL`, `SLACK_ALERTS_CHANNEL_ID` | Operational alerts |
| `#socialauto-publishing` | `SLACK_PUBLISHING_WEBHOOK_URL` | Aggregated publish summaries (one message per post: preview + platform links) |
| `#polar-support` | `SLACK_SUPPORT_*`, `SLACK_BILLING_*` (pending webhook) | Polar user support / billing digest |

- `app/services/slack_notifications.py` posts via Incoming Webhook
  (`{"text": ...}`) with a Slack Web API fallback (`chat.postMessage`) when
  `SLACK_BOT_TOKEN`/`SLACK_ACCESS_TOKEN` is set.
- **Channel override**: when a `*_CHANNEL_ID` env var is a channel name
  (`#polar-support`), the webhook payload includes `"channel"` so one app
  webhook serves multiple channels. IDs (`C...`) are never sent as overrides.
- `SLACK_SUPPORT_*` points at `#polar-support` (Polar user support);
  `SLACK_BILLING_*` remains available for the billing digest channel.
- `SLACK_BOT_TOKEN` is currently empty — channel creation/API calls require a
  user-provided `xoxb` token with `channels:manage`.

### Billing providers (MoR)

| Provider | Status | Notes |
|----------|--------|-------|
| **Polar** (`BILLING_PROVIDER=polar`, production) | ✅ **Live & approved** (Sep 17, 2026) | Org `cloudless` (`88b98199-…`); all capabilities on: checkout_payments, subscription_renewals, payouts, refunds. Identity verified (Stripe Identity), Stripe Express payout connected (GR/EUR). Hosted buy links: `buy.polar.sh/polar_cl_*` (pro/business/enterprise). Review discount code `REVIEW100` (100% off, forever). Webhook: `/api/v1/billing/polar-webhook` |
| **Dodo** (code-ready) | ⏸ `MERCHANT_NOT_LIVE` | Products + webhook registered; checkout blocked until dashboard verification (Product Info Form → Persona KYC → bank). To activate: set `BILLING_PROVIDER=dodo` |

### MoR compliance contract tests

`tests/unit/test_mor_compliance.py` encodes the Polar/Dodo policy
requirements as tests: billing route surface (checkout/portal/cancel/webhooks),
pricing-parity between `PLAN_LIMITS` and the advertised product specs, webhook
signature enforcement, the account-deletion endpoint (GDPR/Meta), support
channel settings, Slack channel-override behavior, Polar webhook team
resolution ordering, and gzip middleware. A drift between the site, the code,
and the product catalog fails the suite.

### Verified end-to-end (Sep 17, 2026)

All four tiers exercised live against Polar on a dedicated test team:

- `free` checkout → `400 Invalid tier 'free'` (free is the default plan, not
  purchasable).
- `pro` / `business` / `enterprise` checkouts created hosted sessions priced
  $10 / $50 / $150; each completed with the `REVIEW100` 100%-off code and the
  webhook provisioned the matching `plan_tier` + `subscription_status=active`
  + `polar_subscription_id` + `subscription_period_end` on the correct team.
- Cancellation (`POST /billing/cancel` → `cancel_at_period_end`, or Polar
  revoke) delivers `subscription.canceled`/`revoked` webhooks that return the
  team to `plan_tier=free`, `subscription_status=canceled`.
- Webhook idempotency verified: replaying a delivery ID returns
  `{"status":"duplicate"}`; unsigned/bad-signature requests get `401`.
- Customer portal (`POST /billing/portal`) returns a hosted
  `polar.sh/cloudless/portal` session URL.

**Shared-customer caveat**: Polar merges customers by email, so one Polar
customer can back multiple teams. Webhook team resolution therefore tries
checkout-scoped identifiers first (`metadata.team_id`,
`external_customer_id`) before the customer-level `external_id`, and the
portal API uses `customer_id` (not `external_customer_id`). Polar also blocks
a new subscription checkout while the customer already has an active
subscription (`AlreadyActiveSubscriptionError`) — upgrades/downgrades go
through the customer portal, and `cancel_at_period_end` keeps the sub active
until period end (immediate freeing requires a revoke).

### Discount codes (Polar)

Discount codes can be captured at registration (`POST /auth/register`
accepts `discount_code` → stored uppercased on `teams.polar_discount_code`)
or managed later from the **Discount tab** on `/settings/billing`:

- `GET /billing/discount` — stored code + redemption state + curated
  discount details (type, basis_points/amount, duration, windows, caps)
- `PUT /billing/discount` — validate against `GET /discounts` and persist;
  unrecognized codes are kept but reported `valid: false`
- `DELETE /billing/discount` — clear the stored code
- `POST /billing/checkout` accepts an optional `discount_code` override;
  otherwise the team's stored code is resolved to a `discount_id` and
  pre-applied on the hosted checkout. `get_discount_id_for_code` honours
  `starts_at`/`ends_at` windows and `max_redemptions`, so expired or
  exhausted codes are skipped and the hosted page's manual field still
  works.

## Public Edge & Lead Funnel

`social.cloudless.gr` is **public** — the `socialauto-app` Access app has a
first-precedence `public-ga-bypass` policy (see `scripts/cf_access.py
public-mode`), added when the product opened self-serve signup: the
admin-email gate was blocking `/`, `/register`, and `/login`, which broke
the public funnel behind otherwise-public marketing pages. The app's own
JWT auth is the security boundary; Access still sits in front but bypasses
everyone until `public-mode off` re-gates it (e.g. incident lockdown).

The per-path bypass apps below remain as explicit documentation of which
prefixes third parties must always reach unauthenticated:

| Public path | Purpose |
|---|---|
| `/api/v1/health` | Monitoring |
| `/api/v1/auth/oauth/` | OAuth authorize + callbacks for all platforms |
| `/api/v1/auth/data-deletion` | Meta deauthorize/data-deletion POSTs |
| `/api/v1/messenger/webhook` | Meta Messenger verify + events |
| `/api/v1/whatsapp/webhook` | Meta WhatsApp verify + events |
| `/api/v1/telegram/webhook/` | Telegram webhooks |
| `/api/v1/billing/*` webhooks | Polar/Dodo payment webhooks |
| `/api/v1/media/view` | Public media fetches (TikTok `PULL_FROM_URL`, Meta `image_url`) |

`/api/v1/leads/public` has no dedicated path app — it is reachable
publicly like the rest of the site (the endpoint is designed for
unauthenticated use: 10/min rate limit, honeypot, email-only payload).

**Zero Trust Access topology (verified 2026-10-29):** the `cloudless-gr`
ZT org lives on account `fb7dc7b6…` — the unified dashboard may show a
"finish setup / choose a plan" wall until **Zero Trust Free** ($0) is
activated (done 2026-10-29); the org + 18 per-hostname Access apps are
then fully manageable. Gating is **per-hostname, not wildcard** — a new
tunnel hostname escapes coverage by construction (that's how
`ntfy-ws.cloudless.gr` was opened instantly via tunnel+DNS API). Machine
paths are ungated with explicit **bypass** policies, matching the
`socialauto-web-*` convention: `machine-bypass` (Everyone → bypass) is
attached to the `ntfy` app, so `ntfy.cloudless.gr` serves machine
clients publicly — ntfy's own ACLs remain the auth boundary. Probe any
host's gate state with `cf-access-ops` (`scripts/cf_access_audit.py`).

### Alerting & notifications (ntfy)

```
publishers (watchdogs, git hooks, kuma, ops tasks)
   → ntfy k3s pod on omv (NodePort :30080, auth_default_access: deny-all)
      ├─ tailnet: http://github-omv:30080        (internal publishers)
      └─ public:  https://ntfy.cloudless.gr      (machine-bypass policy)
                  https://ntfy-ws.cloudless.gr   (ungated-by-omission alt)
   → topics: cloudless-alerts, cloudless-ops, nextcloud-ops
   → phone subscription (ntfy app, `phone` user) + server publishers
     (`cloudless-app` Bearer token, env-file only — never committed)
```

- Server-side publisher auth = non-expiring `ntfy token add` Bearer
  tokens; the Nextcloud wedge watchdog on omv posts to
  `127.0.0.1:30080/nextcloud-ops` with a token from
  `/etc/nextcloud-wedge-watchdog.env` (root 600).
- Phone credential + endpoint inventory lives in `/etc/ntfy-ops.env`
  (omv, root 600): `NTFY_PHONE_USER`/`_PASSWORD`, `NTFY_HTTP_URL`
  (`ntfy.cloudless.gr`), `NTFY_WS_URL` (`ntfy-ws.cloudless.gr`),
  `NTFY_TAILNET_URL` (`github-omv:30080`). To re-credential the app:
  ntfy → ⋮ → Settings → Manage users → + → `phone`/`<NTFY_PHONE_PASSWORD>`
  for the public host — or `ntfy-phone-ops` `ntfy_set_server.py
  <url> --topic <t> --user phone` (reads `NTFY_PHONE_PW` env). The app is
  battery/hibernation-exempted (`deviceidle whitelist` +
  `RUN_ANY_IN_BACKGROUND`) — Android used to auto-suspend it between
  sessions, which is why subscriptions kept dying.
- Ops tooling: `ntfy-ops` (server users/ACLs/tokens, repo-wide publisher
  audit) + `ntfy-phone-ops` (ADB-driven phone app reconfig).
- **Telegram alert channels** are the second notify surface — private
  broadcast channels created via the Telethon user session
  (`telegram-channel-ops`); Cloudless_newbot posts via
  `/api/v1/telegram/{acc}/send` (e.g. `micro1 alerts`, chat
  `-1004358324988`). Slack remains the third surface
  (`slack_notifications` TG_CH_* channel map).
`cloudless.gr` still reaches it server-to-server with the
`cloudless-site-bridge` Access **service token** headers
(`CF-Access-Client-Id` / `CF-Access-Client-Secret`); those pass through
the bypass untouched. The token value lives in D1
`app_config` (`SOCIALAUTO_SERVICE_TOKEN`) on the cloudless.gr side —
never in this repo.

### Playbook lead flow

```
cloudless.gr playbook form
    → src/lib/socialauto-public-leads.ts (Next.js server action)
    → POST https://social.cloudless.gr/api/v1/leads/public
        (+ CF Access service-token headers)
    → leads.py::create_lead_public  (rate-limited 10/min, honeypot, consent)
    → Lead row (source=website, team=CLOUDLESS_WEB_ANALYTICS_TEAM_ID)
    → playbook_email.deliver_playbook  (atomic Redis claim → one email/address)
    → SMTP → OMV-HA postfix → Resend relay → lead's inbox
        (links PLAYBOOK_URL on cloudless.gr)
    → _post_cloudless_leads_webhook (fire-and-forget)
        → cloudless.gr /api/webhooks/socialauto-leads → EspoCRM Lead
          (source "Web Site"); returned espocrm_lead_id written back to
          Lead.meta_data
```

`playbook_delivery` in the response tells the form what happened:
`email` / `already_sent` / `download` / `none`.

### Playbook PDF pipeline

- Source: `docs/playbooks/cloud-migration-playbook.md`
- Builder: `scripts/build_playbook_pdf.py` (ReportLab — headings, tables,
  checklists, `>` callout boxes, clickable footer CTA)
- Output: `social-automation/frontend/public/playbooks/cloud-migration-playbook.pdf`
- Mirrored to `cloudless.gr/public/playbooks/` so `PLAYBOOK_URL`
  (`https://cloudless.gr/playbooks/cloud-migration-playbook.pdf`) serves
  it from the marketing site, not behind Access.

## Security Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Security Layers                                   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Authentication                                               │   │
│  │  • JWT (access + refresh tokens, typed claims)               │   │
│  │  • Refresh-token rotation — jti burned on use, 60s grace    │   │
│  │    replay returns the same pair (Redis)                      │   │
│  │  • OAuth2 form login (+ TOTP otp field when 2FA enabled)     │   │
│  │  • 2FA (TOTP — enforced at login, not just stored)           │   │
│  │  • Per-account login lockout: 10 failures → 15 min (Redis,  │   │
│  │    fail-open) + per-IP slowapi limits                        │   │
│  │  • Team invite via JWT                                       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Authorization                                                │   │
│  │  • Team-scoped data (team_id on all tables)                 │   │
│  │  • Single team resolver (get_user_team/TeamId — owned first, │   │
│  │    then owner-role, tier, oldest; no ad-hoc .first() joins)  │   │
│  │  • Role hierarchy: OWNER > ADMIN > EDITOR > VIEWER          │   │
│  │  • Admin bypass (SOCIAL_ADMIN_EMAIL)                        │   │
│  │  • Quota enforcement (free/pro/business/enterprise)         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Secrets                                                      │   │
│  │  • Cloudflare-first secret store (KV → Postgres failover)   │   │
│  │  • Encrypted OAuth tokens (ENCRYPTION_KEY)                  │   │
│  │  • .env never committed                                     │   │
│  │  • Webhook HMAC-SHA256 verification                         │   │
│  │  • httpx/httpcore loggers capped at WARNING (core/config.py) │   │
│  │    — INFO request logging used to write OAuth access_token   │   │
│  │    query params (e.g. Instagram Graph) into container logs   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Network                                                      │   │
│  │  • Cloudflare Tunnel (social.cloudless.gr)                    │   │
│  │  • Cloudflare Access SSO gate — admin paths; per-path Bypass  │   │
│  │    apps expose only callbacks/webhooks/health/media-view      │   │
│  │  • Service-token auth for cloudless.gr → /api/v1/leads/public │   │
│  │  • WARP SOCKS5 proxy (free, non-datacenter IP)              │   │
│  │  • Internal Docker network (backend, db, gpu)               │   │
│  │  • All published ports bound to 127.0.0.1 (0 LAN exposure)  │   │
│  │  • No direct DB exposure to internet                        │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Environment Variables

### Core

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis broker/cache connection (DB 0) |
| `MESSENGER_REDIS_URL` | Redis DB 1 for bot state (cooldowns, pause, config) |
| `ENCRYPTION_KEY` | Field-level encryption key |
| `SOCIAL_ADMIN_EMAIL` | Admin user email |
| `SOCIAL_ADMIN_PASSWORD` | Admin user password |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT access token TTL |
| `REFRESH_TOKEN_EXPIRE_DAYS` | JWT refresh token TTL |

### Platform OAuth

| Variable | Platforms |
|----------|-----------|
| `FACEBOOK_CLIENT_ID/SECRET` | Facebook + Instagram + Threads |
| `INSTAGRAM_CLIENT_ID/SECRET` | Instagram |
| `LINKEDIN_CLIENT_ID/SECRET` | LinkedIn |
| `LINKEDIN_EXTRA_SCOPES` | Extra OAuth scopes appended to the canonical list (currently `r_ads r_ads_reporting`). Never add `r_member_postAnalytics` here — it lives on the dedicated analytics app |
| `LINKEDIN_EMAIL/PASSWORD` | LinkedIn sidecar credential login (compose → linkedin-browser-sidecar; used by session healer for native re-login + app-push 2FA) |
| `TWITTER_CLIENT_ID/SECRET` | Twitter/X |
| `X_WEB_FALLBACK_ENABLED`, `X_WEB_AUTH_TOKEN`, `X_WEB_CT0` (+ optional `X_WEB_COOKIES_JSON`, limits) | Twitter/X free web fallback |
| `TIKTOK_CLIENT_KEY/SECRET` | TikTok |
| `THREADS_CLIENT_ID/SECRET` | Threads |

### Cloudflare

| Variable | Purpose |
|----------|---------|
| `CLOUDFLARE_API_TOKEN` | Workers AI, D1, KV |
| `CLOUDFLARE_ACCOUNT_ID` | Account ID |
| `CLOUDFLARE_AI_API_TOKEN` | AI-specific token |
| `D1_SOCIAL_AUTOMATION_ID` | D1 database ID |
| `KV_CACHE_NAMESPACE` | KV namespace |
| `KV_QUEUE_NAMESPACE` | KV queue namespace |
| `VECTORIZE_INDEX_NAME` | Vectorize index |
| `R2_ACCESS_KEY_ID/SECRET` | R2 storage |
| `R2_BUCKET_NAME` | R2 bucket |
| `SOCIAL_TUNNEL_TOKEN` | Cloudflare Tunnel |

### Lead funnel / playbook

| Variable | Purpose |
|----------|---------|
| `CLOUDLESS_WEB_ANALYTICS_TEAM_ID` | Team that owns website lead rows |
| `PLAYBOOK_EMAIL_ENABLED` | Send playbook delivery email (default on) |
| `PLAYBOOK_URL` | Public playbook link (default `cloudless.gr/playbooks/cloud-migration-playbook.pdf`) |
| `SMTP_*` | Playbook/transactional send path (OMV-HA postfix → Resend) |

### AI Providers

| Variable | Provider | In Auto Chain? |
|----------|----------|----------------|
| `CLOUDFLARE_API_TOKEN` | Cloudflare Workers AI | ✓ Primary |
| `DMR_URL` | Docker Model Runner | ✓ Fallback |
| `GROQ_API_KEY` | Groq | Manual only |
| `GEMINI_API_KEY` | Google Gemini | Manual only |
| `OPENROUTER_API_KEY` | OpenRouter | Manual only |
| `SAMBANOVA_API_KEY` | SambaNova | Manual only |
| `MISTRAL_API_KEY` | Mistral | Manual only |
| `COHERE_API_KEY` | Cohere | Manual only |
| `HUGGINGFACE_API_KEY` | HuggingFace | Manual only |
| `TOGETHER_API_KEY` | Together AI | Manual only |
| `PIXAZO_API_KEY` | Pixazo | Manual only |

### Infrastructure

| Variable | Purpose |
|----------|---------|
| `N8N_API_URL/KEY` | n8n workflow automation |
| `COMFYUI_URL` | ComfyUI GPU image gen |
| `DMR_URL` | Docker Model Runner |
| `LOCAL_DIFFUSERS_URL` | Local SD 1.5 |
| `CHROMA_URL` | ChromaDB vector store |
| `LANGUAGETOOL_URL` | Spellcheck server |
| `MINIO_*` | MinIO S3 storage |
| `BROWSER_BRIDGE_URL` | noVNC browser bridge |
| `INSTAGRAM_PRIVATE_API_URL` | aiograpi sidecar |
| `MESSENGER_SIDECAR_URL` | Messenger webhook sidecar |
| `SLACK_WEBHOOK_URL` | Slack notifications |
| `SLACK_SUPPORT_WEBHOOK_URL/CHANNEL_ID` | Support channel (#polar-support) |
| `SLACK_BILLING_WEBHOOK_URL/CHANNEL_ID` | Billing digest channel |
| `SMTP_*` | Transactional email |
| `POLAR_MAILBOX_*` | polar@cloudless.gr mailbox on OMV-HA |
| `OMV_SSH_*` | OMV-HA mail server admin (key-based SSH) |
| `JUPYTER_TOKEN` | Jupyter notebook login (localhost-only, never expose publicly) |

## Quota Tiers

| Tier | Posts | AI Calls | Accounts | Price |
|------|-------|----------|----------|-------|
| Free | 50 | 100 | 3 | $0 |
| Pro | 500 | 5,000 | 15 | $10/mo |
| Business | 2,000 | 20,000 | 50 | $50/mo |
| Enterprise | ∞ | ∞ | ∞ | $150/mo |

Admin team is auto-set to Enterprise with unlimited everything.

## Networks

| Network | Purpose | Services |
|---------|---------|----------|
| `backend` | Main internal | API, workers, Redis, Postgres, n8n, jupyter |
| `db` | Database | Postgres instances, jupyter |
| `frontend` | Frontend | social-frontend, env-manager |
| `gpu` | GPU services | ComfyUI, local-diffusers |
| `default` | Tunnel/misc | cloudflared |

## Port Map

Hardware/GPU snapshot for this workstation lives in [`CODEMAP.md` § Workstation (OFFICE / WSL)](CODEMAP.md#workstation-office--wsl) — do not duplicate here.

All published ports are bound to `127.0.0.1` — no service is reachable on the
LAN; public traffic arrives only via the Cloudflare Tunnel.

| Port | Service | Purpose |
|------|---------|---------|
| 1080 | warp-proxy | WARP SOCKS5 |
| 3000 | metabase | BI dashboard |
| 5678 | n8n | Workflow UI |
| 6080 | browser-novnc | noVNC viewer |
| 8000 | comfyui | GPU image gen |
| 8001 | chroma | Vector DB |
| 8010 | languagetool | Spellcheck |
| 8011 | instagram-private-api | aiograpi |
| 8080 | env-manager-frontend | .env UI |
| 8081 | env-manager-backend | .env API |
| 8082 | social-frontend | Next.js |
| 8083 | social-api | FastAPI |
| 9000 | portainer | Container mgmt |
| 5555 | flower | Celery monitor |
| 8888 | jupyter | Analytics notebook (token auth) |
| 9390 | social-metrics | nginx /metrics exporter |
| 9392 | n8n-metrics | nginx /metrics exporter (n8n) |
| 9100/9101 | minio | S3 storage |
| 9223 | browser-novnc | CDP bridge |
| 9224 | tiktok-browser-sidecar | TikTok |
| 9225 | linkedin-browser-sidecar | LinkedIn |
| 9226 | facebook-browser-sidecar | Facebook |
| 9227 | linkedin-mcp-server | LinkedIn MCP |
| 9228/9229 | airbyte-mcp-server | Airbyte MCP |
| 9230 | messenger-sidecar | Messenger |
| 12435 | DMR (host) | Local AI (not 12434) |
| 5433 | social-postgres | App DB |

## Cloudflare platform updates (October 2026)

Cloudflare's "Enterprise for all" pledge made formerly Enterprise-only features
available to free/pay-as-you-go accounts, and raised limits on services this
stack already uses. Relevant changes:

**Available now, $0:**

| Feature | Impact here |
|---------|-------------|
| Billable Usage API + budget alerts (on by default) | Programmatic spend check — backstops the zero-cost rule; wire a usage line into the daily digest |
| Security Insights — weekly scans (Free) + on-demand | Free zone scanning on `cloudless.gr`/`social.cloudless.gr` |
| Resource-level RBAC (now incl. Workers) | Scope deploy/API tokens to individual resources instead of account-wide |
| Resource Tagging (API) | Tag tunnels/Workers/D1/R2 by project for dashboard navigation |
| Custom Dashboards (GA for all) | Personalized CF metric views without Grafana |
| MCP Server Portals (GA) | Optional: expose SocialAuto tools to remote agents without local sidecars |

**Limit increases already in effect:**

| Service | New limit | Was |
|---------|-----------|-----|
| Workers bundle | 64 MiB uncompressed | 10 MB |
| Workers startup | 1 s | 400 ms |
| Workers subrequests | 1,000,000/request | 1,000 |
| Browser Rendering | 200 concurrent browsers, 10 REST req/s, multi-client sessions | 30 browsers, 3 req/s |
| Vectorize | 20M vectors/index, `topK` ≤ 50 | 5M, `topK` ≤ 20 |
| Pages static assets | 100,000 | 20,000 |
| Durable Objects | 15 min alive on outbound connection | evicted after 70–140 s |
| HTTP headers | 128 KB | 32 KB |

Browser Rendering headroom could offload link-preview scraping/page tests from
the local Playwright sidecars (social-platform logged-in automation stays on
sidecars regardless).

**Watch items:**

- **Logpush** is now self-serve on free plans but **pay-as-you-go priced** —
  check the per-GB rate before enabling (violates the zero-cost rule if
  unattended). If cheap, R2 delivery feeds the `etl-n8n-to-r2` pipeline.
- **Organizations** (multi-account grouping) — Enterprise beta, free accounts
  early 2027.
- **New Account button** — free; use it to segment client/experimental work
  from the production account.

Source: <https://blog.cloudflare.com/enterprise-for-all-update/>

## Container Resource Limits

Hard `mem_limit`/`memswap_limit` caps in `docker-compose.yml` protect the VM from
runaway processes (a single leaking container previously wedged the whole box).
Compose values match the live `docker update` caps applied 2026-09-26 — without
them, a compose recreate would silently drop the limits.

| Service | mem_limit | memswap_limit | Rationale |
|---------|-----------|---------------|-----------|
| `local-diffusers` | 4608m | 9216m | Model-load spikes are legitimate; unbounded creep is not |
| `browser-novnc` | 2048m | 4096m | Chromium tabs multiply silently during long sessions |

## Verification Status

All components verified live:

| Component | Status |
|-----------|--------|
| API health | ok |
| Frontend (local) | HTTP 200 |
| Frontend (production) | HTTP 200 |
| Sidecar health | ok |
| Celery workers | 4 nodes (publishing, media, default, messenger) |
| Celery beat | running |
| MCP tools | 27 registered |
| Backend tests | 7000+ passed |
| MCP tests | 10/10 passed |
| Ruff lint | All checks passed |
| TypeScript | 0 errors |
| Compose config | VALID |

## Peripheral Infrastructure (2026-10-25)

- **NC220 camera recorder** — `systemd` service `nc220-record` on `github-omv`. The camera has no working RTSP for this build (`:554` rejects creds); the live stream is HTTP digest-auth multipart at `http://192.168.1.42:8080/stream/video/h264` (curl `--digest`, user `admin`). Writes 10-min `.h264` segments to `NAS_Storage/workspace/camera-recordings/` on sdb1 with 7-day retention. Play in VLC/mpv; remux to mp4 for sharing if needed.
- **Camera alert relay** — postfix dedicated plaintext listener on omv `:2525` (NC220's TLS stack is too old for :587). Sender `camera@cloudless.gr`, recipient `tbaltzakis@cloudless.gr`, scoped to LAN via `mynetworks`. Sending interval raised from 1m to 1h (the camera batches motion events between sends).
- **Slack workspace** — `socialauto-workspace.slack.com` joined on the phone as `themis`; channels `socialauto`, `socialauto-alerts`, `socialauto-publishing`. Magic links for `tbaltzakis@cloudless.gr` land in the omv dovecot mailbox (not Gmail).
- **Personal IG `t_baltzakis`** — funnel-tuned (name/bio/links/pic) but left as **public Creator**; user may revert to private manually (Account type → Switch to personal, then Account privacy → Private). The "Personal" card on that screen is display-only on this build — the switch needs a manual tap path.
- **X/Twitter OAuth** — `TWITTER_CLIENT_ID`/`SECRET` are empty in `.env`; DM auto-reply dead until real creds from console.x.com are pasted in.
- **TikTok business verification** — rejected ("can't be verified"); resubmit with a doc tying the personal AFM cert to the business.
- **Log intelligence pipeline (2026-10-28)** — `promtail` (grafana/promtail:3.0.0) discovers every container via `docker-socket-proxy` (minimal `CONTAINERS=1 INFO=1 POST=0` — never the raw docker.sock), relabels compose service → `service_name`, and ships stdout/stderr to Loki at `LOKI_PUSH_URL` (defaults to the k3s VIP `192.168.1.200:30100`; must resolve to the same Loki the worker's `LOKI_URL` reads). Positions persist on `./promtail-data`. `log_intel.py` queries `{service_name=~".+"} |~ "error|Error|ERROR|failed|exception|warning|WARNING"` and triages via DMR → Slack digest.
- **Loki persistence cutover (2026-10-28)** — `loki_persist.sh` upgrades the omv k3s single-binary Loki from `useTestSchema`+filesystem to a two-entry schemaConfig: filesystem/tsdb/v12 for pre-cutover logs (history stays queryable) + s3/tsdb/v13 from the cutover date (chunks land on the datalake R2 bucket). R2 creds ride a `loki-r2-creds` k8s Secret surfaced via `singleBinary.extraEnv` AWS_* vars — never helm `--set` (values persist in release metadata). Memberlist/ring values included — the single-binary still needs the memberlist service or the ring never forms. Post-upgrade verify: rollout status + `/loki/api/v1/labels`.

## Content-quality layer (2026-10-30)

`app/services/content_sanitizer.py` — the post-text pipeline:

- `sanitize_post_text` — Slack emoji-shortcodes → real emoji + connective
  dedupe; runs inside `publish_to_platform` (every post).
- `slop_scan` + `voice_check` — LLM-filler markers + run-on ceiling; run
  inside `brand_agent` at generation (rejects before queue).
- `content_quality_gate` — 3-gram near-dup vs recent posts + in-batch, fed
  by a 30-post prior-output exclusion set in `brand_agent`.
