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
│   │  (Next.js)   │  │  (FastAPI)   │  │  (27 tools)  │  │  Workflows │ │
│   │  48 pages    │  │  328 routes  │  │              │  │            │ │
│   │  62 comp.    │  │  24 modules  │  │  AI agents   │  │  No-code   │ │
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

### Route Groups (48 pages)

```
app/
├── page.tsx                          # Marketing landing (redirect)
├── layout.tsx                         # Root layout
│
├── (auth)/                            # Authentication (5 pages)
│   ├── login/                         # Sign in
│   ├── register/                      # Sign up
│   ├── forgot-password/               # Request reset
│   ├── reset-password/                # Set new password
│   └── accept-invite/                 # Team invite acceptance
│
├── (public)/                          # Public marketing (4 pages)
│   ├── about/                         # About page
│   ├── features/                      # Feature highlights
│   ├── pricing/                       # Pricing tiers
│   └── api-docs/                      # Swagger UI
│
├── (dashboard)/                       # Main app (38 pages)
│   ├── dashboard/                     # Overview + KPIs
│   ├── accounts/                      # Connected accounts
│   ├── analytics/                     # Performance metrics
│   ├── calendar/                      # Content calendar
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
│   │   └── audit-logs/                # Audit trail
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

### Frontend Services

```
src/services/api.ts
├── authApi          — login, register, refresh, forgot/reset, 2FA, profile
├── accountsApi      — list, connect, disconnect, sync, business accounts
├── contentApi       — posts CRUD, schedule, publish, review, comments
├── mediaApi         — upload, list, delete, generate image
├── mediaEnhanceApi  — resize, upscale, remove-bg, smart-crop, alt text
├── aiApi            — generate content, hashtags, carousel, image, SEO
├── analyticsApi     — overview, platform, trends, export, sync
├── brandApi         — brand CRUD, voice, visual, guidelines, assets, health
├── messengerApi     — Page + personal, setup, send, conversations, auto-reply
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

### API Modules (328 endpoints across 24 route files)

```
app/api/
├── auth.py          (21) — login, register, refresh, 2FA, profile, password
├── accounts.py      (16) — connect, disconnect, sync, business accounts
├── content.py       (24) — posts CRUD, schedule, publish, review, comments
├── media.py         (20) — upload, list, delete, generate, view
├── media_enhance.py (13) — resize, upscale, remove-bg, smart-crop, alt text
├── ai.py            (40) — generate content, hashtags, carousel, image, SEO
├── ai_providers.py  (8)  — provider catalog, config, test, usage
├── analytics.py     (11) — overview, platform, trends, export, sync
├── brand.py         (26) — brand, voice, visual, guidelines, assets, health
├── publishing.py    (8)  — queue, schedule, publish now, recurring
├── messenger.py     (29) — Page + personal, setup, send, auto-reply, webhook, E2EE, bot builder
├── messenger_api.py (14) — Graph API client for Page Messenger
├── profile.py       (29) — profile read/update across platforms
├── linkedin.py      (13) — LinkedIn AI post, improve, hashtags, publish
├── instagram.py     (7)  — Instagram private API, session, profile
├── threads.py       (9)  — Threads post, reply, profile
├── workflows.py     (18) — templates, generate, deploy, executions
├── teams.py         (10) — teams, members, invite, roles, switch
├── secrets.py       (5)  — Cloudflare-first secret store
├── cf_db.py         (6)  — D1/KV/Vectorize health, sync, tables
├── ops.py           (2)  — health, system info
├── audit.py         (1)  — audit logs
├── mcp.py           (5)  — MCP stack status, sessions, screenshots
└── usage.py         (2)  — quota usage, history
```

### Data Models (12 models)

```
app/models/
├── user.py          — User, Team, TeamMember, AuditLog
├── social_account.py — SocialAccount (platform credentials, meta_data)
├── content.py       — Post, PostTarget, PostComment, MediaAsset,
│                      MediaCollection, Pillar, ContentBrief
├── brand.py         — Brand, BrandVoice, BrandVisual, BrandGuidelines,
│                      BrandAsset
├── brand_monitoring.py — BrandMention, CompetitorSnapshot
├── analytics.py     — AnalyticsEvent, PostAnalyticsSnapshot, FollowerSnapshot
├── queue.py         — PublishQueue
├── workflow.py      — PromptTemplate, GeneratedWorkflow, ContentPromptTemplate
├── ai_provider.py   — AIProvider
├── ai_usage.py      — AIUsageLog
├── social_secret.py — SocialSecret
└── email_log.py     — EmailLog
```

