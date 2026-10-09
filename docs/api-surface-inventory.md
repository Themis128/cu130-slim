# API Surface Inventory

<!-- AUTO-GENERATED from openapi.json — regenerate:

  python3 -c "import json,urllib.request; ..." (see tests/integration/test_docs_surfaces.py)

  or: docker compose exec -T social-api python -m pytest tests/integration/test_docs_surfaces.py --generate-inventory

-->

Every route the FastAPI app serves, grouped by prefix. `test_docs_surfaces.py`
asserts every live route appears here — regenerate this file when adding routes.

## `(root)`

- `/health` — GET, HEAD

## `accounts`

- `/api/v1/accounts` — GET
- `/api/v1/accounts/connect` — POST
- `/api/v1/accounts/connect/{platform}` — POST
- `/api/v1/accounts/linkedin/sync-organizations` — POST
- `/api/v1/accounts/{account_id}` — GET, DELETE
- `/api/v1/accounts/{account_id}/page-profile` — GET, PUT
- `/api/v1/accounts/{account_id}/page-profile/assign-manage-task` — POST
- `/api/v1/accounts/{account_id}/page-profile/cover` — POST
- `/api/v1/accounts/{account_id}/page-profile/picture` — POST
- `/api/v1/accounts/{account_id}/refresh` — POST
- `/api/v1/accounts/{account_id}/set-business-account` — POST
- `/api/v1/accounts/{account_id}/sync-business-accounts` — POST
- `/api/v1/accounts/{account_id}/test` — POST
- `/api/v1/accounts/{account_id}/validate` — GET

## `ai`

- `/api/v1/ai/analyze-content` — POST
- `/api/v1/ai/auto-configure` — POST
- `/api/v1/ai/best-time-to-post` — POST
- `/api/v1/ai/dmr/benchmark` — POST
- `/api/v1/ai/dmr/chat` — POST
- `/api/v1/ai/dmr/keep-alive` — POST
- `/api/v1/ai/dmr/requests` — GET
- `/api/v1/ai/dmr/speculative-decoding` — POST
- `/api/v1/ai/dmr/status` — GET
- `/api/v1/ai/dmr/vision` — POST
- `/api/v1/ai/dmr/warmup` — POST
- `/api/v1/ai/drafts` — GET
- `/api/v1/ai/emoji/batch` — POST
- `/api/v1/ai/emoji/generate` — POST
- `/api/v1/ai/emoji/styles` — GET
- `/api/v1/ai/generate-blog-article` — POST
- `/api/v1/ai/generate-carousel` — POST
- `/api/v1/ai/generate-carousel-pipeline` — POST
- `/api/v1/ai/generate-content` — POST
- `/api/v1/ai/generate-hashtags` — POST
- `/api/v1/ai/generate-image` — POST
- `/api/v1/ai/generate-image-flux` — POST
- `/api/v1/ai/generate-image-pipeline` — POST
- `/api/v1/ai/generate-image-prompt` — POST
- `/api/v1/ai/generate-image/{job_id}` — GET
- `/api/v1/ai/generate-workflow` — POST
- `/api/v1/ai/improve-content` — POST
- `/api/v1/ai/nlp-check` — POST
- `/api/v1/ai/post-draft` — POST
- `/api/v1/ai/run-carousel-and-publish` — POST
- `/api/v1/ai/save-draft` — POST
- `/api/v1/ai/save-generation-template` — POST
- `/api/v1/ai/score-content` — POST
- `/api/v1/ai/seed-default-workflows` — POST
- `/api/v1/ai/seo` — POST
- `/api/v1/ai/spellcheck` — POST
- `/api/v1/ai/suggest-hashtags` — POST
- `/api/v1/ai/suggest-hashtags-tiered` — POST
- `/api/v1/ai/transcribe` — POST
- `/api/v1/ai/web-search` — GET
- `/api/v1/ai/workers-ai/batch` — POST
- `/api/v1/ai/workers-ai/batch/retrieve` — POST
- `/api/v1/ai/workflow-config/{content_type}` — GET

## `ai-providers`

