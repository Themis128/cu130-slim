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

## Tools

`scripts/li_page_edit.py` — get/set Company Page `tagline` and
`description` end-to-end (navigate → tab → real keystrokes → Save →
reload-verify). This is the working write path while Organizations API
edits are gated behind Marketing Developer Platform access.

    python3 li_page_edit.py get tagline
    python3 li_page_edit.py set tagline "New tagline"
    python3 li_page_edit.py set description --file desc.txt

Env: `SIDECAR_URL` (default `http://127.0.0.1:9225`), `ORG_ID` (default
`108614163` = cloudless.gr). Requires `has_session:true` on `/health`.

### Field map + hard-won mechanics (2026-11-23)

- `tagline` → `#organization-tagline-field`, **Page info** tab (default)
- `description` → `#organization-description-field`, **Details** tab
  (panel is `artdeco-tabpanel--hidden` until the tab is clicked — the
  `?editPageActiveTab=` param alone does not always lazy-render)
- Admin edit URL that works: `/company/108614163/admin/edit/` — the
  vanity `/company/cloudless-gr/admin/edit/` redirects to
  `/company/unavailable/` for admin sessions.
- **`/debug/type` WITH `selector` re-clicks the element and collapses
  the text selection** — the new text inserts without replacing. The
  working sequence: `/debug/click` → eval `focus()` +
  `setSelectionRange(0, len)` → `/debug/type` with **no selector**.
- Post-save "Share your page edits" modal → click "No thanks".

## Scheduled campaign revert (2026-11-23)

Black Friday tagline/description are temporary. The revert is automated:
`~/scripts/li_tagline_revert.py` (home-ops repo) runs from cron on
**Nov 28 + Nov 29 at 06:00**, drives this same flow to restore the
permanent tagline `Websites, hosting & practical AI for Greek SMBs —
fixed scope, no surprise invoices`, and verifies persistence. Date-guarded,
idempotent, aborts on unexpected current value. Log:
`~/logs/li-tagline-revert.log`. The BF description block should be
removed the same way after Nov 27:
`li_page_edit.py set description --file <canonical-desc.txt>`.

Canonical (permanent) description — save as `canonical-desc.txt`:

    Cloudless is the serverless cloud partner for startups. We handle cloud architecture, AI-driven marketing, and custom software so founders can build, run, and grow without the enterprise BS.

    Specializing in:
    - Serverless-first architecture (Azure, AWS, Cloudflare)
    - AI-powered marketing automation
    - Custom software development
    - Data analytics & growth engineering

    Athens | Worldwide — cloudless.gr
