---
name: ax-pass-ops
description: >
  Ingest the Cisco DevNet AX Pass / Shift.Forward post-event page and adopt
  reusable patterns into the stack. Drop the saved HTML (Ctrl+S) or pasted
  text into Downloads — extracts links/resources/frameworks, maps hits to
  stack surfaces (n8n/mcp/skills/worker/datalake), and emits adopt
  candidates as JSON. The page is attendee-auth + client-side rendered, so
  a saved file is the ingest path when the shared bridge is contended.
  Tool: scripts/ax_pass_ingest.py.
---

# AX Pass Ops — Shift.Forward post-event ingest

`axpass.outshift.com/after` is attendee-auth + client-rendered — the
bridge can't always reach it. Ingest via file-drop:

    ax_pass_ingest.py            # newest axpass/shiftforward file in Downloads
    ax_pass_ingest.py --watch    # poll like audience_watch

Output: `{"links": [...], "stack_hits": [...], "text_head": "..."}` →
then adopt: session links go to the datalake, framework mentions map to
skill/worker surfaces, tool references get skills built.
