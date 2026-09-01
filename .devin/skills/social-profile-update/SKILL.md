---
name: social-profile-update
description: >-
  Update social media business profiles programmatically across LinkedIn,
  Facebook, Instagram, Threads, Twitter/X, and TikTok. Use when the user wants
  to change bio, description, profile picture, cover photo, website, or other
  profile fields on any connected social account. Covers official Graph APIs,
  unofficial/private APIs, and third-party services with honest capability
  notes per platform.
allowed-tools:
  - read
  - exec
  - grep
  - glob
  - web_search
  - webfetch
triggers:
  - user
  - model
---

# Social Profile Update

Update business social profiles programmatically across all 6 platforms
supported by SocialAuto. Credentials are managed through the
`social-profile-secrets` skill and stored in the Cloudflare-first secret store
(`/api/v1/secrets`). All logins should be initiated from the SocialAuto app.

## Platform capability matrix

| Platform | Bio/Description | Profile picture | Cover/Banner | Name | Website | Method |
|----------|:-:|:-:|:-:|:-:|:-:|--------|
| **LinkedIn (Company Page)** | YES | YES | YES | YES | YES | Official API — `POST /organizations/{id}` with `rw_organization_admin` scope |
| **LinkedIn (Personal)** | NO | NO | NO | NO | NO | Official API is read-only for profiles (Partner-only write access) |
| **Facebook (Page)** | YES | YES | YES | NO | YES | Official Graph API — `POST /{page_id}` with Page access token + `MANAGE` task |
| **Instagram (Business)** | NO | NO | NO | NO | NO | Official Graph API is read-only for profile fields |
| **Threads** | NO | NO | NO | NO | NO | Official API is read-only (`GET /me` only) |
| **Twitter / X** | NO | NO | NO | NO | NO | Official v2 API removed profile write endpoints; requires unofficial/cookie-based APIs |
| **TikTok** | NO | NO | NO | NO | NO | Official API is read-only for profile fields |

### Key findings

**Officially writable via API:**
- **LinkedIn Company Page** — full profile update (description, logo, cover, website, name, industries, specialties) via the Organizations API with `rw_organization_admin` scope. Requires ADMINISTRATOR role on the page.
- **Facebook Page** — `about`, `description`, `website`, `phone`, `picture` (profile photo), `cover` (cover photo) via Graph API `POST /{page_id}` with a Page access token that has the `MANAGE` task. Cannot change the page name via API.

**Read-only via official API (no programmatic profile updates):**
- **Instagram Business** — the Graph API only supports `GET` on the IG User node. Fields like `biography`, `profile_picture_url`, `name`, `website` are readable (with proper scopes) but not writable. Profile changes must be done manually in the Instagram app.
- **Threads** — the official API only supports `GET /me` for profile info. No write endpoints for bio, name, or avatar.
- **Twitter / X** — the official v2 API removed the profile update endpoints that existed in v1.1. Updating profile fields requires unofficial approaches (cookie-based private APIs, third-party services like Xquik or TwexAPI).
- **TikTok** — the Display API and Content Posting API are read-only for profile data. No endpoints exist for updating bio, avatar, or display name.

**Unofficial / private API alternatives:**
- **Instagram** — `instagrapi` (Python) provides `account_edit()`, `account_set_biography()`, and `account_change_picture()` via Instagram's private mobile API. Requires username/password login (not OAuth). Risk of account ban.
- **Twitter / X** — `twexapi.io` and `xquik.com` offer paid third-party APIs that update name, bio, location, website, avatar, and banner via cookie-based authentication. Not official, carries risk.
- **Threads** — `threads-go` (Go) and other private API clients can read profiles but profile writes are not reliably supported.

## API details per platform

### LinkedIn Company Page (official API — writable)

**Prerequisites:**
- OAuth token with `rw_organization_admin` scope
- ADMINISTRATOR role on the target organization
- Organization ID (available in SocialAuto as the LinkedIn org account)

**Update description/about:**
```bash
curl -X POST "https://api.linkedin.com/rest/organizations/{org_id}" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -H "X-Restli-Method: PARTIAL_UPDATE" \
  -H "LinkedIn-Version: 202501" \
  -d '{"description": {"value": "New description here"}}'
```

**Writable fields:**
- `description` — company description
- `localizedDescription` — locale-specific description
- `coverPhotoV2` — cover image (upload via Images API first)
- `logoV2` — company logo (upload via Images API first)
- `specialties` — company specialties
- `website` — company website URL
- `industries` — industry URN list
- `foundedOn` — founding date
- `organizationType` — company type enum

**Read-only fields (cannot update via API):**
- `name` — company name (contact LinkedIn support to change)
- `vanityName` — URL slug (contact LinkedIn support)

### Facebook Page (official Graph API — writable)

**Prerequisites:**
- Page access token with `MANAGE` task (or `pages_manage_posts` + `pages_show_list`)
- Page ID (available in SocialAuto as the Facebook page account)

**Update page about/description:**
```bash
curl -X POST "https://graph.facebook.com/v21.0/{page_id}" \
  -H "Content-Type: application/json" \
  -d '{"about": "New about text", "description": "New description", "access_token": "{page_token}"}'
```