- `/api/v1/ai-providers` — GET
- `/api/v1/ai-providers/catalog` — GET
- `/api/v1/ai-providers/usage` — GET
- `/api/v1/ai-providers/{name}` — PUT, DELETE
- `/api/v1/ai-providers/{name}/models` — GET
- `/api/v1/ai-providers/{name}/reset-circuit` — POST
- `/api/v1/ai-providers/{name}/test` — POST

## `analytics`

- `/api/v1/analytics/accounts/{account_id}/insights` — GET
- `/api/v1/analytics/accounts/{account_id}/metrics` — GET
- `/api/v1/analytics/ad-campaigns` — GET
- `/api/v1/analytics/bots/cloudflare-ai` — GET
- `/api/v1/analytics/bots/cloudflare-overview` — GET
- `/api/v1/analytics/bots/summary` — GET
- `/api/v1/analytics/engagement` — GET
- `/api/v1/analytics/followers` — GET
- `/api/v1/analytics/initiative-events` — POST
- `/api/v1/analytics/initiatives` — GET
- `/api/v1/analytics/insights` — GET
- `/api/v1/analytics/overview` — GET
- `/api/v1/analytics/pipeline` — GET
- `/api/v1/analytics/platforms` — GET
- `/api/v1/analytics/posts/{post_id}/metrics` — GET
- `/api/v1/analytics/reports/export` — GET
- `/api/v1/analytics/snapshots` — GET
- `/api/v1/analytics/sync` — POST
- `/api/v1/analytics/tiktok/videos` — GET
- `/api/v1/analytics/top-posts` — GET
- `/api/v1/analytics/web/configs` — GET, POST
- `/api/v1/analytics/web/configs/{config_id}` — PUT, DELETE
- `/api/v1/analytics/web/summary` — GET
- `/api/v1/analytics/web/webhooks/cloudless-analytics` — POST

## `audit`

- `/api/v1/audit/audit-logs` — GET

## `auth`

- `/api/v1/auth/2fa` — DELETE
- `/api/v1/auth/2fa/setup` — POST
- `/api/v1/auth/2fa/verify` — POST
- `/api/v1/auth/account` — DELETE
- `/api/v1/auth/change-password` — POST
- `/api/v1/auth/data-deletion` — POST
- `/api/v1/auth/export-data` — GET
- `/api/v1/auth/forgot-password` — POST
- `/api/v1/auth/linkedin/sync-orgs` — POST
- `/api/v1/auth/login` — POST
- `/api/v1/auth/me` — GET, PATCH
- `/api/v1/auth/notifications/preferences` — GET, PUT
- `/api/v1/auth/oauth/instagram-onboarding/authorize` — GET
- `/api/v1/auth/oauth/instagram2/authorize` — GET
- `/api/v1/auth/oauth/instagram2/callback` — GET
- `/api/v1/auth/oauth/{platform}/authorize` — GET
- `/api/v1/auth/oauth/{platform}/callback` — GET
- `/api/v1/auth/refresh` — POST
- `/api/v1/auth/register` — POST
- `/api/v1/auth/reset-password` — POST
- `/api/v1/auth/switch-team` — POST

## `billing`

- `/api/v1/billing/cancel` — POST
- `/api/v1/billing/checkout` — POST
- `/api/v1/billing/config` — GET
- `/api/v1/billing/discount` — GET, PUT, DELETE
- `/api/v1/billing/dodo-webhook` — POST
- `/api/v1/billing/plans` — GET
- `/api/v1/billing/polar-webhook` — POST
- `/api/v1/billing/portal` — POST
- `/api/v1/billing/subscription` — GET
- `/api/v1/billing/sync` — POST
- `/api/v1/billing/webhook` — POST

## `bluesky`

- `/api/v1/bluesky/connect` — POST
- `/api/v1/bluesky/{account_id}/status` — GET

## `brand`

