# Weekly blog article (DMR → cloudless.gr/blog)

SocialAuto generates a full blog article with the local DMR model and
publishes it straight to the cloudless.gr blog, then posts a LinkedIn
company-page update that links to the article. This powers the
**Weekly Cloud Computing Trends** edition every Monday 09:00 Athens.

## How it works

```
n8n "Weekly Cloud Computing Post" (Mon 09:00 Athens)
  └─ Generate Blog Article  → POST /api/v1/ai/generate-blog-article
  │     DMR (platform=blog → 8B long-form model) writes the article,
  │     uploads JSON to the datalake R2 bucket:
  │       newsletter/articles/{slug}.json
  │     and returns { slug, url, title, excerpt, social_post }
  └─ Run Weekly Post        → POST /api/v1/content/posts
        uses social_post as content_text and the article url as link_url
  └─ Publish Now            → POST /api/v1/content/posts/{id}/publish-now
```

The site reads `newsletter/articles/*.json` from the datalake bucket
(`src/lib/blog-r2.ts` in cloudless.gr). The article page renders the
`content` markdown-ish field (intro, `## ` headings, `- ` lists,
`**bold**`, `` `code` ``, `[text](url)`); `html` is used for newsletter
emails only.

## Generate an article manually

```bash
POST /api/v1/ai/generate-blog-article
{
  "topic": "Weekly Cloud Computing Trends",
  "publish": true,                // false = preview without writing to R2
  "slug": null,                   // optional; default {YYYY-MM-DD}-{topic-slug}
  "extra_context": "optional angle or notes to work in"
}
```

Response:

```json
{
  "slug": "2026-09-28-weekly-cloud-computing-trends",
  "url": "https://cloudless.gr/blog/2026-09-28-weekly-cloud-computing-trends",
  "title": "Weekly Cloud Computing Trends — September 28, 2026",
  "excerpt": "...",
  "category": "Cloud",
  "read_time": "6 min read",
  "created": true,
  "social_post": "..."
}
```

## Idempotency

The default slug is date-prefixed, so a same-day re-run returns the
already-published article (`created: false`) with its stored `socialPost` —
workflow retries can never duplicate an article or re-spend inference.

## Content format contract

The generator normalises model output to the exact shape the blog page
renders (`normalize_article_markdown` in `app/services/blog_articles.py`):

- No `# ` title line (title comes from metadata)
- Only `## ` section headings — deeper headings are downgraded
- Blank lines forced around headings and bullet-list blocks
- `socialPost` is cleaned of invented links — the canonical URL travels via
  the post's `link_url` field instead

Valid categories: `Cloud`, `Serverless`, `Analytics`, `AI Marketing`.

## Requirements

- `DATALAKE_R2_BUCKET` (default `datalake-bucket`), `CLOUDFLARE_ACCOUNT_ID`,
  `CLOUDFLARE_API_TOKEN` — same creds the 6h datalake export uses.
- DMR reachable at `DMR_URL` with the `DMR_TEXT_MODEL` pulled
  (`ai/qwen3:8b-q4_K_M`).

## Troubleshooting

- `503 DATALAKE_R2_BUCKET is not configured` — check the env vars above in
  the social-api container.
- `502 AI did not return a usable article` — DMR returned empty/unparseable
  JSON; check `docker model ps` and social-api logs.
- Article saved but not visible on the blog — the blog index merges R2 +
  static + AppFlowy sources with a 5-minute revalidation window.