**Update profile picture:**
```bash
curl -X POST "https://graph.facebook.com/v21.0/{page_id}/picture" \
  -d "url={public_image_url}&access_token={page_token}"
```

**Update cover photo:**
```bash
curl -X POST "https://graph.facebook.com/v21.0/{page_id}" \
  -d "cover={photo_id}&access_token={page_token}"
```

**Writable fields:**
- `about` — short about text
- `description` — longer description
- `website` — website URL
- `phone` — phone number
- `picture` — profile picture (via `/picture` edge with `url` param)
- `cover` — cover photo (photo ID of an uploaded photo)

**Cannot update via API:**
- `name` — page name (must be changed in Facebook UI)
- Page settings like category, address (some require UI)

### Instagram Business (official API — read-only)

The Instagram Graph API does NOT support profile updates. The `IG User` node
only supports `GET` requests. To update Instagram profile fields:

**Manual method (recommended):**
1. Open the Instagram app or instagram.com
2. Edit Profile → update name, username, bio, website, profile picture

**Unofficial method (instagrapi — use at your own risk):**
```python
from instagrapi import Client
cl = Client()
cl.login("username", "password")
cl.account_set_biography("New bio text")
cl.account_edit(external_url="https://cloudless.gr", full_name="Cloudless")
cl.account_change_picture("/path/to/profile_pic.jpg")
```

**Warning:** `instagrapi` uses Instagram's private mobile API, not the official
Graph API. This can trigger account suspension or ban. Not recommended for
production use.

### Threads (official API — read-only)

The Threads API only supports `GET /me` for profile info. No write endpoints
exist for bio, name, or profile picture.

**To update Threads profile:**
- Threads shares profile info with Instagram. Update your Instagram profile
  and the Threads profile syncs automatically.
- Or edit manually in the Threads app.

### Twitter / X (official API — no profile write)

Twitter API v2 does not include profile update endpoints. The v1.1
`account/update_profile` and `account/update_profile_image` endpoints were
deprecated and removed.

**Third-party alternatives (paid, unofficial):**
- **Xquik** (`xquik.com`) — `PATCH /x/profile`, `PATCH /x/profile/avatar`, `PATCH /x/profile/banner`
- **TwexAPI** (`twexapi.io`) — `POST /twitter/profile` with cookie auth

Both require X account cookies (not OAuth tokens) and carry risk of account
suspension. Not recommended for brand accounts.

**Manual method (recommended):**
- Update via X/Twitter settings UI at x.com/settings/profile

### TikTok (official API — read-only)

The TikTok Display API and Content Posting API do not include profile update
endpoints. Profile fields (bio, avatar, display name) can only be read.

**To update TikTok profile:**
- Edit manually in the TikTok app → Profile → Edit Profile

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# Get current profile info for all connected accounts
.devin/skills/social-profile-update/scripts/get-all-profiles.sh

# Update LinkedIn Company Page description/about
.devin/skills/social-profile-update/scripts/update-linkedin-org.sh "New description" "New about text"

# Update Facebook Page about/description/website
.devin/skills/social-profile-update/scripts/update-facebook-page.sh --about "New about" --description "New desc" --website "https://cloudless.gr"

# Update Facebook Page profile picture from a media library asset
.devin/skills/social-profile-update/scripts/update-facebook-picture.sh <media_asset_id>

# Prepare brand-aligned profile text for manual entry on read-only platforms
.devin/skills/social-profile-update/scripts/generate-brand-profile.sh
```

## Brand profile reference (cloudless.gr)

Based on the Cloudless brand identity:

- **Name**: Cloudless
- **Tagline**: Clear skies. Zero friction.
- **Bio (short)**: Cloud architecture, serverless development, data analytics & AI-powered digital marketing. Clear skies, zero friction.
- **Bio (longer)**: We help startups and SMBs ship faster with serverless cloud architecture, Cloudflare-first delivery, and AI-powered digital marketing. No lock-in. Transparent pricing. Results in 14 days.
- **Website**: https://cloudless.gr
- **Industry**: Cloud Computing, Serverless & AI Marketing
- **Values**: Innovation, Customer-centricity, Flexibility, Collaboration
- **Messaging pillars**: Serverless simplicity, No lock-in, Transparent pricing, Results in 14 days, Data-driven growth
- **Preferred phrases**: Clear skies, serverless, no lock-in, Cloudflare, transparent pricing, results in 14 days, open-source, data-driven, full control, zero friction
- **Banned phrases**: lock-in, vendor lock-in, enterprise BS, synergy, game-changer, disrupt, revolutionary, cutting-edge
- **Visual style**: Dark navy (#0b1220) backgrounds with teal (#22d3e6) accents. Modern technology aesthetic. Clean, minimalist.

## Important notes

- Always verify the current profile before making changes.
- LinkedIn and Facebook are the only platforms with official write APIs for profile fields.
- Instagram, Threads, Twitter/X, and TikTok profile changes must be done manually or via unofficial/private APIs that carry account-ban risk.
- Never store or commit social media passwords in the repository.
- For LinkedIn org updates, the token must have `rw_organization_admin` scope and the user must be an ADMINISTRATOR of the organization.
- For Facebook Page updates, use a Page access token (not a user token) with the `MANAGE` task permission.
- Profile picture uploads require a publicly accessible URL (Facebook) or a two-step upload via the Images API (LinkedIn).