- `/api/v1/brand` — GET, POST, PUT, DELETE
- `/api/v1/brand/ad-kit` — POST
- `/api/v1/brand/analyze-voice` — POST
- `/api/v1/brand/assets` — GET, POST
- `/api/v1/brand/assets/{asset_id}` — DELETE
- `/api/v1/brand/autopilot/run` — POST
- `/api/v1/brand/competitors` — GET
- `/api/v1/brand/competitors/snapshot` — POST
- `/api/v1/brand/compliance` — POST
- `/api/v1/brand/digital-card` — GET
- `/api/v1/brand/digital-card/{token}` — GET
- `/api/v1/brand/extract` — POST
- `/api/v1/brand/generate-favicon` — POST
- `/api/v1/brand/generate-logo` — POST
- `/api/v1/brand/guidelines` — GET
- `/api/v1/brand/guidelines/compile` — POST
- `/api/v1/brand/guidelines/share/{token}` — GET
- `/api/v1/brand/health` — GET
- `/api/v1/brand/mentions` — GET
- `/api/v1/brand/mentions/collect` — POST
- `/api/v1/brand/trends` — GET
- `/api/v1/brand/visual` — GET, PUT
- `/api/v1/brand/voice` — GET, PUT

## `cf-db`

- `/api/v1/cf-db/health` — GET
- `/api/v1/cf-db/replay` — POST
- `/api/v1/cf-db/status` — GET
- `/api/v1/cf-db/sync` — POST
- `/api/v1/cf-db/sync-tables` — GET
- `/api/v1/cf-db/tables` — GET

## `content`

- `/api/v1/content/briefs` — GET, POST
- `/api/v1/content/briefs/{brief_id}` — DELETE
- `/api/v1/content/link-preview` — GET
- `/api/v1/content/media` — GET
- `/api/v1/content/media/upload` — POST
- `/api/v1/content/media/{media_id}` — DELETE
- `/api/v1/content/pillars` — GET, POST
- `/api/v1/content/pillars/{pillar_id}` — DELETE, PATCH
- `/api/v1/content/posts` — GET, POST
- `/api/v1/content/posts/calendar` — GET
- `/api/v1/content/posts/{post_id}` — GET, DELETE, PATCH
- `/api/v1/content/posts/{post_id}/approve` — POST
- `/api/v1/content/posts/{post_id}/comments` — POST
- `/api/v1/content/posts/{post_id}/cross-post` — POST
- `/api/v1/content/posts/{post_id}/duplicate` — POST
- `/api/v1/content/posts/{post_id}/publish-now` — POST
- `/api/v1/content/posts/{post_id}/reject` — POST
- `/api/v1/content/posts/{post_id}/schedule` — POST
- `/api/v1/content/posts/{post_id}/submit-review` — POST

## `digital-cards`

- `/api/v1/digital-cards` — GET, POST
- `/api/v1/digital-cards/from-brand` — POST
- `/api/v1/digital-cards/public/{token}` — GET
- `/api/v1/digital-cards/public/{token}/track` — POST
- `/api/v1/digital-cards/{card_id}` — GET, DELETE, PATCH
- `/api/v1/digital-cards/{card_id}/send` — POST
- `/api/v1/digital-cards/{card_id}/vcard` — GET

## `inbox`

- `/api/v1/inbox/inbox` — GET

## `instagram`

- `/api/v1/instagram/app-review/guide` — GET
- `/api/v1/instagram/app-review/status` — GET
- `/api/v1/instagram/comments/{comment_id}` — DELETE
- `/api/v1/instagram/comments/{comment_id}/hide` — POST
- `/api/v1/instagram/comments/{comment_id}/reply` — POST
- `/api/v1/instagram/comments/{media_id}` — GET
- `/api/v1/instagram/mentions` — GET
- `/api/v1/instagram/quota` — GET
- `/api/v1/instagram/stories` — POST

## `leads`

- `/api/v1/leads` — GET, POST
- `/api/v1/leads/public` — POST

## `linkedin`

- `/api/v1/linkedin/accounts/{account_id}/followers` — GET
- `/api/v1/linkedin/accounts/{account_id}/validate` — GET
- `/api/v1/linkedin/ads-control` — POST
- `/api/v1/linkedin/analytics/organization` — GET
- `/api/v1/linkedin/analytics/post/{post_urn}` — GET
- `/api/v1/linkedin/best-time` — GET
- `/api/v1/linkedin/comment` — POST
- `/api/v1/linkedin/company-page-url` — GET
- `/api/v1/linkedin/generate-article` — POST
- `/api/v1/linkedin/generate-comment` — POST
- `/api/v1/linkedin/generate-hashtags` — POST
- `/api/v1/linkedin/generate-post` — POST
- `/api/v1/linkedin/improve-post` — POST
- `/api/v1/linkedin/publish` — POST
- `/api/v1/linkedin/{account_id}/dm/auto-reply` — GET, PUT
- `/api/v1/linkedin/{account_id}/dm/conversations` — GET
- `/api/v1/linkedin/{account_id}/dm/threads/{thread_id}` — GET
- `/api/v1/linkedin/{account_id}/dm/threads/{thread_id}/send` — POST

