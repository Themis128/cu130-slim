# gates-ops — the last physical gates, scripted

Every remaining owner action, mapped to a tool. All use either the root CDP
session (`consent-root-ops` — run `start-chrome-debug.bat` once), ADB on the
S22+, or the GitHub pipeline. Never prints secrets.

| Gate | Tool | Human part |
|---|---|---|
| #786 WhatsApp Cloud API terms | `cdp_click.py "https://business.facebook.com/wa/manage/home/" "Accept|Agree"` | debug Chrome open + logged in |
| #785 Meta evidence upload | `cdp_click.py <console-url> "Upload|Continue"` | same |
| X re-auth approve | `cdp_click.py <oauth-url> "Authorize|Approve"` | same |
| #789 TikTok website edit | `tiktok_profile_edit.py https://cloudless.gr` | S22+ USB-connected (ADB) |
| #793/#794/#795 approvals | `gate_check.py` + reply `approve`/`done` on the issue | one word |
| #788 IG password rotation | `social-profile-update`'s session_bootstrap + new `INSTAGRAM_PASSWORD` in .env | edit one secret |

## gate_check.py

Lists every open issue with its live-verified unblock action — run anytime:

    python3 .devin/skills/gates-ops/scripts/gate_check.py

## cdp_click.py <url> <text-regex>

Drives the user's real logged-in Chrome (CDP :9222): navigates, finds a
button/link whose text matches the regex, clicks it, reports what happened.
Needs `websocket-client` on Windows python and the debug Chrome running.

## tiktok_profile_edit.py <url>

ADB (S22+): opens TikTok → profile → Edit profile → website field → Save.
Runs `adb shell` taps/text-input at known-good coordinates; screenshots
before/after into /tmp for verification. App-side only surface.

## Consolidated remaining-input map (2026-10-29)

| Gate | State | The one act | Auto-finisher |
|---|---|---|---|
| X OAuth/2FA | armed | Google sign-in in debug window | `gate_runner2.py` x-watch + `totp-ops` |
| #786/#785 Meta | **blocked** | co-admin or new portfolio — Meta's own answer | none — account-level restriction |
| #788 Instagram | needs secret | `INSTAGRAM_PASSWORD` in `.env` | `session_bootstrap.py` |
| #793–795 | needs word | `approve`/`done` replies | owner-gate 6h pipeline |
| TikTok #789 | rejected ×2 | doc-type fix — see `tiktok-verification` | rerun flow after type fix |

Runner: `gate_runner2.py` = clone→debug→forwarder→watch→click, single call.
