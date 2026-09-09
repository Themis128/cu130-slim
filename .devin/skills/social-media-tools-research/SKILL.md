# Social Media Tools Research

Curated catalog of free/open-source social media management tools, libraries, and frameworks
discovered through web and GitHub research (September 2026). Each entry includes integration
notes for SocialAuto.

## Publishing & Scheduling Platforms

### Hookpost
- **URL**: https://github.com/jatinder14/hookpost
- **License**: AGPL-3.0
- **Stack**: Next.js, Docker Compose, MCP server
- **What it does**: All-in-one open-source social media scheduler with multi-agent AI copilot.
  Publishes to 30+ networks (X, LinkedIn, Instagram, Facebook, Threads, YouTube, TikTok, Reddit,
  Pinterest, Bluesky, Mastodon, Telegram, Discord, Slack).
- **Integration with SocialAuto**: MCP server can be used as an alternative publishing backend.
  REST API compatible with SocialAuto's content API. Can replace n8n for scheduling workflows.
- **Free/Open-source**: Yes (AGPL-3.0)

### pendpost
- **URL**: https://github.com/pendpost/pendpost
- **License**: MIT
- **Stack**: Python, MCP-native
- **What it does**: Agent-first social media planner with human approval gate. AI agents draft
  and schedule posts, but nothing publishes until a human approves. Supports Instagram,
  Facebook, LinkedIn, YouTube, X, Telegram, Discord, Mastodon, Nostr, WordPress, Ghost.
- **Integration with SocialAuto**: The approval gate pattern can be added to SocialAuto's
  publish queue. MCP server can drive SocialAuto's API from AI agents.
- **Free/Open-source**: Yes (MIT)

### Posthive
- **URL**: https://github.com/AstaBlackClove/posthive
- **License**: AGPL-3.0
- **Stack**: Next.js, Redis
- **What it does**: Agentic social media scheduling platform with built-in AI agent support
  via MCP. 14+ platforms, calendar view, bulk CSV scheduling, post templates, first comment
  scheduling, per-platform overrides, Instagram Reels & Stories, YouTube Shorts.
- **Integration with SocialAuto**: OAuth 2.0 + PKCE MCP server pattern. Calendar view UI
  patterns. Bulk scheduling approach.
- **Free/Open-source**: Yes (AGPL-3.0)

### SocialFlow
- **URL**: https://github.com/inbharatai/socialflow
- **License**: Open-source (unspecified)
- **Stack**: FastAPI, Playwright, Ollama
- **What it does**: AI-powered autonomous social media CMO. 6-agent pipeline (Scout, Planner,
  Creator, Reviewer, Publisher, Analyst). 12 platforms including Discord, Reddit, Medium,
  Substack. Brand kit, approval gates, local AI via Ollama.
- **Integration with SocialAuto**: Multi-agent pipeline pattern. Brand kit system. Local AI
  via Ollama/DMR (already in SocialAuto). Playwright-based publishing (already used).
- **Free/Open-source**: Yes

## Python Libraries

### marqetive-lib
- **URL**: https://pypi.org/project/marqetive-lib/
- **License**: Open-source
- **What it does**: Modern Python library for social media platform integrations. Unified
  async API for Twitter/X, LinkedIn, Instagram, TikTok, Threads. Auto token refresh,
  media upload with progress, retry logic with exponential backoff and jitter.
- **Integration with SocialAuto**: Can replace or supplement individual platform API
  clients. The retry/backoff pattern and auto token refresh factory are directly applicable.
  Type-safe with full type hints.
- **Free/Open-source**: Yes

### instagrapi (best practices)
- **URL**: https://github.com/subzeroid/instagrapi
- **License**: MIT
- **What it does**: Instagram private API client. Best practices for rate limiting:
  - One stable proxy/IP per account
  - Match country, locale, device settings, and saved sessions
  - Avoid rotating proxy identity mid-session
  - Use `socks5h://` for proxy hostnames that resolve through the proxy
  - Limit concurrency per account
  - Exponential backoff with jitter
- **Integration with SocialAuto**: Already integrated. Best practices document should be
  followed for WARP proxy assignment and session management.
- **Free/Open-source**: Yes (MIT)

### Agoras
- **URL**: https://github.com/LuisAlejandro/agoras
- **License**: Open-source
- **What it does**: CLI utility for publishing to X, Facebook, Instagram, LinkedIn, Discord,
  YouTube, TikTok, Threads, Telegram, WhatsApp. Modular architecture (5 PyPI packages).
  OAuth callback server for easier authentication.
