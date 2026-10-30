---
name: content-quality-ops
description: The content-quality layer — content_sanitizer extensions —
  LLM-slop keyword scan (delves/leverage/showcasing/openers), voice-constraint
  check (slop markers + run-on sentences), near-duplicate gate. Wire into the
  generation path, not just publish. Companion to
  app/services/content_sanitizer.py (slop_scan, voice_check,
  content_quality_gate, sanitize_post_text).
---

# Content quality ops

Generation-side gates from the 2026 AI-slop research: `slop_scan` flags
LLM-favorite filler, `voice_check` enforces slop-markers + sentence-ceiling,
`content_quality_gate` rejects near-duplicates vs recent posts. Sanitizer
handles emoji + connective cleanup at publish.
