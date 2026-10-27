# Funnel + Media Architecture (2026-10-25)

## Lead funnels — one spine

```
Social CTA → cloudless.gr/survey → /api/survey forwarder (CF Access, server-side)
  → POST /api/v1/leads/public/survey → Postgres lead + meta_data
  → EspoCRM upsert → nurture_sequence Celery sweep (day 3/7/14)
```

- **Survey**: email-validated, phone E.164 + explicit consent, honeypot silent-success,
  `meta_data.platform` (linkedin/x/threads/instagram/facebook/tiktok) drives
  `PLATFORM_SUBJECTS/OPENERS` — "from your TikTok comment" style openers.
- **Audit**: `POST /leads/public/audit` — live score + issues, lead-magnet entry.
- **Chatbot**: book_slot → calendar + `form:"chatbot-booking"` lead.
- **Newsletter**: subscriber scrape → EspoCRM (source "Newsletter").
- **Contact/playbook**: `/api/playbook-lead` forwarder.
- Full docs mirrored on Nextcloud `Funnels/` (cloud.cloudless.gr).

## Media pipeline — upload → AI ops → publish

Upload: `.png .jpg .webp .gif .avif .heic` + `.mp4 .webm .mov .mkv .avi` + `.mp3 .wav .aac .m4a .ogg .flac`

- Image ops (`media/enhance`): resize, crop, convert, compress, watermark,
  upscale, remove-background, smart-crop, alt-text, batch, quality-score.
- Video ops (`media/enhance/assets/{id}/video/*`): trim, convert (mp4/webm/mov),
  attach-audio (mux music, loops under video — enforces the
  "posts must carry sound/music" rule), mute, extract-audio.
- CLI: `.devin/skills/socialauto-media/scripts/video-ops.py`.
- Publish: `media_ids` on post → 4-worker publish queue → all platforms.

## Platform-aware nurture

`services/nurture_sequence.py`: `PLATFORM_SUBJECTS`/`PLATFORM_OPENERS` keyed by
`lead.meta_data["platform"]`, sequential day-3/7/14 via `_due_step`,
opt-out on `unsubscribed`/`nurture_opted_out`, per-lead failure isolation.

## Governance

Implementation agents open PRs (no direct deploys); proposals draft-for-approval;
risky/paid/platform-verification/account-security actions are human-gated —
live list via `stack-doctor` human-gates report.
