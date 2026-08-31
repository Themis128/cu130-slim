# Brand Identity Setup

Set up your brand DNA, voice, and visual identity so every AI-generated post is on-brand.

## Prerequisites

- You have a SocialAuto account and team.
- You are logged in to the dashboard.

## Step 1 — Create your brand

1. Click **Brand** in the sidebar.
2. Click **Create Brand Manually**.
3. Fill in:
   - **Brand Name** (required) — e.g. `Cloudless`
   - **Industry** — e.g. `Cloud Infrastructure`
   - **Tagline** — e.g. `Clear skies. Zero friction.`
   - **Website URL** — e.g. `https://cloudless.gr`
4. Click **Create Brand**.

You will be redirected to the brand dashboard showing four completeness cards: Identity, Voice, Visual, and Guidelines.

## Step 2 — Define your brand identity

1. Click the **Brand Identity** card.
2. Fill in:
   - **Positioning Statement** — one sentence describing who you serve and what makes you different.
   - **Mission** — your brand's purpose.
   - **Brand Values** — type a value and press Enter to add it.
   - **Competitors** — add competitor names for future monitoring.
3. Click **Save Changes**.

## Step 3 — Configure your brand voice

1. Go to **Brand → Voice & Tone**.
2. Adjust the five **Tone Dimensions** sliders (1-5):
   - Formal vs Casual
   - Serious vs Playful
   - Humble vs Authoritative
   - Distant vs Friendly
   - Simple vs Technical
3. Add **Banned Phrases** — words the AI should never use (e.g. `synergy`, `game-changer`).
4. Add **Preferred Phrases** — words the AI should prefer (e.g. `zero friction`, `clear skies`).
5. Paste 1-2 paragraphs of **Example Content** that embodies your voice.
6. Click **Save Voice**.

## Step 4 — Set your visual identity

1. Go to **Brand → Visual Identity**.
2. Pick your **Primary Color** and **Accent Color** using the color pickers.
3. Add **Neutral Colors** for backgrounds and text.
4. Enter your **Heading Font** and **Body Font** (e.g. `Inter`).
5. Paste your **Logo URL** (upload to the Media Library first, then copy the URL).
6. Describe your **Image Style** and **Photography Direction** for AI image generation.
7. Click **Save Visual**.

## Step 5 — Compile and share brand guidelines

1. Go to **Brand → Brand Guidelines**.
2. Click **Compile** to generate a shareable guidelines document from your brand, voice, and visual settings.
3. Click **Copy Share Link** to get a public URL for sharing with your team or external partners.
4. Whenever you update your brand, voice, or visual, click **Recompile** to refresh the guidelines (version number increments automatically).

## What happens next

Once your brand is configured:

- **Phase 3** (coming soon): AI content generation will automatically inject your brand voice, banned phrases, and tone dimensions into every prompt.
- **Phase 3**: A brand compliance score (1-5) will appear in the post editor before publishing.
- **Phase 4**: Carousel themes will use your brand colors, fonts, and logo instead of hardcoded themes.

## API reference

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/v1/brand` | Get full brand profile |
| `POST` | `/api/v1/brand` | Create brand |
| `PUT` | `/api/v1/brand` | Update brand identity |
| `DELETE` | `/api/v1/brand` | Delete brand |
| `GET` | `/api/v1/brand/voice` | Get brand voice |
| `PUT` | `/api/v1/brand/voice` | Update brand voice |
| `GET` | `/api/v1/brand/visual` | Get brand visual |
| `PUT` | `/api/v1/brand/visual` | Update brand visual |
| `GET` | `/api/v1/brand/guidelines` | Get compiled guidelines |
| `POST` | `/api/v1/brand/guidelines/compile` | Compile guidelines |
| `GET` | `/api/v1/brand/assets` | List brand assets |
| `POST` | `/api/v1/brand/assets` | Add brand asset |
| `DELETE` | `/api/v1/brand/assets/{id}` | Delete brand asset |
