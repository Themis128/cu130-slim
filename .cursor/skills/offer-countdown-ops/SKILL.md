---
name: offer-countdown-ops
description: Live countdown token for time-boxed campaign posts (Black Friday etc.) — `{countdown:<ISO-8601>}` and bare `{countdown}` expand to "N days, H hours, M minutes" inside render_post_text at publish time; bare form reads post.meta_data["offer_ends_at"]. Covers token semantics, the COUNTDOWN_RULE generation-side guide, DB bookkeeping for existing posts, and the IG caption-edit lane (Graph API can't edit organic captions — use ig-takedown-ops/ig_edit.py).
---

# Offer countdown ops

Owner rule 2026-11-22: every post for a time-boxed campaign (Black Friday etc.)
carries a live countdown — days, hours, minutes to the offer end — computed at
publish, never a static "this weekend". Black Friday 2026 deadline:
**2026-11-27T23:59:00+02:00 (EET)**.

## The token

Two forms, both expanded inside `render_post_text`
(`app/services/content_renderer.py` → `_expand_countdown_tokens`) at publish
time, so scheduled posts always show the real remaining time:

| Form | Deadline source |
|---|---|
| `{countdown:2026-11-27T23:59:00+02:00}` | inline ISO-8601 (always write the offset) |
| `{countdown}` | `post.meta_data["offer_ends_at"]` |

Semantics:

- Naive deadline → treated as UTC. Always include `+02:00` (EET winter) or
  `+03:00` (EEST summer).
- Past deadline → renders `expired`.
- Unparseable / missing deadline → token left untouched (never crashes render).
- Expansion covers both `content_text` and per-platform `platform_specific`
  overrides.

## Generation side

`COUNTDOWN_RULE` in `app/services/content_guides.py` is injected into
`generate_content` prompts (`app/api/ai.py`): the model emits the token
verbatim whenever the brief names a deadline, and omits it entirely otherwise.

## Setting up a campaign

1. Put the token in the post copy — via generation (automatic once the brief
   names the deadline) or hand-written drafts.
2. For bare `{countdown}`, set the deadline once on the post:
   ```sql
   UPDATE posts SET meta_data = COALESCE(meta_data,'{}'::jsonb)
     || '{"offer_ends_at":"2026-11-27T23:59:00+02:00"}'::jsonb
     WHERE id = '<post-id>';
   ```
3. Verify the render in-container:
   ```bash
   docker exec social-api python3 - <<'PY'
   import sys; sys.path.insert(0,'/app')
   from app.services.content_renderer import _expand_countdown_tokens
   print(_expand_countdown_tokens('Only {countdown:2026-11-27T23:59:00+02:00} left'))
   PY
   ```

## Editing an already-live IG caption

Graph API has no caption edit for organic media. Use
`.devin/skills/ig-takedown-ops/scripts/ig_edit.py` — reuses the session→2FA→
instagrapi chain (`ig_delete.py`): bridge `/session/start` on instagram →
remembered cloudless.gr profile → WhatsApp Business SMS code via ADB →
`login_by_sessionid` → `media_edit`. A caption edit keeps the post URL and
never trips the 30-day same-media dedup guard.

Caption gotcha: IG captions are static once posted — write the computed
remaining time AND the explicit end date ("ends Friday Nov 27, 23:59 EET") so
it stays meaningful between edits.

## Tests

`tests/unit/test_content_renderer.py::TestCountdownToken` — 8 cases covering
expansion math, render wiring, expired, naive→UTC, unparseable passthrough,
and the bare-token meta lookup. Runtime images ship without pytest; run on a
dev checkout (`pip install pytest` then `pytest tests/unit/test_content_renderer.py`).
