# Social Media Tools Research

Comprehensive catalog of 60+ free/open-source social media management tools, libraries, and
frameworks from web and GitHub research (September 2026). Each entry includes integration
notes for SocialAuto.

## All-in-One Scheduling Platforms

| Name | GitHub | License | Stars | Integration |
|------|--------|---------|-------|-------------|
| **TryPost** | https://github.com/trypostit/trypost | AGPL-3.0 | ~572 | MCP server, 12 networks, visual calendar. Self-host as sidecar. |
| **BrightBean Studio** | https://github.com/brightbeanxyz/brightbean-studio | AGPL-3.0 | ~1,845 | 10+ platforms, self-hostable dashboard. Fork for frontend. |
| **OpenPost** | https://github.com/rodrgds/openpost | AGPL/MIT | ~38 | Go+SvelteKit, single container, SQLite. Drop-in scheduling backend. |
| **pendpost** | https://github.com/pendpost/pendpost | MIT | ~6 | AI-agent-first with human approval gate, MCP-native. Approval pattern. |
| **SocialFlow** | https://github.com/inbharatai/SocialFlow | — | ~30 | 6-agent autonomous CMO, 12 platforms. Multi-agent pipeline pattern. |
| **Universal AI Studio** | https://github.com/Sadhi-Team-16/Universal-AI-Studio | MIT | ~1 | 13-platform engine registry. BaseEngine pattern. |
| **PostAll** | https://github.com/qingxuantang/postall | MIT | ~12 | AI content generation + RLHF. AI copywriter backend. |
| **Hookpost** | https://github.com/jatinder14/hookpost | AGPL-3.0 | 0 | 30+ networks, MCP + CLI. REST API alternative. |
| **Posthive** | https://github.com/AstaBlackClove/posthive | AGPL-3.0 | ~12 | MCP, OAuth 2.0 + PKCE, 14+ platforms, bulk CSV. |

## Cross-Platform Content Syndication

| Name | GitHub | License | Integration |
|------|--------|---------|-------------|
| **Open-Dispatch** | https://github.com/Matthew-Selvam/Open-Dispatch | MIT | FastAPI dispatcher for 7 platforms. Publishing sidecar. |
| **usp** | https://github.com/adam-arutyunov/usp | — | Markdown → 9 platforms with LLM rewrites. CLI/CI cross-posting. |
| **universal-social-sdk** | https://github.com/Gabo-Tech/universal-social-sdk | MIT | TypeScript SDK for 9 platforms. Future Node worker. |

## Platform-Specific Python SDKs

### LinkedIn
- **linkedin-api-python-client** (official): https://github.com/linkedin-developers/linkedin-api-python-client — Rest.li client, OAuth2/URN.
- **octopus-linkedin**: https://github.com/octoryn/octopus-linkedin — MCP server for governed LinkedIn marketing.

### Twitter/X
- **tweepy**: https://github.com/tweepy/tweepy (MIT, 11K stars) — De-facto Python library for API v2.
- **snscrape**: https://github.com/JustAnotherArchivist/snscrape (GPL-3.0, 5.4K stars) — Scraper for profiles/hashtags.
- **Scweet**: https://github.com/Altimis/Scweet (MIT, 1.5K stars) — Scrape X without API, proxy support.

### Facebook & Instagram
- **python-facebook**: https://github.com/sns-sdks/python-facebook (Apache-2.0, 379 stars) — Graph API wrapper, Threads support in v0.20.1.
- **instagrapi**: https://github.com/subzeroid/instagrapi (MIT, 6.5K stars) — Private Instagram API. Already integrated.
- **aiograpi**: https://github.com/subzeroid/aiograpi (MIT, 410 stars) — Async private Instagram API. Already integrated.
- **Inoue-AI Instagram SDK**: https://github.com/Inoue-AI/Inoue-AI-Instagram-SDK (MIT) — Async Graph API with Pydantic v2.

### Threads
- **pythreads**: https://github.com/marclove/pythreads (MIT, 72 stars) — Clean async wrapper for official Threads API.
- **threads-client**: https://github.com/nicko4o/threads-client (MIT) — Production-grade async-first SDK.
- **meta-threads-sdk**: https://github.com/MetaThreads/meta-threads-sdk (MIT) — Sync/async, OAuth, rate-limit tracking.
- **Inoue-AI Threads SDK**: https://github.com/Inoue-AI/Inoue-AI-Threads-SDK (MIT) — Async Pydantic v2, publishing/insights/replies.

### TikTok
- **Inoue-AI TikTok SDK**: https://github.com/Inoue-AI/Inoue-AI-TikTok-SDK (MIT) — Content Posting API, Display API, Data Portability.
- **tiktok-api-client**: https://github.com/mymi14s/tiktok_api_client (MIT) — OAuth and video/photo publishing helper.
- **python-tiktok**: https://github.com/sns-sdks/python-tiktok (MIT, 26 stars) — TikTok for Business/Research APIs.
- **TikTok-Api**: https://github.com/davidteather/TikTok-Api (MIT, 6.4K stars) — Scraper for public data (not posting).

## AI Content Generation

| Name | GitHub | License | Integration |
|------|--------|---------|-------------|
| **PostAll** | https://github.com/qingxuantang/postall | MIT | AI content + RLHF + scheduling. |
| **AetherPost** | https://github.com/fununnn/aetherpost | MIT | YAML campaigns, profile sync. |
| **Social Vase** | https://github.com/Docwaltt/Social-Vase | — | Brand-aware AI content. |
| **PulseTag** | https://github.com/bradmca/pulse-tag | MIT | Three-tier hashtag strategy (Safe/Rising/Niche). Free OpenRouter LLMs. |
| **contentflow** | https://github.com/teyfikoz/contentflow | — | 8 platforms, 5 brand voices, content scoring, offline templates. |
| **Marketing Orchestrator** | https://github.com/Dakshaarvind/Marketing-Orchestator | — | 4-stage AI pipeline, SEO scoring. |

