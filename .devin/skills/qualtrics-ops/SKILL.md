---
name: qualtrics-ops
description: Fill Qualtrics surveys via the adhoc browser bridge — label→field
  matching (Qualtrics hides input ids), radio-click by choice index, Next-page
  navigation. Use for access-request forms like the LinkedIn CM-API upgrade.
  Tool: scripts/qualtrics_fill.py.
---

# qualtrics-ops — label-driven form fill

- Qualtrics fields are anonymous (`input` without id/name) — match by **label text**, never id.
- Radios: `mc-choice-input-QIDx-y` → click the y-th choice (1-indexed value in answers.json).
- Pages: loop fill → `Next` button → repeat until Submit; `--dry` stops before first Next.

```bash
qualtrics_fill.py --url <form> --answers answers.json          # fill all pages
qualtrics_fill.py --url <form> --answers a.json --dry          # inspect only
```

`answers.json`: `{"<label substring>": "<text>"}` or `{"<label>": "<choice idx>"}`.
The LinkedIn CM-API answers are drafted in the session — paste them into an
answers.json and the script fills the form; review + Submit stays manual.
