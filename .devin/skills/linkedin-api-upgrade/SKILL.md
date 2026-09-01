---
name: linkedin-api-upgrade
description: >-
  Manage the LinkedIn Marketing API developer access tier upgrade process.
  Use when responding to LinkedIn developer-access emails, preparing business
  use case descriptions, demo video scripts, or tracking the 14-business-day
  review window. Covers the development → standard tier upgrade for the
  Community Management API and Share on LinkedIn products.
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

# LinkedIn API Developer Access Tier Upgrade

Manage the LinkedIn Marketing API upgrade process from **development tier** to
**standard tier** for the Cloudless SocialAuto platform.

## Context

LinkedIn Marketing API has three access tiers:

1. **Development** — limited to 5 members, 5 pages, 5 ad accounts. Sufficient
   for testing and small-scale use.
2. **Standard** — removes the 5-account limit for POST operations (creating
   ads). No limit on GET/data/analytics calls across any number of accounts.
3. **Direct** — enterprise tier, requires direct LinkedIn partnership.

**Key finding from LinkedIn's email:** The tier upgrade from development to
standard **only affects POST ad creation for more than 5 ad accounts**. There
is no limit on the number of accounts you can call to retrieve
data/analytics. If you are NOT creating ads for more than 5 ad accounts, the
upgrade may not be necessary.

## SocialAuto's LinkedIn API usage

SocialAuto uses the following LinkedIn API products:

### Share on LinkedIn (`w_member_social`)
- Create organic posts on behalf of authenticated members (personal profiles)
- Create multi-image posts and article posts
- Delete posts

### Community Management API (`w_organization_social`, `r_organization_social`)
- Create organic posts on behalf of Company Pages (the cloudless.gr carousel pipeline)
- Read organization/page data
- Read post analytics (impressions, clicks, engagement, etc.)
- Read follower counts and lifetime organization stats

### Organizations API (`r_organization_admin`)
- Discover Company Pages the member administers
- Read organization profile data

### What SocialAuto does NOT do
- **No LinkedIn Ads creation** — SocialAuto does not use the Marketing API's
  ad campaign, ad account, or sponsored content endpoints
- **No `rw_ads` or `r_ads` scopes** — these are not requested during OAuth
- **No Campaign Manager integration** — no ad campaign creation, editing, or
  optimization

## Upgrade assessment

Given that SocialAuto does not create LinkedIn Ads:

- **If the goal is to manage more than 5 Company Pages** for organic posting
  and analytics → the upgrade IS needed because `w_organization_social` POST
  calls (creating posts as a page) are limited to 5 pages in development tier.
- **If the goal is only to read analytics** from more than 5 pages → the
  upgrade is NOT needed (LinkedIn confirmed no limit on GET/data calls).
- **If the goal is to create ads** for more than 5 ad accounts → the upgrade
  IS needed, but SocialAuto does not currently have ad creation functionality.

## Required submission materials

LinkedIn requires two items for the upgrade review:

### 1. Business use case description

A written description of:
- What the app offers to customers
- How it leverages the LinkedIn API
- Which specific endpoints/products are used

See `scripts/generate-use-case.sh` to generate this automatically from the
codebase, or use the template below.

### 2. Demo video recording

A screen recording showing:
- The SocialAuto platform UI
- How a user connects their LinkedIn account (OAuth consent flow)
- How a user creates and publishes a post to a LinkedIn Company Page
- How a user views LinkedIn analytics
- (If applicable) How the carousel pipeline generates and publishes content

The video must be shared via Google Drive or Microsoft SharePoint link.

**Demo video script template:**

```
1. Introduction (10s)
   "Hi, I'm Themistoklis from Cloudless. This is a demo of our social media
   automation platform, SocialAuto, which uses the LinkedIn API."

2. Account connection (30s)
   - Show the Accounts page
   - Click "Connect LinkedIn"
   - Show the LinkedIn OAuth consent screen
   - Show the connected account appearing in the list

3. Creating a post (60s)
   - Show the post editor
   - Write a post with text and an image
   - Select the LinkedIn Company Page as the target
   - Click "Publish"
   - Show the post appearing on the LinkedIn Company Page

4. Analytics (30s)
   - Show the Analytics page
   - Show LinkedIn post analytics (impressions, engagement)
   - Show organization-level stats

5. Carousel pipeline (60s) [optional]
   - Show the AI carousel generator
   - Generate a carousel about a topic
   - Show the branded slides
   - Publish to the LinkedIn Company Page

6. Closing (10s)
   "SocialAuto helps businesses manage their LinkedIn presence efficiently.
   Thank you for reviewing our upgrade request."
```

Record with OBS, Loom, or any screen recording tool. Upload to Google Drive
with "anyone with the link can view" permission.

## Reply email template

```
Hi Rajeshwari,

Thank you for the update on our upgrade request.

Business use case:

Cloudless operates SocialAuto, a social media automation platform that helps
businesses manage their presence across LinkedIn, Facebook, Instagram,
Threads, Twitter/X, and TikTok from a single dashboard.

Our LinkedIn integration leverages the following API products:

1. Share on LinkedIn (w_member_social) — allows users to create and publish
   organic posts on their personal LinkedIn profiles, including text posts,
   multi-image posts, and article posts.

2. Community Management API (w_organization_social, r_organization_social) —
   allows users to create and publish organic posts on behalf of LinkedIn
   Company Pages they administer, and read post analytics (impressions,
   clicks, engagement rates) and organization-level statistics (follower
   counts, lifetime analytics).

3. Organizations API (r_organization_admin) — allows us to discover which
   Company Pages a member administers so they can select the correct page
   when publishing.

Our platform does NOT create, edit, or manage LinkedIn Ads or sponsored
content. We do not use the rw_ads or r_ads scopes. Our use case is entirely
organic content publishing and analytics.

We are requesting the standard tier upgrade to support managing more than 5
Company Pages for organic posting, as we work with multiple business clients
who each have their own LinkedIn Company Page.

Demo video:
[INSERT GOOGLE DRIVE LINK HERE]

Please let me know if you need any additional information.

Best regards,
Themistoklis Baltzakis
Cloudless
https://cloudless.gr
```

## Review timeline

- LinkedIn states the review can take up to **14 business days**
- Reply directly to the existing email thread (do not email
  developer-access@linkedin.com separately)
- LinkedIn will follow up with status updates

## Tool scripts

```bash
# Generate the business use case description from the codebase
.devin/skills/linkedin-api-upgrade/scripts/generate-use-case.sh

# Check which LinkedIn scopes are currently configured
.devin/skills/linkedin-api-upgrade/scripts/check-scopes.sh
```
