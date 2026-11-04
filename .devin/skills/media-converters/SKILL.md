---
name: media-converters
description: Lightweight media format converters for social posting — PDF→image, still→video with audio bed, and license-free synth audio generation. All run inside social-api via backend/scripts (fitz + ffmpeg + PIL already installed). Use when converting certificates/documents to post-ready media, adding sound to a still for a video post, or generating copyright-safe audio tracks.
---

# Media Converters

Platform tools for turning documents/stills into post-ready media. Canonical
home: `social-automation/backend/scripts/` — mounted at `/app/scripts` inside
`social-api` (and the workers), so everything runs in-container:

```bash
docker compose exec -T social-api python3 /app/scripts/<tool>.py ...
```

Host fallback works too (Git Bash `python` + `/usr/bin/ffmpeg` + pymupdf all
present on this workstation). Generated files persist to host when written to
`/app/pipeline-assets` (compose mount) — otherwise use `docker compose cp`.

## pdf_to_image.py — PDF → PNG/JPG

```bash
docker compose cp in.pdf social-api:/tmp/in.pdf
docker compose exec -T social-api python3 /app/scripts/pdf_to_image.py \
    /tmp/in.pdf --out /app/pipeline-assets/cert --zoom 2.5
# cert.pdf (1 page) -> pipeline-assets/cert.png ; multi-page -> cert-p1.png …
```

- `--zoom` render scale (default 2.5 ≈ 180 dpi, 2106×1489 for a cert)
- `--pages 1 3`, `--format jpg --quality 90`

## audio_bed.py — license-free synth audio bed

ffmpeg-only synthesis — **no samples, no copyright surface**. Presets:

| preset | feel | use for |
|---|---|---|
| `chime` | rising chime arpeggio | reveals, certificates, wins |
| `pad` | warm ambient chord | background under voice/stills |
| `whoosh` | filtered noise sweep | transitions |
| `tick` | quiet 1Hz pulse | countdowns |

```bash
docker compose exec -T social-api python3 /app/scripts/audio_bed.py \
    --preset chime --seconds 6 --volume 0.7 --out /app/pipeline-assets/bed.wav
```

## image_to_video.py — still → MP4 with audio

Ken Burns zoom over a dark blurred backdrop (nothing cropped), H.264
yuv420p, AAC, +faststart — same encode contract as
`gen_branded_video.py` (LinkedIn/TikTok-safe: ≤30fps, ≤1920px/side).

```bash
docker compose exec -T social-api python3 /app/scripts/image_to_video.py \
    /app/pipeline-assets/cert.png --out /app/pipeline-assets/cert.mp4 \
    --audio chime --seconds 6 --aspect 16:9
```

- `--audio chime|pad|whoosh|tick` = synth bed; `--audio /path/x.wav` = your
  file; `--audio none` = silent AAC track (platforms want a stream present)
- `--aspect 16:9|1:1|4:5|9:16` (default 16:9, letterboxes to fit)
- `--zoom` zoom delta (default 0.06), `--fps` (default 25), `--seconds`

## Chains

```bash
# certificate PDF -> LinkedIn video post
pdf_to_image cert.pdf -> image_to_video --audio chime -> upload /api/v1/media
# deck slide -> X/LinkedIn image post
pdf_to_image slide.pdf --pages 1 -> upload
```

After producing media, upload + post through the API (`socialauto-publish`
skill: `POST /api/v1/media/upload`, `POST /api/v1/content/posts`,
`publish-now`). Video posts satisfy the media-required rule only when the
audio track is present — keep `--audio` on unless the post is meant silent.

For multi-slide branded slideshows use `branded-video-pipeline` instead —
this skill is for single-asset conversion.

## Full post pipeline

`social_post.py` (backend/scripts, runs inside social-api) does the whole
chain in one command — convert -> upload -> create post -> publish -> poll:

    docker compose exec -T social-api python3 /app/scripts/social_post.py         --file /app/pipeline-assets/cert.pdf         --caption "..." --account <uuid> --sound --publish

PDF -> PNG, still + --sound -> 6s MP4 with synth audio, then upload,
create, publish-now, and poll until published/failed. Never posts
without media.
