---
name: social-accounts-manager
description: >-
  Manage and update all connected social media accounts (Facebook Page,
  Facebook personal, LinkedIn Organization, LinkedIn personal, Instagram
  Business) with professional links, bio, website, and profile fields.
  Uses the correct method per platform: Graph API for Facebook Pages,
  browser sidecar for LinkedIn, instagrapi for Instagram, and SocialAuto
  profile API where supported. Use when adding links, updating bios,
  syncing profile info across all platforms, or managing account
  connectivity.
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

# Social Accounts Manager

Update profile fields (website, bio, links, contact info) across ALL
connected social accounts using the correct method per platform.

## Connected accounts

| Platform | Account | SocialAuto ID | Method |
|----------|---------|---------------|--------|
| Facebook Page | cloudless.gr | `ca22c266-4a93-47bd-b3ed-32b38d0ffa7b` | Graph API (Page token) |
| Facebook personal | Themistoklis Baltzakis | `a8ca7769-60a9-413d-ac59-5b16f7dd12a4` | FB browser sidecar (port 9226) |
| LinkedIn Organization | cloudless.gr | `57e9ed31-7454-4979-9e6b-efcb35d2d78e` | LinkedIn sidecar (port 9225) |
| LinkedIn personal | Themistoklis Baltzakis | `e17ee097-8ff9-46f4-9422-b0c59e27b7a6` | LinkedIn sidecar (port 9225) |
| Instagram Business | cloudless.gr | `31822deb-fbd5-4b7b-a5fd-5ca864783cf7` | instagrapi sidecar (port 8011) |

## Platform capability matrix

| Platform | Bio/About | Website | Profile pic | Cover | Links in bio | Method |
|----------|:-:|:-:|:-:|:-:|:-:|--------|
| **Facebook Page** | YES (100 char about + long description) | YES (single URL) | YES | YES | In description | Graph API `POST /{page_id}` |
| **Facebook personal** | YES (bio) | YES (contact info) | YES | YES | In bio | FB browser sidecar |
| **LinkedIn Org** | YES (tagline + description, 2000 chars) | YES (single URL) | YES | YES | In description | LinkedIn sidecar admin edit |
| **LinkedIn personal** | YES (about section) | YES (contact info, multiple URLs) | YES | YES | In about | LinkedIn sidecar profile edit |
| **Instagram Business** | YES (150 chars, emojis count as 2) | YES (single external_url) | YES | NO | NO (use external_url) | instagrapi sidecar |

## Professional links to use

```
Website:     https://cloudless.gr
Portfolio:   https://baltzakisthemis.com
WhatsApp:    https://wa.me/306977777838
LinkedIn:    https://www.linkedin.com/company/cloudless-gr
```

## API base URLs

```
SocialAuto API:    http://localhost:8083/api/v1
FB sidecar:        http://localhost:9226
LinkedIn sidecar:  http://localhost:9225
Instagram sidecar: http://localhost:8011
```

## Authentication

```bash
TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=tbaltzakis@cloudless.gr&password=TH!123789th!" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

## Tool scripts

All scripts run from the repo root `cu130-slim/`:

### Universal (all platforms)

```bash
# Add professional links to ALL connected accounts
.devin/skills/social-accounts-manager/scripts/add-links-all.sh

# Read ALL profiles and show current state
.devin/skills/social-accounts-manager/scripts/read-all.sh

# Sync profile info from LinkedIn to all other platforms
.devin/skills/social-accounts-manager/scripts/sync-from-linkedin.sh
```

### Facebook Page

```bash
# Update Facebook Page website
.devin/skills/social-accounts-manager/scripts/fb-page-update-website.sh "https://cloudless.gr"

# Update Facebook Page about (100 char limit)
.devin/skills/social-accounts-manager/scripts/fb-page-update-about.sh "About text"

# Update Facebook Page long description
.devin/skills/social-accounts-manager/scripts/fb-page-update-description.sh "Long description"

