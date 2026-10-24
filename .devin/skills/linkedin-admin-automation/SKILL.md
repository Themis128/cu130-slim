# LinkedIn Admin UI Automation (sidecar)

Proven techniques for driving LinkedIn's Ember/ArtDeco admin surfaces via
`linkedin-browser-sidecar` `/debug/*` endpoints. Distilled from
github.com/negrueu/linkedin-company-admin-mcp (validated flows) plus live
testing on company page 108614163.

## Debug endpoint primitives (server.js, PR #837/#842)

`/debug/navigate` {url} · `/debug/eval` {expr} · `/debug/click` {selector} ·
`/debug/type` {selector?, text, delay?, force?} · `/debug/press` {key} ·
`/debug/screenshot` → `/data/debug-shot.png` in container.

Reach it via `http://linkedin-browser-sidecar:9225` from `social-api`
(`docker exec -i social-api python - <<EOF ...`).

## The golden rules (learned live)

1. **The field may live on another tab.** Specialties pill input is on the
   **Details tab**, not Page info. Click the tab by text
   (`a,button` with innerText === 'Details') — `?editPageActiveTab=` URL param
   alone does not always lazy-render the fields.
2. **Scroll every scrollable ancestor + window** to force lazy render BEFORE
   looking for inputs: `[...document.querySelectorAll('*')].filter(e=>
   e.scrollHeight>e.clientHeight+300&&e.clientHeight>400).forEach(s=>s.scrollTop=s.scrollHeight)`.
   The pill input is `input[id*=pill__input]` — invisible until its section
   renders.
3. **Real keystrokes only.** `/debug/type` (page.keyboard.type) lands text;
   `.value=` and synthetic events never flip Ember's dirty flag.
4. **Typeahead accept = ArrowDown then Enter** (same pattern as Industry).
5. **Save = visible `button` with innerText 'Save'** — post-save LinkedIn shows
   a "Share your page edits" modal; dismiss with "No thanks".
6. **Field bug:** sidecar `/company/about` writes the tagline field — use
   `/debug/eval` on the Details-tab Overview textarea for description.

## Verified 2026-10-24

Company specialties set via: Details tab → scroll → `input[id*=pill__input]` →
type → ArrowDown → Enter (×N) → Save. Pills: Digital Marketing, Web
Development, Managed Hosting, AI Automation, Custom Software.
