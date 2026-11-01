---
name: media-copy-gate
description: Pre-render copy QA — gate slide/media text on spelling +
  NLP + SEO BEFORE the image pipeline consumes it. Catches "Baklava"-style
  proper-noun mangles and low-score copy at the copy stage, not after the
  pixels are baked. Tool: scripts/media_copy_gate.py — `check <file>` or
  stdin, returns pass/fail + the corrected text.
---
# Media copy gate — pre-render spelling/NLP/SEO
