# LinkedIn API Demo — Full Walkthrough

Recorded via Playwright MCP against the live SocialAuto platform at
`http://localhost:8082`. 10 screenshots covering the complete LinkedIn
integration flow: login, accounts, content creation, draft saving,
calendar, analytics, brand identity, and AI provider settings.

## How to record the video

1. Open OBS Studio / Loom / any screen recorder at 1280x720
2. Walk through each step below, narrating as you go
3. Upload to Google Drive with "anyone with the link can view"
4. Paste the link in the reply email to Rajeshwari

## Demo script (10 steps, ~3.5 min total)

### Step 1: Dashboard (15s)
**Screenshot:** `demo-step-01-dashboard.png`
**Action:** Show the main dashboard
**Narration:** "Hi, I'm Themistoklis from Cloudless. This is SocialAuto,
our social media automation platform that uses the LinkedIn API. Here's
the main dashboard showing overview stats across all our connected
social accounts."

### Step 2: Connected Accounts (30s)
**Screenshot:** `demo-step-02-accounts-overview.png`
**Action:** Scroll through the Accounts page
**Narration:** "SocialAuto connects to 6 social platforms. The LinkedIn
integration uses the Share on LinkedIn product for personal profile
posts and the Community Management API for Company Page posts. We can
see the cloudless-gr LinkedIn organization is connected and active, as
well as a personal LinkedIn account."

### Step 3: LinkedIn Content Page (15s)
**Screenshot:** `demo-step-03-linkedin-content.png`
**Action:** Show the LinkedIn-specific content page
**Narration:** "The LinkedIn content page provides AI-powered tools for
generating LinkedIn-optimized posts, articles, hashtags, and comments."

### Step 4: Post Editor — Platform Selection (20s)
**Screenshot:** `demo-step-04-post-editor.png`
**Action:** Show the post editor with all platform options
**Narration:** "The post editor lets us create content and select which
platforms to publish to. We can choose Post, Carousel, Thread, Poll,
Story, or Article formats. Here I'm selecting LinkedIn as the target
platform."

### Step 5: Writing LinkedIn Content (20s)
**Screenshot:** `demo-step-05-linkedin-post-content.png`
**Action:** Show the text content typed into the editor
**Narration:** "I'm writing a post about Cloudless serverless solutions.
The editor shows a live preview of how the post will appear on LinkedIn.
We can also use AI to generate content with brand-aligned tone."

### Step 6: Saving as Draft (15s)
**Screenshot:** `demo-step-06-draft-saved.png`
**Action:** Click "Save Draft" and show confirmation
**Narration:** "I save the post as a draft. The post is stored and can
be scheduled or published later."

### Step 7: Content Calendar (15s)
**Screenshot:** `demo-step-07-calendar.png`
**Action:** Show the calendar with scheduled/draft posts
**Narration:** "The content calendar shows all scheduled and draft posts
across platforms, making it easy to plan our LinkedIn content strategy."

### Step 8: LinkedIn Analytics (30s)
**Screenshot:** `demo-step-08-analytics.png`
**Action:** Show the analytics dashboard
**Narration:** "We use the r_organization_social scope to read LinkedIn
post analytics — impressions, clicks, engagement rates — and
organization-level statistics like follower counts. This data helps us
measure the impact of our LinkedIn content."

### Step 9: Brand Identity (20s)
**Screenshot:** `demo-step-09-brand.png`
**Action:** Show the Brand page
**Narration:** "Our brand identity system ensures all LinkedIn content
maintains consistent voice, tone, and visual style. The brand DNA
includes positioning, messaging pillars, and tone dimensions that are
injected into AI-generated content."

### Step 10: AI Provider Settings (20s)
**Screenshot:** `demo-step-10-settings.png`
**Action:** Show the Settings page with AI providers
**Narration:** "We've configured 13 AI providers including Cloudflare
Workers AI, NVIDIA, Groq, Gemini, and others for content generation and
image creation. This gives us redundancy and cost optimization."

## Closing statement (10s)
**Narration:** "SocialAuto helps businesses manage their LinkedIn
presence through organic content publishing and analytics. We do not
create or manage LinkedIn Ads. We're requesting the standard tier
upgrade to support managing more than 5 Company Pages for our clients.
Thank you for reviewing our request."

## Key points for the email reply

1. **Organic posting only** — `w_member_social` + `w_organization_social`
2. **Analytics reading** — `r_organization_social` + `r_organization_admin`
3. **No Ads API** — no `rw_ads` or `r_ads` scopes
4. **Multi-client need** — standard tier needed for >5 Company Pages