- **Integration with SocialAuto**: OAuth callback server pattern. Modular platform
  implementations. GitHub Actions integration.
- **Free/Open-source**: Yes

## Content Generation

### PulseTag
- **URL**: https://github.com/bradmca/pulse-tag
- **License**: Open-source
- **Stack**: FastAPI, Next.js, Playwright, OpenRouter
- **What it does**: AI-driven hashtag generator. Analyzes social media posts in real-time
  and generates optimized hashtag strategies. Three-tier strategy: Safe (high-volume),
  Rising (trending mid-volume), Niche (low-competition). Uses free OpenRouter LLMs.
- **Integration with SocialAuto**: Hashtag generation API can be called from SocialAuto's
  AI content pipeline. Three-tier hashtag strategy pattern. Free LLM via OpenRouter.
- **Free/Open-source**: Yes

### contentflow
- **URL**: https://github.com/teyfikoz/contentflow
- **License**: Open-source
- **What it does**: Multi-platform AI marketing content generator. 8 platforms, 5 brand
  voices, 5 languages. Content scoring (readability, engagement, hashtag quality, length
  fit). Content calendar with auto-scheduling. SEO optimizer (meta tags, titles, outlines).
  Works offline with 50+ curated templates or online with HuggingFace AI.
- **Integration with SocialAuto**: Content scoring pattern. Offline template system.
  SEO optimizer. HuggingFace Inference API (free tier) as fallback for Cloudflare Workers AI.
- **Free/Open-source**: Yes

### Marketing Orchestrator
- **URL**: https://github.com/Dakshaarvind/Marketing-Orchestator
- **License**: Open-source
- **What it does**: AI-powered marketing content generation with 4-stage pipeline:
  Analysis -> Competitor Research -> Content Generation -> SEO Optimization. Automatic
  DALL-E 3 image generation. Competitor intelligence via Yelp API. SEO scores (85-90/100).
- **Integration with SocialAuto**: 4-stage pipeline pattern. Competitor analysis. SEO
  scoring (already in SocialAuto).
- **Free/Open-source**: Yes

## Rate Limiting & Proxy Management

### ProxyRotator
- **URL**: https://github.com/keyhankamyar/ProxyRotator
- **License**: Open-source
- **What it does**: Async V2ray (VMESS) proxy rotation library. Auto-update subscriptions,
  test connections, rotate user-agents, handle rate limits. Built on Xray-core. Built-in
  delay with jitter, thread-safe global lock.
- **Integration with SocialAuto**: Can supplement WARP proxy with V2ray rotation. Rate
  limit delay with jitter pattern. User-agent rotation.
- **Free/Open-source**: Yes

### Adaptive Rate Limiting (auto_connector)
- **URL**: https://github.com/ivasik-k7/auto_connector
- **License**: Open-source
- **What it does**: Adaptive throttling with primary + secondary rate-limit handling,
  per-worker pacing, exponential backoff. Honors `Retry-After` headers. Two-layer cache
  (in-memory + TTL disk). Resumable campaigns with JSON state.
- **Integration with SocialAuto**: Adaptive throttling pattern for all platform API calls.
  `Retry-After` header handling. Two-layer cache pattern.
- **Free/Open-source**: Yes

## Key Patterns to Adopt

1. **Human approval gate** (pendpost): Add optional approval step to publish queue before
   posts go live. Posts stay in `pending_approval` state until a human approves.

2. **Three-tier hashtag strategy** (PulseTag): Categorize hashtags as Safe/Rising/Niche
   instead of a flat list. Improves reach across audience types.

3. **Content scoring** (contentflow): Score generated content on readability, engagement,
   hashtag quality, and length fit before publishing.

4. **Adaptive rate limiting** (auto_connector): Honor `Retry-After` headers, use exponential
   backoff with jitter, pace requests per-worker, cache responses.

5. **Stable proxy identity** (instagrapi): One stable proxy/IP per account. Match country,
   locale, device settings. Never rotate proxy mid-session.

6. **MCP server** (Hookpost, pendpost, Posthive): Expose SocialAuto's API through an MCP
   server so AI agents (Claude, ChatGPT, Cursor) can drive content creation and scheduling.

7. **Offline templates** (contentflow): Ship 50+ curated content templates that work
   without AI, as a fallback when all AI providers are unavailable.

8. **Multi-agent pipeline** (SocialFlow): Scout -> Planner -> Creator -> Reviewer ->
   Publisher -> Analyst. Each agent has a specific role and schedule.
