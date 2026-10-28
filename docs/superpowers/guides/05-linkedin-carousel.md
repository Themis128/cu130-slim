# LinkedIn carousel

Generate a multi-slide PDF carousel and publish it to the cloudless.gr Company Page. This flow is locked to Cloudflare Workers AI.

## 1. Prepare the topic

1. Go to **Content > LinkedIn carousel**.
2. Enter a topic or prompt, e.g. `5 tips for a cloudless social strategy`.
3. Select the number of slides (3-10).
4. Choose the tone: **Professional**, **Casual**, or **Technical**.

## 2. Generate the slides

1. Click **Generate carousel**.
2. The pipeline does the following:
   - Writes the copy in plain English.
   - Generates each slide with Cloudflare FLUX schnell.
   - Composes a branded slide layout.
   - Builds a single PDF from the slides.
3. Wait for the status indicator to show **Generated**.

## 3. Review and edit

1. A preview of each slide appears.
2. Click a slide to edit its text or regenerate its image.
3. *Optional:* add alt text for accessibility.

## 4. Publish to Company Page

1. Make sure the **cloudless.gr Company Page** account is selected.
2. Click **Publish now** or **Schedule**.
3. The worker posts the PDF as a LinkedIn document post using the Company Page URN.
4. After publishing, a `platform_url` is shown. Click it to open the post on LinkedIn.

## 5. Automate with n8n

1. The n8n workflow `cloudless-cf-carousel-linkedin` can trigger this flow on a schedule.
2. Go to **Workflows > cloudless-cf-carousel-linkedin**.
3. Set the schedule (default: every 2 days).
4. Enable the workflow.

## Limits

- Cloudflare Workers AI free quota: ~10,000 neurons/day.
- Carousel generation falls back if quota is exhausted, but it will always try Cloudflare first.
- Only one Company Page can be the default target for the Cloudless pipeline.