# Update Facebook Page profile picture
.devin/skills/social-accounts-manager/scripts/fb-page-update-picture.sh /path/to/image.png

# Update Facebook Page cover photo
.devin/skills/social-accounts-manager/scripts/fb-page-update-cover.sh /path/to/cover.png
```

### Facebook Personal

```bash
# Update Facebook personal profile bio
.devin/skills/social-accounts-manager/scripts/fb-personal-update-bio.sh "Bio text"

# Update Facebook personal contact info (website)
.devin/skills/social-accounts-manager/scripts/fb-personal-update-website.sh "https://cloudless.gr"

# Update Facebook personal profile picture
.devin/skills/social-accounts-manager/scripts/fb-personal-update-picture.sh /path/to/image.png
```

### LinkedIn Organization

```bash
# Update LinkedIn org tagline
.devin/skills/social-accounts-manager/scripts/li-org-update-tagline.sh "Tagline text"

# Update LinkedIn org description (2000 char limit)
.devin/skills/social-accounts-manager/scripts/li-org-update-description.sh "Description text"

# Update LinkedIn org website
.devin/skills/social-accounts-manager/scripts/li-org-update-website.sh "https://cloudless.gr"

# Update LinkedIn org specialties
.devin/skills/social-accounts-manager/scripts/li-org-update-specialties.sh "Cloud, AI, Software"

# Update LinkedIn org logo
.devin/skills/social-accounts-manager/scripts/li-org-update-logo.sh /path/to/logo.png

# Update LinkedIn org cover
.devin/skills/social-accounts-manager/scripts/li-org-update-cover.sh /path/to/cover.png
```

### LinkedIn Personal

```bash
# Update LinkedIn personal headline
.devin/skills/social-accounts-manager/scripts/li-personal-update-headline.sh "Headline text"

# Update LinkedIn personal about section
.devin/skills/social-accounts-manager/scripts/li-personal-update-about.sh "About text"

# Update LinkedIn personal contact info (website)
.devin/skills/social-accounts-manager/scripts/li-personal-update-website.sh "https://cloudless.gr"

# Update LinkedIn personal profile picture
.devin/skills/social-accounts-manager/scripts/li-personal-update-picture.sh /path/to/image.png

# Update LinkedIn personal cover photo
.devin/skills/social-accounts-manager/scripts/li-personal-update-cover.sh /path/to/cover.png
```

### Instagram Business

```bash
# Login to Instagram sidecar (by sessionid or username/password)
.devin/skills/social-accounts-manager/scripts/ig-login.sh

# Update Instagram bio (150 char limit, emojis count as 2)
.devin/skills/social-accounts-manager/scripts/ig-update-bio.sh "Bio text"

# Update Instagram external URL
.devin/skills/social-accounts-manager/scripts/ig-update-url.sh "https://cloudless.gr"

# Update Instagram profile picture
.devin/skills/social-accounts-manager/scripts/ig-update-picture.sh /path/to/image.png

# Read Instagram profile
.devin/skills/social-accounts-manager/scripts/ig-read-profile.sh
```

## Important notes

- **Facebook Page About** is limited to 100 characters. Use the description field for longer text.
- **Instagram bio** is limited to 150 characters (emojis count as 2 each in Instagram's count).
- **LinkedIn org description** is limited to 2000 characters.
- **LinkedIn personal About** is limited to 2600 characters.
- **LinkedIn sidecar** requires a logged-in browser session. Use `POST /login` with credentials from the secret store.
- **Instagram sidecar** requires a valid sessionid. Use `login-via-facebook.sh` from the `instagram-profile-manager` skill or `POST /auth/login` with username/password.
- **Facebook personal profile** updates require the FB browser sidecar (Graph API doesn't support personal profile writes).
- **LinkedIn personal profile** writes require browser automation (LinkedIn API is read-only for profiles without Partner Program access).
- Never log or commit session IDs, tokens, or passwords.
