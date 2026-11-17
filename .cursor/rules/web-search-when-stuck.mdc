# Use web search when stuck

User directive (2026-11-16): when the agent gets stuck or hits a wall —
"when you stack use web search".

## When

- Repeated attempts fail with no new information (e.g. taps not landing,
  a flow looping, an API erroring identically).
- A platform/UI behaves unexpectedly and local evidence can't explain it
  (Meta/WhatsApp flows, OAuth wizards, verification screens, new console UIs).
- Before escalating to the user as "blocked" — search for the known
  workaround first; many platform quirks are documented.

## How

- Prefer official docs first (developer.facebook.com, Meta Business Help,
  platform docs), then community reports for undocumented quirks.
- Search the *symptom*, not the theory: "whatsapp verify didn't receive
  code button not working", not "whatsapp automation".
- Feed findings back: if the fix is a durable operational fact (a rate-limit
  window, a UI quirk, a required surface), document it in the matching skill
  or BACKLOG so the next session doesn't re-search it.
- Don't grind retries against a rate-limited or human-gated surface while
  searching — search is the alternative to hammering, not an add-on.
