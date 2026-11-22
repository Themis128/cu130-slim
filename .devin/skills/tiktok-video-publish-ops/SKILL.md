---
name: tiktok-video-publish-ops
description: >-
  End-to-end runbook for publishing ONE video to TikTok through SocialAuto:
  account health (expired vs active rows), creator_info prerequisites, the
  >=60s duration rule, media upload + probe verification, draft -> verify ->
  publish-now -> post_targets poll, and what DIRECT_POST gating /
  SEND_TO_USER_INBOX actually mean. Use whenever uploading or diagnosing a
  TikTok video post. Content rules live in tiktok-content-ops; token/API
  diagnostics in tiktok-api-ops.
---

# TikTok video publish ops

## Account selection — always verify live, rows drift

`social_accounts` currently holds THREE tiktok rows for the same profile:

| id prefix | profile | status (2026-11-15) |
|---|---|---|
| `dcda73fb-…` | @cloudless.gr | **expired** — 401 access_token_invalid |
| `ab439e1e-…` | @cloudless.gr (display: "Cloudless \| Managed Cloud") | active, `video.publish`+`video.upload` |
| `7c33ed15-…` | same open_id as ab439e1e (duplicate row) | active |

Check first — never publish to expired rows:

```bash
docker exec social-api python3 /app/scripts/tiktok_tool.py accounts
docker exec social-api python3 /app/scripts/tiktok_tool.py -a <prefix> validate
docker exec social-api python3 /app/scripts/tiktok_tool.py -a <prefix> creator
```

`creator` (required before publishing per TikTok rules) returns
`max_video_post_duration_sec: 3600` and
`privacy_level_options: [PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS, SELF_ONLY]`
— the app IS audited for public posts.

## Video eligibility (Cloudless rules)

- **>= 60 seconds** — hard owner rule (2026-11-15); sub-minute clips are ineligible.
- 9:16 vertical (or converted via media ops / ffmpeg letterbox-on-blur).
- Must carry an audio stream (silent video is rejected by the platform gate;
  synth license-free bed is acceptable — `scripts/audio_bed.py` when the
  container ffmpeg has the `sawtooth` filter; ffmpeg 7.1.5 lacks it → use
  sine sources or host-side imageio-ffmpeg instead).
- h264 / yuv420p / aac, <=30fps, +faststart.
- On-brand, real content — no third-party captures, no internal-looking
  material, no invented metrics on slides.
- Duplicate guard: never republish a media/post that already hit this
  profile (check `post_targets` history on BOTH account rows — they share
  one open_id/profile).
- New derivative cuts must be genuinely different, not a repackage of a
  published piece.

## Pipeline — through the platform, never direct TikTok calls

1. Upload: `POST /api/v1/media/upload` (multipart `file`, optional
   `alt_text`). Response carries probed metadata — VERIFY:
   `duration_seconds >= 60`, `width:height == 9:16`, `mime_type video/mp4`,
   `meta_data.video.has_audio == true`, `video_codec h264`, `fps`.
2. Draft: `POST /api/v1/content/posts` with `content_text`, `media_ids`,
   `target_account_ids`, `status:"draft"`, and
   `platform_specific: { tiktok: { privacy_level: "PUBLIC_TO_EVERYONE" } }`.
   Add `"is_aigc": true` inside `platform_specific.tiktok` for AI-generated
   clips. Default `publish_mode` is DIRECT_POST — do not override.
3. Verify the stored draft: `GET /api/v1/content/posts/{id}` — caption,
   target, target status `pending`.
4. Publish: `POST /api/v1/content/posts/{id}/publish-now` → status becomes
   `scheduled`; the publishing worker runs `publish_post_now` then the
   platform calls TikTok.
5. Poll `post_targets` until `published` or `failed`:

   ```sql
   SELECT status, platform_post_id, platform_url, error_message
   FROM post_targets WHERE post_id='<id>';
   ```

## Interpreting the result — publish_mode matters more than target status

Check `posts.platform_specific.tiktok` after publish:

- `publish_mode: "DIRECT_POST"`, status `PUBLISH_COMPLETE` → live on the
  profile; `platform_post_id` is the TikTok item id.
- `publish_mode: "MEDIA_UPLOAD"`, status `SEND_TO_USER_INBOX`, publish_id
  `v_inbox_file~v2.…` → TikTok gated DIRECT_POST
  (`unaudited_client_can_only_post_to_private_accounts`) and the platform
  fell back to an **inbox draft**: the video sits in the creator's TikTok
  app inbox and must be finished manually in the app. `post_targets.status`
  still reads `published` — that means "handed to TikTok", NOT "live".
- `v_inbox*` / `v_upload*` publish ids = inbox uploads; DIRECT_POST ids do
  not carry that prefix.

## Finishing an inbox upload on the phone (proven 2026-11-22)

`scripts/tiktok_inbox_finish.py` automates the safe parts (`--probe` maps
the screen, `--watch` waits for a safe phone). Manual runbook when driving
by hand — every step verified live:

1. **Safety gate**: never tap while `com.facebook.katana`, WhatsApp,
   Telegram, Instagram or Termux holds focus; abort on cover/lockscreen.
2. Launch TikTok → **Inbox** → tap the "content from Cloudless is ready"
   notification → **Next** (bottom-right of the editor).
3. Post screen: caption field `id=hgl`, **Post** button `id=twl`.
   - The Post bar is HIDDEN while the IME/hashtag-suggestion strip is up.
     `mDismissImeOnBackKeyPressed=false` — **Back exits the composer**
     (draft is saved, but you lose the screen). To dismiss: tap the
     video-preview area (~860,430) instead.
4. Typing the caption over ADB — hard-won rules:
   - `input text` decodes `%s` as space; `%20`/`%23` stay **literal**.
   - `#` must be sent as a single quoted shell arg:
     `adb shell "input text '#Tag%s#Tag2'"` — unquoted `#…` returns
     "Argument expected after text".
   - Newlines: `input keyevent 66` between `input text` calls.
   - Clearing a long field: `keyevent 123` (MOVE_END) then ~1.5×
     char-count × `keyevent 67` (DEL) — DEL is ~3 chars/sec, budget it.
   - ASCII only — em-dashes break `input text`.
5. Verify via API, not the UI: `docker exec social-api python
   scripts/tiktok_tool.py status '<publish_id>'` → `PUBLISH_COMPLETE` +
   `publicaly_available_post_id` means live. The inbox notification
   lingers after posting — it is NOT proof of failure.

## Quality gate

The publish path runs a content quality gate (`overall` vs `min_score`,
recorded in `platform_specific.quality_gate`). Do not bypass; a failing
gate means fix the content, not the gate.

## Known env gotchas (2026-11-15)

- Container ffmpeg 7.1.5 has NO `sawtooth` filter → `scripts/audio_bed.py`
  fails (exit 8). Render beds with `sine` sources or on the Windows host
  via `imageio-ffmpeg` (`pip install imageio-ffmpeg`; binary ships inside
  the wheel — `imageio_ffmpeg.get_ffmpeg_exe()`).
- `scripts/social_post.py` login payload is stale (sends `email`; endpoint
  wants OAuth2 `username`/`password` form fields) — log in manually until
  the script is fixed.
- Stack dead from fsbridge poison → see `stack-rescue-ops` for the
  baked-image rescue path that keeps this whole pipeline usable.