## `mcp`

- `/api/v1/mcp/linkedin/profile` — POST
- `/api/v1/mcp/linkedin/search-people` — POST
- `/api/v1/mcp/stack` — GET
- `/api/v1/mcp/stack/{service_id}/screenshot` — GET
- `/api/v1/mcp/stack/{service_id}/session` — POST

## `media`

- `/api/v1/media/assets` — GET
- `/api/v1/media/assets/bulk-delete` — POST
- `/api/v1/media/assets/{asset_id}` — GET, DELETE, PATCH
- `/api/v1/media/assets/{asset_id}/similar` — GET
- `/api/v1/media/assets/{asset_id}/tag` — POST
- `/api/v1/media/collections` — GET, POST
- `/api/v1/media/collections/{collection_id}` — GET, DELETE, PATCH
- `/api/v1/media/collections/{collection_id}/assets` — POST
- `/api/v1/media/collections/{collection_id}/assets/{asset_id}` — DELETE
- `/api/v1/media/enhance/assets/{asset_id}/alt-text` — POST
- `/api/v1/media/enhance/assets/{asset_id}/compress` — POST
- `/api/v1/media/enhance/assets/{asset_id}/convert` — POST
- `/api/v1/media/enhance/assets/{asset_id}/crop` — POST
- `/api/v1/media/enhance/assets/{asset_id}/info` — GET
- `/api/v1/media/enhance/assets/{asset_id}/quality` — GET
- `/api/v1/media/enhance/assets/{asset_id}/remove-background` — POST
- `/api/v1/media/enhance/assets/{asset_id}/resize` — POST
- `/api/v1/media/enhance/assets/{asset_id}/smart-crop` — POST
- `/api/v1/media/enhance/assets/{asset_id}/upscale` — POST
- `/api/v1/media/enhance/assets/{asset_id}/watermark` — POST
- `/api/v1/media/enhance/batch` — POST
- `/api/v1/media/enhance/presets` — GET
- `/api/v1/media/generate-image` — POST
- `/api/v1/media/generate-video` — POST
- `/api/v1/media/generate-video/{task_id}` — GET
- `/api/v1/media/search` — GET
- `/api/v1/media/upload` — POST
- `/api/v1/media/upload/complete` — POST
- `/api/v1/media/upload/prepare` — POST
- `/api/v1/media/view` — GET

## `messenger`

- `/api/v1/messenger/sidecar/status` — GET
- `/api/v1/messenger/webhook` — GET, POST
- `/api/v1/messenger/{account_id}/auto-reply` — GET, PUT
- `/api/v1/messenger/{account_id}/bot` — GET, PUT
- `/api/v1/messenger/{account_id}/bot/activate` — POST
- `/api/v1/messenger/{account_id}/bot/create` — POST
- `/api/v1/messenger/{account_id}/bot/deactivate` — POST
- `/api/v1/messenger/{account_id}/bot/pause-thread/{thread_id}` — POST
- `/api/v1/messenger/{account_id}/bot/personalities` — GET
- `/api/v1/messenger/{account_id}/bot/resume-thread/{thread_id}` — POST
- `/api/v1/messenger/{account_id}/conversations` — GET
- `/api/v1/messenger/{account_id}/conversations/{conversation_id}` — GET
- `/api/v1/messenger/{account_id}/personal/auto-reply` — GET, PUT
- `/api/v1/messenger/{account_id}/personal/conversations` — GET
- `/api/v1/messenger/{account_id}/personal/conversations/{thread_id}` — GET
- `/api/v1/messenger/{account_id}/personal/index-brand` — POST
- `/api/v1/messenger/{account_id}/personal/send` — POST
- `/api/v1/messenger/{account_id}/personal/threads/{thread_id}/config` — GET, PUT
- `/api/v1/messenger/{account_id}/personal/threads/{thread_id}/memory` — GET
- `/api/v1/messenger/{account_id}/personal/threads/{thread_id}/pause` — POST
- `/api/v1/messenger/{account_id}/personal/threads/{thread_id}/resume` — POST
- `/api/v1/messenger/{account_id}/profile` — GET, PUT, DELETE
- `/api/v1/messenger/{account_id}/send` — POST
- `/api/v1/messenger/{account_id}/send-quick-replies` — POST
- `/api/v1/messenger/{account_id}/setup` — POST
- `/api/v1/messenger/{account_id}/unsubscribe` — POST
- `/api/v1/messenger/{account_id}/user/{psid}` — GET

