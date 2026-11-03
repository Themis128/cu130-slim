# ig-takedown-ops — delete Instagram organic posts programmatically

IG Graph API **cannot delete organic media** — that is a platform limit,
not a missing scope. When a bad post must come down (garbled media,
wrong account, policy fix), the working path is:

1. `POST /session/start {"platform":"instagram"}` on `browser-novnc:9223`
   — opens the IG login page (kill whichever platform holds the shared
   session first via `/session/stop`).
2. The bridge remembers the `cloudless.gr` profile — a single
   `role=button` ancestor click on the profile span logs in
   (no credential form needed).
3. IG then challenges with **WhatsApp 2FA** (+**********38) — the code
   lands in **WhatsApp Business** (`com.whatsapp.w4b`, not
   `com.whatsapp`) on the Galaxy S22+. Read it via ADB `uiautomator
   dump` regex `([0-9]{6}) is your Instagram login code` — newest = last.
4. Enter the code: the text input + the `Continue` control is a
   `div[role=button]`, NOT a `button` — query both.
5. `/session/cookies` → fresh `sessionid` → `instagrapi
   login_by_sessionid` → `media_delete(pk)`. `MediaNotFound` = already
   deleted on-platform (verify, don't retry).

After a platform-side delete, force-remove the stale local row
(`post_targets` then `posts`) — the API delete route 502s because the
external delete "fails" on an already-deleted post.

Tool: `scripts/ig_delete.py <media_pk> [--row <post_uuid>]` — runs the
whole chain (session → profile login → ADB 2FA → delete → optional row
cleanup). Run via `docker exec -i social-api python ...` (needs httpx +
instagrapi); `adb` must reach the phone from the WSL host.

**Real incident 2026-11-03**: post `18189629230413062` — all API paths
exhausted (Graph no-delete, instagrapi `publickeys` bug kills password
login, stored sessionid expired, CDP real-Chrome blocked by dead WSL
interop). This flow worked end-to-end.
