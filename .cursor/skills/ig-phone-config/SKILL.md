# IG Phone Config (ADB)

Drive Instagram business/messaging settings on the Galaxy S22+ via ADB —
welcome message, FAQ, saved replies, broadcast channel. For settings the
API/Graph can't write (all profile + messaging config is mobile-app-only).

## When to use

- Toggling IG business messaging tools (welcome, FAQ, saved replies)
- Creating broadcast channels
- Profile/bio edits that are app-gated

## Hard-won gotchas (2026-10-25 session)

- **Cover screen kills UI control.** `CoverHomeActivity` = physical flip cover
  closed — ADB sees the device but input lands on the cover UI. `dismiss-keyguard`,
  `KEYCODE_WAKEUP`, `am start` all fail to focus the app. Needs a PHYSICAL unfold.
- **`input text` eats spaces** — use `%s` escapes: `input text "Hey!%sHow%sare%dyou"`.
- **`input text` is ASCII-only** — em-dash/Unicode silently truncates. Stay ASCII.
- **Welcome message rejects links** — IG validation: no URLs. CTA must point to
  "link in bio", never a raw domain.
- **Text fields accumulate, never replace** — `input text` appends at cursor.
  Clean replace = long-press → ⋮ overflow → Select all → Cut → type. (Backspace
  loops work but race focus and undershoot.)
- **Settings search doesn't cover business tools** — "welcome" returns 0 results.
  Navigate: Settings → Account type and tools → Messaging tools.
- **Screen is logical 1080×2340** — screenshots may render different px size;
  always tap by uiautomator `bounds`, not screenshot estimates.
- **KEYCODES:** 122=MOVE_HOME, 123=MOVE_END, 67=DEL, 66=ENTER, 23=DPAD_CENTER.

## Verified navigation paths (@cloudless.gr, Oct 2026)

| Target | Path |
|---|---|
| Welcome message | profile → ☰ → Account type and tools → Messaging tools → Welcome message → toggle + field (no links) |
| FAQ | same → Frequently asked questions → Add question (≤4) → Show questions ON |
| Saved reply | same → Saved reply (slow to load ~5s) |
| Broadcast channel | DM inbox (paper-plane) → + → Create channel |
| Monetization | ☰ → Professional dashboard → Monetization tools |
| Account check | ☰ → Settings → Account privacy (must be Public) |

## Script

`scripts/ig-adb.sh` — helpers: `unlock`, `shot`, `ui`, `tap_text`,
`type_field` (select-all+replace), `tap_row` (bounds lookup).
