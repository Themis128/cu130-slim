---
name: linkedin-connect-ops
description: >
  Send LinkedIn connection requests (with optional note) to specific
  profiles through the logged-in session on the Windows debug Chrome
  (CDP :9229, chrome-debug-profile). Uses LinkedIn's live Voyager
  relationships endpoint in-page — NOT the dead /growth/normInvitations
  path. Use for "connect with X on LinkedIn", outreach batches, or
  post-event networking. Tool: scripts/li_connect.py
---

# LinkedIn Connect Ops

Sends real connection requests from the user's personal LinkedIn account
by driving the live Voyager API inside the logged-in debug-Chrome tab.

## Why in-page fetch (not cookie replay, not REST)

- Raw `li_at`/`JSESSIONID` replay via httpx 401s on Voyager — LinkedIn
  needs headers the replay misses.
- `POST /voyager/api/growth/normInvitations` is **dead** (301). The live
  SPA endpoint (verified 2026-06, linkedin-mcp 2.x writecapture) is:

  ```
  POST /voyager/api/voyagerRelationshipsDashMemberRelationships
       ?action=verifyQuotaAndCreateV2
  {"invitee":{"inviteeUnion":{"memberProfile":"urn:li:fsd_profile:..."}}}
  ```

- Optional note rides as `customMessage` (unverified — default is
  no-note; LinkedIn caps ~200 notes/day and no-note invites often get
  better acceptance).

## Prerequisites

1. Debug Chrome running on `:9229` (see consent-root-ops):
   `chrome.exe --remote-debugging-port=9229 --user-data-dir=C:\Users\tbaltzakis\chrome-debug-profile`
   The port binds LAN/tailnet IPs, NOT 127.0.0.1 — discover via
   `netstat -ano | findstr 9229` or try `http://192.168.1.23:9229/json/version`.
2. A logged-in linkedin.com tab in that profile.

## Tool

```bash
python scripts/li_connect.py status                      # session + tab check
python scripts/li_connect.py connect <profile-url> [...] # send requests
python scripts/li_connect.py connect <url> --note "..."  # with note
python scripts/li_connect.py connect <url> --dry-run     # resolve + report only
python scripts/li_connect.py connect <url> --cdp http://<host>:9229
```

Profile URL accepts `/in/<vanity>/` links (search results, posts, etc.).

## Response shape (verified live 2026-11-27)

`GET /voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity=<slug>`
returns `data.elements[0]`:

- `entityUrn` = `urn:li:fsd_profile:ACoAAA...` — the invite target URN
- `memberRelationship.memberRelationshipUnion.<connType>.memberDistance`:
  `DISTANCE_1` = already connected → skip; `DISTANCE_2/3` = invitable
- `memberRelationship...invitationUnion`: `noInvitation` = clear to invite;
  any other key (`invitationSent` etc.) = pending → skip

Invite POST returns `{"value":{"invitationUrn":"urn:li:fsd_invitation:..."}}`
on success. HTTP 200 can still carry nested GraphQL `errors[]` — the
script classifies those as failures, never trusts status alone.

## Rate-limit etiquette

- Weekly invite cap ~100–200 (varies by account age). Keep batches small
  (<20/run), space requests ~6–10s, never retry-loop failures.
- Quota/restriction errors → stop the batch immediately.

## Verified live

2026-11-27: all three AFI seminar teachers invited successfully
(invitationUrn returned for each).