### Service Layer (63 services)

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
│   ├── twitter_api.py        — Twitter/X API v2 client
│   ├── twitter_profile.py    — Twitter profile management
│   └── hikerapi_client.py    — HikerAPI Instagram client
│
├── ── Messenger ─────────────────────────────────────────────
│   ├── messenger_api.py      — Page Messenger Graph API
│   ├── messenger_sidecar.py  — Webhook sidecar (AI auto-reply)
│   └── browser_bridge.py     — Personal Messenger (CDP + noVNC)
│       ├── E2EE + regular thread support
│       ├── ensure_session() — auto-recover + cookie extraction
│       └── _navigate_to_thread() — SPA-safe navigation
│
├── ── Browser Automation ────────────────────────────────────
│   ├── browser_profile.py    — Browser session management
│   ├── facebook_sidecar.py    — Facebook browser sidecar
│   └── linkededin_sidecar.py  — LinkedIn browser sidecar
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
├── ── Media ─────────────────────────────────────────────────
│   ├── media_storage.py      — Storage abstraction (R2→MinIO→disk)
│   ├── r2_storage.py         — Cloudflare R2 storage
│   ├── r2_presigned.py       — R2 presigned URLs
│   ├── minio_storage.py      — MinIO S3 storage
│   ├── image_enhance.py      — Image enhancement
│   ├── image_transform.py    — Image transforms
│   ├── infographic_renderer.py — Procedural infographic renderer
│   └── content_renderer.py   — Content rendering
│
├── ── Quality ───────────────────────────────────────────────
│   ├── media_quality.py      — Quality pipeline
│   ├── media_spellcheck.py    — Media spellcheck
│   ├── spellcheck.py          — LanguageTool spellcheck
│   ├── plain_english.py       — NLP plain-English check/fix
│   ├── content_scorer.py      — Content SEO scoring
│   ├── seo.py                 — SEO optimization
│   ├── quality_pipeline.py    — Full quality pipeline
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
│   ├── analytics_sync.py     — Analytics synchronization
│   └── publishing.py         — Publishing service
```

### MCP Server (27 tools)

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
```

### Celery Tasks (11 task modules, 9 beat schedules)

```
app/worker/tasks/
├── publishing.py           — process_publish_queue, check_scheduled, publish_now
├── analytics.py            — sync_all_analytics, sync_team_analytics
├── media.py                — auto_tag_asset
├── media_enhance.py        — batch_enhance
├── token_refresh.py        — refresh_expiring_tokens
├── recurring.py            — process_recurring_posts
├── digest.py               — send_daily_slack_digest
├── instagram_session_check.py — check_instagram_sessions
├── linkedin_session_check.py  — check_linkedin_sessions
├── workflows.py            — execute_workflow, deploy_workflow
└── personal_messenger.py   — poll_personal_messenger (auto-reply, E2EE + regular, 20 convos/poll)

Beat Schedule:
┌──────────────────────────┬────────────────────────────────┬──────────┐
│ Name                     │ Task                           │ Schedule │
├──────────────────────────┼────────────────────────────────┼──────────┤
│ process-publish-queue    │ publishing.process_publish_queue│ 30s      │
│ check-scheduled-posts    │ publishing.check_scheduled_posts│ 60s      │
│ sync-analytics           │ analytics.sync_all_analytics   │ 300s     │
│ process-recurring-posts  │ recurring.process_recurring    │ 300s     │
│ poll-personal-messenger  │ personal_messenger.poll         │ 120s     │
│ refresh-expiring-tokens  │ token_refresh.refresh           │ hourly   │
│ check-instagram-sessions │ instagram_session_check        │ 6h       │
│ check-linkedin-sessions  │ linkedin_session_check          │ 12h      │
│ daily-slack-digest       │ digest.send_daily_slack_digest  │ daily 9am│
└──────────────────────────┴────────────────────────────────┴──────────┘
```

## Platform Support

| Platform | OAuth | Publishing | Analytics | Profile | Messenger | Special |
|----------|-------|-----------|-----------|---------|-----------|---------|
| Facebook | ✓ | ✓ | ✓ | ✓ (Graph) | ✓ Page + Personal (E2EE) | Page sidecar, browser bridge |
| Instagram | ✓ | ✓ | ✓ | ✓ (Graph + private) | — | Private API sidecar |
| LinkedIn | ✓ | ✓ | ✓ | ✓ (API + browser) | — | Company Page, browser sidecar |
| Twitter/X | ✗ | ✓ | ✓ | ✓ (API) | — | API v2 |
| TikTok | ✓ | ✓ | ✓ | ✓ (API + browser) | — | Browser sidecar |
| Threads | ✓ | ✓ | — | ✓ (API) | — | Instagram-based |