## `meta-growth`

- `/api/v1/meta-growth/organic-campaign` — POST
- `/api/v1/meta-growth/readiness` — GET

## `ops`

- `/api/v1/ops/billing-digest` — POST
- `/api/v1/ops/billing-digest/preview` — GET
- `/api/v1/ops/browser-orchestrator` — GET
- `/api/v1/ops/browser-orchestrator/release` — POST
- `/api/v1/ops/console` — GET
- `/api/v1/ops/daily-digest` — POST
- `/api/v1/ops/daily-digest/preview` — GET
- `/api/v1/ops/paddle-digest` — POST
- `/api/v1/ops/paddle-digest/preview` — GET
- `/api/v1/ops/session-heal` — POST
- `/api/v1/ops/tiktok-audit` — PUT

## `profile`

- `/api/v1/profile/browser/cookies` — GET
- `/api/v1/profile/browser/extract` — POST
- `/api/v1/profile/browser/import-instagram-session` — POST
- `/api/v1/profile/browser/novnc-url` — GET
- `/api/v1/profile/browser/platforms` — GET
- `/api/v1/profile/browser/start` — POST
- `/api/v1/profile/browser/status` — GET
- `/api/v1/profile/browser/stop` — POST
- `/api/v1/profile/instagram/web-session` — GET, POST
- `/api/v1/profile/tiktok/accessibility/contrast` — POST
- `/api/v1/profile/tiktok/ads/personalized` — POST
- `/api/v1/profile/tiktok/business-verification/fill` — POST
- `/api/v1/profile/tiktok/business-verification/status` — GET
- `/api/v1/profile/tiktok/notifications/desktop` — POST
- `/api/v1/profile/tiktok/notifications/interactions` — POST
- `/api/v1/profile/tiktok/privacy/comments` — POST
- `/api/v1/profile/tiktok/privacy/direct-messages` — POST
- `/api/v1/profile/tiktok/privacy/private-account` — POST
- `/api/v1/profile/tiktok/session` — GET, POST
- `/api/v1/profile/tiktok/settings` — GET
- `/api/v1/profile/{account_id}` — GET, PUT
- `/api/v1/profile/{account_id}/cover` — POST
- `/api/v1/profile/{account_id}/login` — POST
- `/api/v1/profile/{account_id}/picture` — POST
- `/api/v1/profile/{account_id}/threads/settings` — GET, PUT

## `publishing`

- `/api/v1/publishing/history` — GET
- `/api/v1/publishing/queue` — GET, POST
- `/api/v1/publishing/queue/{queue_id}` — GET, DELETE
- `/api/v1/publishing/queue/{queue_id}/cancel` — POST
- `/api/v1/publishing/queue/{queue_id}/retry` — POST
- `/api/v1/publishing/retry/{queue_id}` — POST

## `reports`

- `/api/v1/reports` — GET
- `/api/v1/reports/files/{filename}` — GET
- `/api/v1/reports/notebooks` — GET
- `/api/v1/reports/run` — POST

## `secrets`

- `/api/v1/secrets` — GET
- `/api/v1/secrets/{key}` — GET, POST, PUT, DELETE

## `support`

- `/api/v1/support/report` — POST

## `teams`

- `/api/v1/teams` — GET, POST
- `/api/v1/teams/accept-invite` — POST
- `/api/v1/teams/{team_id}` — GET, DELETE, PATCH
- `/api/v1/teams/{team_id}/invite` — POST
- `/api/v1/teams/{team_id}/members/{user_id}` — POST, DELETE, PATCH