## Image & Video Generation

| Name | GitHub | License | Integration |
|------|--------|---------|-------------|
| **postcanvas** | https://github.com/ghedo44/postcanvas | — | Pixel-perfect social image generator with platform presets. |
| **Open Carrusel** | https://github.com/Hainrixz/open-carrusel | MIT | Claude-driven Instagram carousel builder. |
| **ogcops** | https://github.com/codercops/ogcops | MIT | OG image generator, 109 templates, 8 platform previews. |
| **OpenReels** | https://github.com/streamoji-sdk/OpenReels | MIT | AI short-form video pipeline (script→TTS→visuals→MP4). |
| **foco** | https://github.com/Chisu-io/foco | Apache-2.0 | AI short-form studio with Revideo rendering. |
| **Remotion** | https://github.com/remotion-dev/remotion | Other | Programmatic video in React (58K stars). |

## Analytics, Monitoring & Listening

| Name | GitHub | License | Integration |
|------|--------|---------|-------------|
| **influence-hub** | https://github.com/reforia/influence-hub | — | Multi-platform analytics + MCP server. |
| **social-brain** | https://github.com/catehstn/social-brain | — | CLI → Claude prompts for analytics reports. |
| **linkedin-report-automation** | https://github.com/Nikkk2312/linkedin-report-automation | — | LinkedIn Marketing → dashboard + PPTX. |
| **Harken** | https://github.com/VladUZH/harken | MIT | Self-hosted listening: HN, Reddit, Mastodon, Bluesky, X, YouTube, RSS. |
| **openmagpie** | https://github.com/obris-dev/openmagpie | — | Social listening with LLM relevance scoring. |
| **snscrape** | https://github.com/JustAnotherArchivist/snscrape | GPL-3.0 | Scrape public data when no API available. |

## Rate Limiting, Retry & Proxy

| Name | GitHub | License | Integration |
|------|--------|---------|-------------|
| **tenacity** | https://github.com/jd/tenacity | Apache-2.0 | Python retry/backoff library. Wrap all publisher calls. |
| **NyaProxy** | https://github.com/nya-foundation/nyaproxy | — | Quota-aware routing, credential pooling, rate limits. |
| **proxyspin** | https://github.com/gproxynet/proxyspin | — | Rotating proxy pool for Scrapy/Playwright/requests. |
| **swiftshadow** | https://github.com/sachin-sankar/swiftshadow | GPL/MIT | Free IP proxy rotator (326 stars). |
| **LitProxy** | https://github.com/OEvortex/LitProxy | MIT | Proxy management with health checks, httpx support. |
| **ProxyRotator** | https://github.com/keyhankamyar/ProxyRotator | — | V2ray VMESS rotation, user-agent rotation, rate-limit delay. |

## Browser Automation & Anti-Detection

| Name | GitHub | License | Notes |
|------|--------|---------|-------|
| **Playwright** | https://github.com/microsoft/playwright | Apache-2.0 | Already integrated. |
| **agentic-stealth-browser** | https://github.com/shanewas/agentic-stealth-browser | — | Human-mimicking for Cloudflare/LinkedIn anti-bot. |
| **invisible_playwright** | https://github.com/feder-cr/invisible_playwright | MIT | Stealth-patched Firefox, passes bot detection. |
| **arcanada-publisher** | https://github.com/Arcanada-one/arcanada-publisher | MIT | Playwright publisher for FB/LI/X/Reddit/VK/Telegram. |
| **ClawSocial** | https://github.com/alex-noel/clawsocial | — | Playwright for IG/X/LI (post/like/comment/DM/follow). |
| **ultrastealth** | https://github.com/anusoft/ultrastealth | — | Maximum-stealth with CDP-leak fixes + MCP. |

## Workflow / General Automation

| Name | GitHub | License | Notes |
|------|--------|---------|-------|
| **n8n** | https://github.com/n8n-io/n8n | Other | Already in stack. |
| **n8n-nodes-social** | https://github.com/botzvn/n8n-nodes-social | MIT | Community nodes for Meta, X, TikTok, Threads. |
| **Activepieces** | https://github.com/activepieces/activepieces | Other | Open-source Zapier, 400+ pieces, MCP. 24K stars. |
| **Huginn** | https://github.com/huginn/huginn | MIT | Self-hosted IFTTT, 49.8K stars. |

## Top Recommendations for SocialAuto

1. **Replace per-platform API clients with typed SDKs**:
   - Threads: `pythreads` or `Inoue-AI Threads SDK`
   - TikTok: `Inoue-AI TikTok SDK` (Content Posting API)
   - Instagram: `Inoue-AI Instagram SDK` or `python-facebook`
   - LinkedIn: `linkedin-api-python-client` (official)
   - X: `tweepy` + `snscrape` for no-API analytics

2. **Add self-hosted scheduling/calendar sidecar**: TryPost or BrightBean Studio

3. **Strengthen AI content + media generation**:
   - `PostAll` for AI copywriting
   - `postcanvas` for branded static images
   - `Open Carrusel` for carousel assets
   - `OpenReels` for short-form video

4. **Add social listening**: `Harken` (MIT, simplest self-hosted listener)

5. **Resilience**: Wrap all publisher calls in `tenacity` with platform-specific retry/backoff

6. **Agent/MCP integration**: Expose SocialAuto via MCP server (see socialauto-mcp-server skill)
