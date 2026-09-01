# LinkedIn API Demo Video Storyboard

Screenshots captured via Playwright MCP from the SocialAuto platform at
`http://localhost:8082`. Use these as a storyboard to record the demo video
required for the LinkedIn Marketing API developer access tier upgrade.

## Recording instructions

1. Use OBS Studio, Loom, or any screen recording tool
2. Record at 1280x720 or higher
3. Narrate each section as you walk through the platform
4. Upload to Google Drive with "anyone with the link can view" permission
5. Paste the link in the reply email to Rajeshwari

## Storyboard

### Scene 1: Introduction (10s)
**Screenshot:** `demo-01-landing.png`
**Narration:** "Hi, I'm Themistoklis from Cloudless. This is a demo of
SocialAuto, our social media automation platform that uses the LinkedIn API."

### Scene 2: Dashboard (15s)
**Screenshot:** `demo-01-dashboard.png`
**Action:** Show the main dashboard with overview stats
**Narration:** "SocialAuto provides a single dashboard to manage social media
across LinkedIn, Facebook, Instagram, Threads, Twitter/X, and TikTok."

### Scene 3: Connected Accounts (30s)
**Screenshot:** `demo-02-accounts.png`
**Action:** Scroll through the Accounts page showing all 6 connected platforms
**Narration:** "Here are our connected accounts. The LinkedIn integration uses
the Share on LinkedIn product for personal posts and the Community Management
API for Company Page posts. We can see the cloudless-gr LinkedIn organization
is connected and active."

### Scene 4: LinkedIn Content Creation (30s)
**Screenshot:** `demo-03-linkedin-content.png`
**Action:** Show the LinkedIn-specific content page
**Narration:** "The LinkedIn content page lets us generate AI-powered posts,
articles, hashtags, and comments optimized for LinkedIn's audience."

### Scene 5: Post Editor (30s)
**Screenshot:** `demo-04-content-new.png`
**Action:** Show the general post creation page
**Narration:** "The post editor allows us to create content with text, images,
and media, then select which platforms to publish to — including the LinkedIn
Company Page."

### Scene 6: Analytics (30s)
**Screenshot:** `demo-05-analytics.png`
**Action:** Show the analytics dashboard
**Narration:** "We use the r_organization_social scope to read LinkedIn post
analytics — impressions, clicks, engagement rates — and organization-level
statistics like follower counts."

### Scene 7: Brand Identity (20s)
**Screenshot:** `demo-06-brand.png`
**Action:** Show the Brand page
**Narration:** "Our brand identity system ensures all content maintains
consistent voice, tone, and visual style across every platform."

### Scene 8: AI Provider Settings (20s)
**Screenshot:** `demo-07-settings.png`
**Action:** Show the Settings page with AI providers
**Narration:** "We've configured 13 AI providers including Cloudflare Workers
AI, NVIDIA, Groq, and others for content generation and image creation."

### Scene 9: Media Library (20s)
**Screenshot:** `demo-08-media-library.png`
**Action:** Show the media library with carousel images
**Narration:** "The media library stores all generated images, carousels, and
assets used in our social media posts."

### Scene 10: Closing (10s)
**Narration:** "SocialAuto helps businesses manage their LinkedIn presence
efficiently through organic content publishing and analytics. We do not create
or manage LinkedIn Ads. Thank you for reviewing our upgrade request."

## Total estimated video length: ~3.5 minutes

## Key points to emphasize during recording

1. **Organic posting only** — we use `w_member_social` and
   `w_organization_social` for creating organic posts, not ads
2. **Analytics reading** — we use `r_organization_social` and
   `r_organization_admin` to read post and organization analytics
3. **No Ads API** — we do not use `rw_ads` or `r_ads` scopes
4. **Multi-client use case** — we need the standard tier to manage more than
   5 Company Pages for our clients