## `telegram`

- `/api/v1/telegram/connect` — POST
- `/api/v1/telegram/webhook/{account_id}` — POST
- `/api/v1/telegram/{account_id}/auto-reply` — GET, PUT
- `/api/v1/telegram/{account_id}/bot` — GET, PUT
- `/api/v1/telegram/{account_id}/bot/activate` — POST
- `/api/v1/telegram/{account_id}/bot/create` — POST
- `/api/v1/telegram/{account_id}/bot/deactivate` — POST
- `/api/v1/telegram/{account_id}/bot/personalities` — GET
- `/api/v1/telegram/{account_id}/credentials` — PUT
- `/api/v1/telegram/{account_id}/delete-webhook` — POST
- `/api/v1/telegram/{account_id}/group-watch` — GET, PUT
- `/api/v1/telegram/{account_id}/group-watch/activity` — GET
- `/api/v1/telegram/{account_id}/group-watch/add-chat` — POST
- `/api/v1/telegram/{account_id}/group-watch/digest-now` — POST
- `/api/v1/telegram/{account_id}/group-watch/setup-links` — POST
- `/api/v1/telegram/{account_id}/send` — POST
- `/api/v1/telegram/{account_id}/setup-status` — GET
- `/api/v1/telegram/{account_id}/setup-webhook` — POST
- `/api/v1/telegram/{account_id}/threads/pause` — POST
- `/api/v1/telegram/{account_id}/threads/resume` — POST

## `threads`

- `/api/v1/threads/followers` — GET
- `/api/v1/threads/insights` — GET
- `/api/v1/threads/posts` — GET
- `/api/v1/threads/posts/{media_id}` — DELETE
- `/api/v1/threads/posts/{media_id}/insights` — GET
- `/api/v1/threads/posts/{media_id}/reply` — POST
- `/api/v1/threads/profile` — GET, PUT
- `/api/v1/threads/quota` — GET
- `/api/v1/threads/{account_id}/dm/auto-reply` — GET, PUT
- `/api/v1/threads/{account_id}/dm/conversations` — GET
- `/api/v1/threads/{account_id}/dm/login` — POST
- `/api/v1/threads/{account_id}/dm/session-status` — GET
- `/api/v1/threads/{account_id}/dm/threads/{thread_id}` — GET
- `/api/v1/threads/{account_id}/dm/threads/{thread_id}/send` — POST

## `tiktok`

- `/api/v1/tiktok/accounts/{account_id}/creator-info` — GET
- `/api/v1/tiktok/accounts/{account_id}/health` — GET
- `/api/v1/tiktok/accounts/{account_id}/publish/cancel` — POST
- `/api/v1/tiktok/accounts/{account_id}/publish/status` — POST
- `/api/v1/tiktok/accounts/{account_id}/uploads` — GET
- `/api/v1/tiktok/accounts/{account_id}/videos` — GET
- `/api/v1/tiktok/accounts/{account_id}/videos/query` — POST
- `/api/v1/tiktok/{account_id}/dm/auto-reply` — GET, PUT
- `/api/v1/tiktok/{account_id}/dm/conversations` — GET
- `/api/v1/tiktok/{account_id}/dm/send` — POST

## `twitter`

- `/api/v1/twitter/{account_id}/dm/auto-reply` — GET, PUT
- `/api/v1/twitter/{account_id}/dm/events` — GET
- `/api/v1/twitter/{account_id}/dm/send` — POST

## `usage`

- `/api/v1/usage` — GET
- `/api/v1/usage/history` — GET

## `viber`

- `/api/v1/viber/connect` — POST
- `/api/v1/viber/webhook/{account_id}` — POST
- `/api/v1/viber/{account_id}/auto-reply` — GET, PUT
- `/api/v1/viber/{account_id}/broadcast` — POST
- `/api/v1/viber/{account_id}/credentials` — PUT
- `/api/v1/viber/{account_id}/delete-webhook` — POST
- `/api/v1/viber/{account_id}/send` — POST
- `/api/v1/viber/{account_id}/send-picture` — POST
- `/api/v1/viber/{account_id}/setup-status` — GET
- `/api/v1/viber/{account_id}/setup-webhook` — POST
- `/api/v1/viber/{account_id}/threads/{user_id}/pause` — POST
- `/api/v1/viber/{account_id}/threads/{user_id}/resume` — POST

