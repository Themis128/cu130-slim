# Carousel pipeline reference

## Endpoint contract

`POST http://localhost:8083/api/v1/ai/run-carousel-and-publish`

Auth: Bearer from `POST /api/v1/auth/login` (form: `username`, `password`).

```json
{
  "topic": "How cloudless.gr helps teams ship serverless apps",
  "num_slides": 7,
  "tone": "clear and friendly",
  "include_cta": true,
  "text_model": "@cf/meta/llama-3.2-3b-instruct",
  "txt2img_model": "@cf/black-forest-labs/flux-1-schnell",
  "img2img_model": "@cf/runwayml/stable-diffusion-v1-5-img2img",
  "strength": 0.42,
  "target_account_id": "4a8d9440-47d2-4bda-bd11-3776fd9022ba",
  "publish": true
}
```

Response includes `post_id`, `media_ids`, `slides`, `nlp_report`, `target_account`, `status`, optional `platform_url`.

## Pipeline stages

1. LLM slide copy (JSON schema) — default free-tier text model
2. `run_nlp_check_and_fix(..., force_fix=True)` + **duplicate content detector**
3. Per slide: CF FLUX schnell (4 steps) → SD v1.5 img2img (8 steps); draft-only if enhance fails
4. Brand compose (single NLP text line when title/body overlap)
5. Create `Post` + `PostTarget` for org account
6. If `publish`: Celery `publish_post_now` + optional poll

Defaults live in `app/services/cf_models.py`. All Workers AI models share the same **10,000 neurons/day** free pool — switching models does not bypass a 429 quota error.

Duplicate detector (`app/services/duplicate_detector.py`): stem/Jaccard/sequence similarity; collapses title↔body↔highlight and cross-slide twins. Results land in `nlp_report.duplicates`.

## Common failures

| Symptom | Fix |
|---------|-----|
| `varchar(20)` on media source | Shorten `source=` (max 20 chars) |
| LinkedIn text-only / no carousel | Restart `social-worker`; check media path join with `UPLOAD_DIR` |
| Document status 400 | URL-encode document URN |
| FLUX 400 unexpected props | Drop width/height/guidance; use `steps` |
| SD img2img 429 | Fall back to FLUX.2 klein-4b |
| Personal profile posts | Ensure target is org UUID; `_linkedin_author_urn` uses organization |
| Redis/Celery refused | `docker compose ps redis social-worker` |