## Docker Compose Stack (30 services)

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
│  │ 328 routes  │  │ 48 pages    │  │ Celery      │  │ Celery     │ │
│  │ MCP server  │  │             │  │ publishing  │  │ media      │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └─────┬──────┘ │
│         │                │                │                │        │
│  ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐               │
│  │ social-     │  │ celery-beat │  │ social-     │               │
│  │ worker-     │  │             │  │ postgres    │               │
│  │ default     │  │ Scheduler   │  │ :5433       │               │
│  │             │  │             │  │             │               │
│  │ Celery      │  │ 9 schedules │  │ PostgreSQL  │               │
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
│  │             │  │             │  │             │  │            │ │
│  │ GPU image   │  │ SD 1.5 GPU  │  │ WARP SOCKS5 │  │ Tunnel to  │ │
│  │ workflows   │  │ fallback    │  │ free proxy  │  │ cloudless  │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

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
│  ┌─────────────┐                                                    │
│  │ airbyte-mcp │  Host-level (not Compose):                        │
│  │ :9228/9229  │  ┌──────────────────────────────────────┐         │
│  │             │  │ Docker Model Runner (DMR) :12434     │         │
│  │ Airbyte MCP │  │ Text: qwen3:8b  Vision: qwen3-vl     │         │
│  │ connector   │  │ Embeddings: qwen3-embedding           │         │
│  └─────────────┘  └──────────────────────────────────────┘         │
└─────────────────────────────────────────────────────────────────────┘
```

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
     │                       │                   │                    │ 8. Publish
     │                       │                   │                    │    to platform
     │                       │                   │                    │    (OAuth API)
     │                       │                   │  9. Update status  │
     │                       │                   │◀───────────────────│
     │                       │                   │ 10. Sync analytics │
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

## Security Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Security Layers                                   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Authentication                                               │   │
│  │  • JWT (access + refresh tokens)                            │   │
│  │  • OAuth2 form login (admin)                                 │   │
│  │  • 2FA (TOTP)                                                │   │
│  │  • Team invite via JWT                                       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Authorization                                                │   │
│  │  • Team-scoped data (team_id on all tables)                 │   │
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
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Network                                                      │   │
│  │  • Cloudflare Tunnel (cloudless.gr)                          │   │
│  │  • WARP SOCKS5 proxy (free, non-datacenter IP)              │   │
│  │  • Internal Docker network (backend, db, gpu)               │   │
│  │  • No direct DB exposure to internet                        │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Environment Variables

### Core

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis broker/cache connection |
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
| `TWITTER_CLIENT_ID/SECRET` | Twitter/X |
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
| `SMTP_*` | Transactional email |

## Quota Tiers

| Tier | Posts | AI Calls | Accounts | Price |
|------|-------|----------|----------|-------|
| Free | 10 | 50 | 1 | €0 |
| Pro | 100 | 500 | 5 | €19/mo |
| Business | ∞ | 5,000 | 20 | €49/mo |
| Enterprise | ∞ | ∞ | ∞ | Custom |

Admin team is auto-set to Enterprise with unlimited everything.

## Networks

| Network | Purpose | Services |
|---------|---------|----------|
| `backend` | Main internal | API, workers, Redis, Postgres, n8n |
| `db` | Database | Postgres instances |
| `frontend` | Frontend | social-frontend, env-manager |
| `gpu` | GPU services | ComfyUI, local-diffusers |
| `default` | Tunnel/misc | cloudflared |

## Port Map

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
| 9100/9101 | minio | S3 storage |
| 9223 | browser-novnc | CDP bridge |
| 9224 | tiktok-browser-sidecar | TikTok |
| 9225 | linkedin-browser-sidecar | LinkedIn |
| 9226 | facebook-browser-sidecar | Facebook |
| 9227 | linkedin-mcp-server | LinkedIn MCP |
| 9228/9229 | airbyte-mcp-server | Airbyte MCP |
| 9230 | messenger-sidecar | Messenger |
| 12434 | DMR (host) | Local AI |
| 5433 | social-postgres | App DB |

## Verification Status

All components verified live:

| Component | Status |
|-----------|--------|
| API health | ok |
| Frontend (local) | HTTP 200 |
| Frontend (production) | HTTP 200 |
| Sidecar health | ok |
| Celery workers | 3 nodes |
| Celery beat | running |
| MCP tools | 27 registered |
| Backend tests | 559 passed, 1 skipped |
| MCP tests | 10/10 passed |
| Ruff lint | All checks passed |
| TypeScript | 0 errors |
| Compose config | VALID |