## `whatsapp`

- `/api/v1/whatsapp/flows/endpoint` — POST
- `/api/v1/whatsapp/register/create-number` — POST
- `/api/v1/whatsapp/register/deregister` — POST
- `/api/v1/whatsapp/register/number` — POST
- `/api/v1/whatsapp/register/request-code` — POST
- `/api/v1/whatsapp/register/verify-code` — POST
- `/api/v1/whatsapp/webhook` — GET, POST
- `/api/v1/whatsapp/webhooks/subscribe` — POST, DELETE
- `/api/v1/whatsapp/webhooks/subscriptions/{waba_id}` — GET
- `/api/v1/whatsapp/{account_id}/auto-reply` — GET, PUT
- `/api/v1/whatsapp/{account_id}/bot` — GET, PUT
- `/api/v1/whatsapp/{account_id}/bot/activate` — POST
- `/api/v1/whatsapp/{account_id}/bot/create` — POST
- `/api/v1/whatsapp/{account_id}/bot/deactivate` — POST
- `/api/v1/whatsapp/{account_id}/bot/personalities` — GET
- `/api/v1/whatsapp/{account_id}/credentials` — PUT
- `/api/v1/whatsapp/{account_id}/flows` — GET, POST
- `/api/v1/whatsapp/{account_id}/flows/from-template` — POST
- `/api/v1/whatsapp/{account_id}/flows/send` — POST
- `/api/v1/whatsapp/{account_id}/flows/templates/list` — GET
- `/api/v1/whatsapp/{account_id}/flows/templates/{template_id}/json` — GET
- `/api/v1/whatsapp/{account_id}/flows/{flow_id}` — GET, PUT, DELETE
- `/api/v1/whatsapp/{account_id}/flows/{flow_id}/json` — GET, PUT
- `/api/v1/whatsapp/{account_id}/flows/{flow_id}/publish` — POST
- `/api/v1/whatsapp/{account_id}/flows/{flow_id}/validate` — POST
- `/api/v1/whatsapp/{account_id}/index-brand` — POST
- `/api/v1/whatsapp/{account_id}/phone/register` — POST
- `/api/v1/whatsapp/{account_id}/phone/request-code` — POST
- `/api/v1/whatsapp/{account_id}/phone/status` — GET
- `/api/v1/whatsapp/{account_id}/phone/verify-code` — POST
- `/api/v1/whatsapp/{account_id}/profile` — GET, PUT
- `/api/v1/whatsapp/{account_id}/send` — POST
- `/api/v1/whatsapp/{account_id}/send-template` — POST
- `/api/v1/whatsapp/{account_id}/setup` — POST
- `/api/v1/whatsapp/{account_id}/setup-status` — GET
- `/api/v1/whatsapp/{account_id}/threads/pause` — POST
- `/api/v1/whatsapp/{account_id}/threads/resume` — POST
- `/api/v1/whatsapp/{account_id}/threads/{phone}/config` — GET, PUT
- `/api/v1/whatsapp/{account_id}/threads/{phone}/memory` — GET
- `/api/v1/whatsapp/{account_id}/threads/{phone}/window` — GET

## `workflows`

- `/api/v1/workflows` — GET
- `/api/v1/workflows/content-templates` — GET, POST
- `/api/v1/workflows/content-templates/{template_id}` — DELETE, PATCH
- `/api/v1/workflows/deploy/{workflow_id}` — POST
- `/api/v1/workflows/execute/{workflow_id}` — POST
- `/api/v1/workflows/generate` — POST
- `/api/v1/workflows/import-cloudless-carousel` — POST
- `/api/v1/workflows/templates` — GET, POST
- `/api/v1/workflows/templates/{template_id}` — GET, DELETE, PATCH
- `/api/v1/workflows/{workflow_id}` — GET, DELETE
- `/api/v1/workflows/{workflow_id}/executions` — GET
- `/api/v1/workflows/{workflow_id}/undeploy` — POST
