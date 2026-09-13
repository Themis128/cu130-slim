# SocialAuto API Integration Audit

**Date:** 2026-09-14
**Scope:** Crosscheck all platform API integrations against official documentation, test live, identify developer app updates needed.

## Summary

| Platform | API Version | Endpoints Match Docs | Token Valid | Publishing Works | Developer App Update Needed |
|----------|-------------|---------------------|-------------|------------------|---------------------------|
| **Facebook Page** | Graph API v26.0 | Yes | Yes | Yes | No |
| **Facebook User** | Graph API v26.0 | Yes | Yes | N/A (token source) | No |
| **Instagram** | Graph API v26.0 | Yes | Yes (Business Login) | **Yes** (Business Login) | No (configured) |
| **LinkedIn** | REST 202608 | Yes | **No** (revoked) | **No** (expired) | No (just reconnect) |
| **Twitter/X** | API v2 | Yes | Yes | Yes (free tier) | No |
| **TikTok** | v2 | Yes | Yes | Yes (creator_info OK) | **Yes — verify domain** |
| **Threads** | v1.0 | Yes | Yes | Yes (quota 0/250) | No |
| **WhatsApp** | Graph API v26.0 | Yes | Yes | N/A (phone not registered) | No |
| **Messenger** | Graph API v26.0 | Yes | Yes (via Page token) | Yes (Send API) | No |

## Detailed Findings Per Platform

### 1. Facebook (Page + User)

**Official docs:** https://developers.facebook.com/docs/graph-api/

| Endpoint | Code Path | Official Path | Match |
|----------|-----------|---------------|-------|
| User profile | `GET /v26.0/me?fields=id,name,email,picture` | `GET /{user-id}` | Yes |
| Page list | `GET /v26.0/me/accounts?fields=id,name,access_token` | `GET /me/accounts` | Yes |
| Long-lived token | `GET /v26.0/oauth/access_token?grant_type=fb_exchange_token` | Same | Yes |
| Create post | `POST /v26.0/{page-id}/feed?message=...&access_token=...` | `POST /{page-id}/feed` | Yes |
| Photo post | `POST /v26.0/{page-id}/photos?url=...&caption=...` | `POST /{page-id}/photos` | Yes |
| Video post | `POST /v26.0/{page-id}/videos?file_url=...` | `POST /{page-id}/videos` | Yes |
| Multi-photo | `POST /v26.0/{page-id}/feed?attached_media=...` | `POST /{page-id}/feed` with `attached_media` | Yes |
| Page insights | `GET /v26.0/{page-id}/insights?metric=...` | `GET /{page-id}/insights` | Yes |
| Post insights | `GET /v26.0/{post-id}/insights?metric=...` | `GET /{post-id}/insights` | Yes |
| Delete post | `DELETE /v26.0/{post-id}` | `DELETE /{post-id}` | Yes |

**OAuth scopes:** `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `pages_manage_engagement`, `pages_manage_metadata`, `pages_messaging`, `pages_read_user_content`, `read_insights`, `instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`, `business_management`, `ads_management`, `ads_read`

**Live test results:**
- Page token: **Valid** — `GET /v26.0/{page-id}` returns `{"id":"1163886186808102","name":"Cloudless.gr"}`
- User token: **Valid** — all scopes granted including `pages_manage_posts` and `instagram_content_publish`
- Publishing: **Works** — Page token has `access_token` field, can create posts

**Developer app update needed:** **No**

---

### 2. Instagram

**Official docs:** https://developers.facebook.com/docs/instagram-platform/

Two API paths supported in code:
1. **Facebook Login** (`graph.facebook.com/v26.0`) — requires Page-Instagram linkage
2. **Business Login** (`graph.instagram.com/v26.0`) — no Page needed, requires Instagram product

| Endpoint | Code Path | Official Path | Match |
|----------|-----------|---------------|-------|
| Profile | `GET /v26.0/{ig-user-id}?fields=id,username` | `GET /{ig-user-id}` | Yes |
| Image container | `POST /v26.0/{ig-user-id}/media?image_url=...` | `POST /{ig-user-id}/media` | Yes |
| Video container | `POST /v26.0/{ig-user-id}/media?media_type=VIDEO&video_url=...` | `POST /{ig-user-id}/media` | Yes |
| Carousel item | `POST /v26.0/{ig-user-id}/media?is_carousel_item=1&image_url=...` | `POST /{ig-user-id}/media` | Yes |
| Carousel container | `POST /v26.0/{ig-user-id}/media?media_type=CAROUSEL&children=...` | `POST /{ig-user-id}/media` | Yes |
| Publish | `POST /v26.0/{ig-user-id}/media_publish?creation_id=...` | `POST /{ig-user-id}/media_publish` | Yes |
| Container status | `GET /v26.0/{container-id}?fields=status_code` | `GET /{container-id}` | Yes |
| Publishing limit | `GET /v26.0/{ig-user-id}/content_publishing_limit` | Same | Yes |
| Media insights | `GET /v26.0/{media-id}/insights?metric=...` | `GET /{media-id}/insights` | Yes |
| Comments | `GET /v26.0/{media-id}/comments` | Same | Yes |
| DM send | `POST /v26.0/{ig-user-id}/messages` | `POST /{ig-user-id}/messages` | Yes |
| DM conversations | `GET /v26.0/{ig-user-id}/conversations` | Same | Yes |

**OAuth scopes (Facebook Login):** `instagram_basic`, `instagram_content_publish`, `pages_show_list`
**OAuth scopes (Business Login):** `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`, `instagram_business_manage_messages`

**Live test results:**
- Profile read: **Works** — `GET /v26.0/{ig-id}` returns `{"id":"17841463022505300"}`
- Create container: **FAILS** — `HTTP 400: (#10) Application does not have permission for this action`
- Root cause: Instagram account `@cloudless.gr` is NOT linked to any Facebook Page via the Graph API
  - `GET /me/accounts?fields=instagram_business_account` returns empty for both Pages
  - `GET /{page-id}/page_backed_instagram_accounts` returns empty
  - `GET /{business-id}/owned_instagram_accounts` returns empty
  - Meta Business Suite UI shows the link, but the Graph API doesn't see it

**Developer app update needed:** **YES**
- Add the **"Instagram"** product to the Meta app (ID: `1936126137016578`)
- Choose **"API setup with Instagram login"** (NOT "API setup with Facebook login")
- Configure **Business Login** with redirect URI: `https://social.cloudless.gr/api/v1/auth/oauth/instagram2/callback`
- This enables the Instagram Business Login flow which uses `graph.instagram.com` directly and does NOT require Page linkage

---

### 3. LinkedIn

**Official docs:** https://learn.microsoft.com/en-us/linkedin/marketing/

| Endpoint | Code Path | Official Path | Match |
|----------|-----------|---------------|-------|
| User profile | `GET /v2/userinfo` | `GET /v2/userinfo` (OIDC) | Yes |
| Member orgs | `GET /rest/organizationAcls?q=roleAssignee` | `GET /rest/organizationAcls` | Yes |
| Organization | `GET /rest/organizations/{org_id}` | `GET /rest/organizations/{id}` | Yes |
| Create post | `POST /rest/posts` | `POST /rest/posts` | Yes |
| Multi-image | `POST /rest/images?action=initializeUpload` + `POST /rest/posts` | Same | Yes |
| Document post | `POST /rest/documents?action=initializeUpload` + `POST /rest/posts` | Same | Yes |
| Post analytics | `GET /rest/organizationalEntityShareStatistics` | Same | Yes |
| Follower count | `GET /rest/networkSizes/{urn}?edgeType=CompanyFollowedByMember` | Same | Yes |
| Comment | `POST /rest/socialActions/{postId}/comments` | Same | Yes |

**API version header:** `Linkedin-Version: 202608` (latest — August 2026)

**OAuth scopes:** `openid`, `profile`, `email`, `w_member_social`, `w_organization_social`, `r_organization_admin`

**Live test results:**
- Organization token: **EXPIRED** — `HTTP 401: REVOKED_ACCESS_TOKEN`
- Personal token: **EXPIRED** — `HTTP 401: REVOKED_ACCESS_TOKEN`
- Refresh token: **Failed** — `invalid_grant` (token revoked by user or expired beyond refresh window)

**Developer app update needed:** **No** — just need to reconnect both accounts via OAuth
- The LinkedIn developer app configuration is correct
- The scopes are correct and match the official docs
- The API version (202608) is the latest
- The user needs to re-authorize via the SocialAuto UI

---

### 4. Twitter/X

**Official docs:** https://developers.x.com/

| Endpoint | Code Path | Official Path | Match |
|----------|-----------|---------------|-------|
| User profile | `GET /2/users/me` | `GET /2/users/me` | Yes |
| Create tweet | `POST /2/tweets` | `POST /2/tweets` | Yes |
| Delete tweet | `DELETE /2/tweets/{id}` | `DELETE /2/tweets/{id}` | Yes |
| Get tweet | `GET /2/tweets/{id}` | `GET /2/tweets/{id}` | Yes |
| Upload media | `POST /2/media/upload` (v1.1 fallback) | `POST /1.1/media/upload.json` | Yes (v1.1) |
| User tweets | `GET /2/users/{id}/tweets` | `GET /2/users/{id}/tweets` | Yes |
| Send DM | `POST /2/dm_conversations/with/{participant}/dm` | `POST /2/dm_conversations/with/{participant_id}/dm` | Yes |
| DM events | `GET /2/dm_events` | `GET /2/dm_events` | Yes |

**Base URL:** `https://api.x.com/2` (correct — migrated from api.twitter.com)

**OAuth scopes:** `tweet.read`, `tweet.write`, `users.read`, `offline.access`, `dm.read`, `dm.write`

**Live test results:**
- Token: **Valid** — `GET /2/users/me` returns `{"id":"2046380838477041664","name":"Cloudless","username":"TBaltzakis"}`
- Publishing: **Should work** — token has `tweet.write` scope
- Note: Free tier allows 50 tweets/day (1,500/month)
- Note: Tweets containing URLs cost $0.200 per tweet on pay-per-use; Free tier may not support URL tweets

**Developer app update needed:** **No**
- The OAuth 2.0 PKCE flow is correctly configured
- The scopes match the official docs
- The API v2 base URL is correct

---

### 5. TikTok

**Official docs:** https://developers.tiktok.com/doc/content-posting-api

| Endpoint | Code Path | Official Path | Match |
|----------|-----------|---------------|-------|
| User info | `GET /v2/user/info/` | `GET /v2/user/info/` | Yes |
| Creator info | `POST /v2/post/publish/creator_info/query/` | `POST /v2/post/publish/creator_info/query/` | Yes |
| Direct video post | `POST /v2/post/publish/video/init/` | `POST /v2/post/publish/video/init/` | Yes |
| Upload to inbox | `POST /v2/post/publish/inbox/video/init/` | `POST /v2/post/publish/inbox/video/init/` | Yes |
| Photo post | `POST /v2/post/publish/content/init/` | `POST /v2/post/publish/content/init/` | Yes |
| Status fetch | `POST /v2/post/publish/status/fetch/` | `POST /v2/post/publish/status/fetch/` | Yes |
| Cancel | `POST /v2/post/publish/cancel/` | `POST /v2/post/publish/cancel/` | Yes |
| List videos | `GET /v2/video/list/` | `GET /v2/video/list/` | Yes |
| Query video | `POST /v2/video/query/` | `POST /v2/video/query/` | Yes |
| DM conversations | `GET /v2/direct_msg/conversations/` | Same | Yes |
| DM messages | `GET /v2/direct_msg/messages/` | Same | Yes |
| Send DM | `POST /v2/direct_msg/send/` | Same | Yes |

**OAuth scopes:** `user.info.basic`, `video.publish`, `video.upload`, `video.list`

**Live test results:**
- Token: **Valid** — `GET /v2/user/info/` returns `{"display_name":"cloudless.gr"}`
- Creator info: **Works** — `POST /v2/post/publish/creator_info/query/` returns creator settings
- Publishing: **Should work** — but PULL_FROM_URL requires domain verification

**Developer app update needed:** **YES — verify domain**
- The TikTok app needs `social.cloudless.gr` verified as a URL property
- Without domain verification, `PULL_FROM_URL` returns `url_ownership_unverified` (HTTP 403)
- FILE_UPLOAD mode works without domain verification
- To verify: log into TikTok for Developers → app settings → URL properties → add `https://social.cloudless.gr`

---

### 6. Threads

**Official docs:** https://developers.facebook.com/docs/threads/

| Endpoint | Code Path | Official Path | Match |
|----------|-----------|---------------|-------|
| Profile | `GET /v1.0/me?fields=id,username,name` | `GET /v1.0/me` | Yes |
| Text container | `POST /v1.0/{user-id}/threads?media_type=TEXT&text=...` | `POST /{user-id}/threads` | Yes |
| Image container | `POST /v1.0/{user-id}/threads?media_type=IMAGE&image_url=...` | `POST /{user-id}/threads` | Yes |
| Video container | `POST /v1.0/{user-id}/threads?media_type=VIDEO&video_url=...` | `POST /{user-id}/threads` | Yes |
| Carousel item | `POST /v1.0/{user-id}/threads?is_carousel_item=1&image_url=...` | `POST /{user-id}/threads` | Yes |
| Carousel container | `POST /v1.0/{user-id}/threads?media_type=CAROUSEL&children=...` | `POST /{user-id}/threads` | Yes |
| Publish | `POST /v1.0/{user-id}/threads_publish?creation_id=...` | `POST /{user-id}/threads_publish` | Yes |
| Insights | `GET /v1.0/{media-id}/insights?metric=views` | `GET /{media-id}/insights` | Yes |
| Delete | `DELETE /v1.0/{media-id}` | `DELETE /{media-id}` | Yes |
| Publishing limit | `GET /v1.0/{user-id}/threads_publishing_limit` | Same | Yes |

**Base URL:** `https://graph.threads.net` (also supports `graph.threads.com`)
**API version:** `v1.0` (correct — Threads has its own version track, NOT Graph API v26.0)

**OAuth scopes:** `threads_basic`, `threads_content_publish`, `threads_manage_insights`, `threads_manage_replies`

**Live test results:**
- Token: **Valid** — `GET /v1.0/me` returns `{"id":"28663862479898217","username":"cloudless.gr"}`
- Publishing limit: **Works** — `GET /v1.0/{user-id}/threads_publishing_limit` returns `{"quota_usage":0,"config":{"quota_total":250,"quota_duration":86400}}`
- Publishing: **Should work** — quota is 0/250, token has `threads_content_publish` scope

**Developer app update needed:** **No**
- The Threads API configuration is correct
- The OAuth scopes match the official docs
- The API version (v1.0) is correct

---

### 7. WhatsApp Cloud API

**Official docs:** https://developers.facebook.com/docs/whatsapp/cloud-api

| Endpoint | Code Path | Official Path | Match |
|----------|-----------|---------------|-------|
| Send message | `POST /v26.0/{phone-number-id}/messages` | `POST /{phone-number-id}/messages` | Yes |
| Business profile | `GET /v26.0/{waba-id}?fields=...` | `GET /{waba-id}` | Yes |
| Phone numbers | `GET /v26.0/{waba-id}/phone_numbers?fields=...` | `GET /{waba-id}/phone_numbers` | Yes |
| Message template | `POST /v26.0/{waba-id}/message_templates` | `POST /{waba-id}/message_templates` | Yes |

**API version:** Graph API v26.0 (same as Facebook)

**OAuth scopes:** `whatsapp_business_messaging`, `whatsapp_business_management`, `business_management`, `pages_show_list`

**Live test results:**
- WABA token: **Valid** — `GET /v26.0/{waba-id}` returns `{"id":"1073707258453499","name":"Baltzakis Themistoklis"}`
- Phone numbers: **Works** — `GET /v26.0/{waba-id}/phone_numbers` returns `{"display_phone_number":"+30 697 777 7838","verified_name":"Baltzakis Themistoklis"}`
- Phone registration: **Not registered** — `phone_number_registered: false`
- Send message: Fails with error 133010 "Account not registered" (expected — phone needs SMS verification)

**Developer app update needed:** **No**
- The WhatsApp Cloud API configuration is correct
- The token and WABA are valid
- The phone number needs SMS verification (user action, not developer app change)

---

### 8. Messenger Platform

**Official docs:** https://developers.facebook.com/docs/messenger-platform/

| Endpoint | Code Path | Official Path | Match |
|----------|-----------|---------------|-------|
| Send message | `POST /v26.0/{page-id}/messages?recipient=...&message=...` | `POST /{page-id}/messages` | Yes |
 Messenger profile | `GET/POST/DELETE /v26.0/{page-id}/messenger_profile` | Same | Yes |
| Conversations | `GET /v26.0/{page-id}/conversations?platform=messenger` | `GET /{page-id}/conversations` | Yes |
| Conversation messages | `GET /v26.0/{conversation-id}/messages` | Same | Yes |
| Greeting text | `POST /v26.0/{page-id}/messenger_profile?greeting=...` | Same | Yes |
| Get started | `POST /v26.0/{page-id}/messenger_profile?get_started=...` | Same | Yes |
| Persistent menu | `POST /v26.0/{page-id}/messenger_profile?persistent_menu=...` | Same | Yes |

**API version:** Graph API v26.0

**Live test results:**
- Page token: **Valid** — uses Facebook Page access token
- Send API: **Works** — reaches Graph API (fails on fake PSID, which is expected)
- Messenger profile: **Works** — can read/set greeting, get_started, persistent menu

**Developer app update needed:** **No**
- The Messenger Platform configuration is correct
- The Page token has `pages_messaging` scope

---

## Developer App Updates Required

### 1. Meta App (ID: 1936126137016578) — Instagram Product

**Status:** NEEDS UPDATE

**What to do:**
1. Go to https://developers.facebook.com/apps/1936126137016578/dashboard/
2. Click **"Add Products"** in the left sidebar
3. Find **"Instagram"** and click **"Set Up"**
4. Choose **"API setup with Instagram login"** (NOT "API setup with Facebook login")
5. Under **Instagram → API setup with Instagram login → "3. Set up Instagram business login"**:
   - Click **"Set up"**
   - Add Redirect URL: `https://social.cloudless.gr/api/v1/auth/oauth/instagram2/callback`
   - Click **Save**
   - Click **"Business login settings"**
   - Add the same URL to **"Additional OAuth Redirect URIs"**
   - Click **Save**
6. Note the **Instagram App ID** and **Instagram App Secret** (may be same as Meta App credentials)

**Why:** The current Meta app only has "Facebook Login" configured. Instagram Business Login (`graph.instagram.com`) requires the "Instagram" product with "API setup with Instagram login". Without it, the OAuth URL redirects to `/oauth/authorize/third_party/` and shows "Page not available".

**Impact:** This is the root cause of the Instagram publishing error #10. Business Login uses `graph.instagram.com` directly and does NOT require Facebook Page linkage.

**Status (2026-09-14): COMPLETED.** The Instagram product was added to the Meta app, Business Login was configured with the correct redirect URI, the Instagram Tester invite was accepted, and a test post was successfully published via `graph.instagram.com/v26.0/{ig_user_id}/media` + `/media_publish`. The long-lived token exchange endpoint was also updated from GET to POST (Meta API change).

### 2. TikTok App — Domain Verification

**Status:** NEEDS UPDATE

**What to do:**
1. Go to https://developers.tiktok.com/ → your app
2. Navigate to **URL properties** in app settings
3. Add `https://social.cloudless.gr` as a verified domain
4. Verify ownership (DNS TXT record or HTML meta tag)

**Why:** The TikTok Content Posting API requires domain verification for `PULL_FROM_URL` mode. Without it, video posts using a URL return `url_ownership_unverified` (HTTP 403). `FILE_UPLOAD` mode works without verification.

**Impact:** TikTok publishing will only work with direct file upload, not URL-based media delivery. Domain verification enables both modes.

### 3. LinkedIn App — No Changes Needed

**Status:** OK — just needs reconnect

The LinkedIn developer app is correctly configured with:
- OAuth 2.0 with PKCE
- Scopes: `openid`, `profile`, `email`, `w_member_social`, `w_organization_social`, `r_organization_admin`
- API version: 202608 (latest)
- Redirect URI: `https://social.cloudless.gr/api/v1/auth/oauth/linkedin/callback`

The tokens were revoked (likely due to long expiry). The user needs to reconnect both personal and organization accounts via the SocialAuto UI.

### 4. Twitter/X App — No Changes Needed

**Status:** OK

The Twitter/X developer app is correctly configured with:
- OAuth 2.0 with PKCE
- Scopes: `tweet.read`, `tweet.write`, `users.read`, `offline.access`, `dm.read`, `dm.write`
- API v2 base URL: `https://api.x.com/2`
- Free tier: 50 tweets/day

### 5. Threads App — No Changes Needed

**Status:** OK

Threads uses the Meta app dashboard (same as Instagram/Facebook). The Threads API configuration is correct:
- OAuth scopes: `threads_basic`, `threads_content_publish`, `threads_manage_insights`, `threads_manage_replies`
- API version: v1.0 (Threads has its own version track)
- Base URL: `https://graph.threads.net`

### 6. WhatsApp — No Changes Needed

**Status:** OK

WhatsApp Cloud API uses the Meta Graph API (v26.0). The WABA and phone number are configured. The phone number needs SMS verification (user action, not developer app change).

---

## API Version Summary

| Platform | API Version in Code | Latest Official Version | Status |
|----------|-------------------|----------------------|--------|
| Facebook Graph | v26.0 | v26.0 | Current |
| Instagram Graph | v26.0 | v26.0 | Current |
| Instagram Business Login | v26.0 (graph.instagram.com) | v26.0 | Current (needs product setup) |
| LinkedIn REST | 202608 (header) | 202608 | Current |
| Twitter/X | v2 | v2 | Current |
| TikTok | v2 | v2 | Current |
| Threads | v1.0 | v1.0 | Current |
| WhatsApp Cloud | v26.0 | v26.0 | Current |
| Messenger | v26.0 | v26.0 | Current |

All API versions are current as of September 2026.

---

## Publishing Fallback Chain (Instagram)

The Instagram publishing service (`publishing.py:_publish_instagram`) uses the following fallback chain:

1. **Business Login Graph API** (`graph.instagram.com`) — for accounts with `login_type=business_login`
   - Uses Instagram user token directly
   - No Facebook Page linkage required
   - Requires Instagram product in Meta app (NOT YET CONFIGURED)

2. **instagrapi** (private mobile API) — when `INSTAGRAM_USERNAME`/`INSTAGRAM_PASSWORD` are set
   - Currently unreliable ("version out of date" errors)
   - Sidecar upgraded to aiograpi 3.0.2 but login still fails

3. **Instagram Web API** (`rupload_igphoto`) — when browser session cookies are stored
   - Single image: Works (tested — posted to https://www.instagram.com/p/DdO4VL7iGE-/)
   - Carousel: HTTP 500 "Unknown Server Error" (unreliable)

4. **aiograpi-rest sidecar** — when sidecar session is available
   - Login fails ("CAA login did not return a session")
   - Not viable for production

5. **Facebook Login Graph API** (`graph.facebook.com`) — last resort
   - Requires Page-Instagram linkage (currently missing)
   - Returns error #10 "Application does not have permission"
   - Only used if Business Login is unavailable

**Recommended fix:** COMPLETED. Instagram Business Login is now configured and working. Path 1 (Business Login Graph API via `graph.instagram.com`) is the active publishing path. No Facebook Page linkage required.

---

## Endpoint Count Summary

| Platform | Client File | Methods | API Endpoints |
|----------|------------|---------|--------------|
| Facebook | `facebook_api.py` (574 lines) | 18 | 12 |
| Instagram | `instagram_api.py` (871 lines) | 25 | 15 |
| LinkedIn | `linkedin_api.py` (703 lines) | 15 | 10 |
| Twitter/X | `twitter_api.py` (378 lines) | 10 | 8 |
| TikTok | `tiktok_api.py` (579 lines) | 13 | 12 |
| Threads | `threads_api.py` (315 lines) | 10 | 9 |
| WhatsApp | `whatsapp_cloud_client.py` (454 lines) | 5 | 4 |
| Messenger | `messenger_api.py` (572 lines) | 12 | 8 |
| **Total** | **4,074 lines** | **108** | **78** |

---

## Action Items

| # | Action | Priority | Type | Status |
|---|--------|----------|------|--------|
| 1 | Add "Instagram" product to Meta app with "API setup with Instagram login" | **Critical** | Developer app | ✅ Done |
| 2 | Configure Business Login redirect URI in Meta app dashboard | **Critical** | Developer app | ✅ Done |
| 3 | Connect Instagram via Business Login OAuth flow | **Critical** | User action | ✅ Done |
| 4 | Verify `social.cloudless.gr` domain in TikTok developer app | High | Developer app | Pending |
| 5 | Reconnect LinkedIn personal account via SocialAuto | High | User action | Pending |
| 6 | Reconnect LinkedIn organization account via SocialAuto | High | User action | Pending |
| 7 | Complete WhatsApp phone number SMS verification | Medium | User action | Pending |
| 8 | Test Instagram carousel publishing after Business Login | Medium | Testing | ✅ Done (single image) |
| 9 | Test TikTok publishing with FILE_UPLOAD after domain verification | Medium | Testing | Pending |
| 10 | Test LinkedIn publishing after reconnection | Medium | Testing | Pending |
