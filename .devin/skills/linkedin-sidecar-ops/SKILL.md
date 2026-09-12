---
name: linkedin-sidecar-ops
description: >-
  Operate and debug the LinkedIn browser sidecar (port 9225) for profile
  reads, headline/about/website/location updates, company page edits,
  experience/education additions, and session management. Covers the
  fixed handlers (specialties, headline via /edit/intro, About with
  scroll-to-load, profile read with lazy-load), session injection from
  SocialAuto, and the LinkedInSidecarClient methods. Use when updating
  LinkedIn profiles via SocialAuto, debugging sidecar profile loading
  issues, or fixing broken edit button selectors.
allowed-tools:
  - read
  - exec
  - grep
  - web_search
triggers:
  - user
  - model
---

# LinkedIn Sidecar Operations

Operate and debug the LinkedIn browser sidecar for profile reads and
updates. The sidecar runs on port 9225 and provides browser automation
for LinkedIn personal profiles and company pages.

## When to use

- Read a LinkedIn personal profile (name, headline, about, experience, education, skills)
- Update LinkedIn headline, About, website, or location
- Update LinkedIn company page (about, website, specialties)
- Add experience or education entries to a LinkedIn profile
- Debug LinkedIn profile page not fully loading in the sidecar
- Fix broken edit button selectors after LinkedIn UI changes
- Inject a browser session from SocialAuto into the sidecar
- Upload profile picture or cover photo (only when explicitly requested)

## Architecture

```
SocialAuto API (port 8083)
    /api/v1/profile/{account_id}
         │
         ▼
    LinkedInSidecarClient
    (social-automation/backend/app/services/linkedin_sidecar.py)
         │
         ▼
    LinkedIn Browser Sidecar (port 9225)
    (linkedin-browser-sidecar/server.js)
         │
         ▼
    Playwright + Chromium (headed, with session)
```

## Session management

The sidecar requires a browser session (cookies/storage state) to operate.
Sessions are stored in the SocialAuto account's `meta_data.browser_storage_state`.

### Inject session from SocialAuto to sidecar

```python
# Inside social-api container
docker compose exec -T social-api python3 -c "
import asyncio, httpx
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

async def save_session():
    # Get cookies from sidecar
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get('http://linkedin-browser-sidecar:9225/debug/all-cookies')
        cookies = r.json().get('cookies', {})

    # Build storage_state
    cookie_list = [
        {'name': n, 'value': v, 'domain': '.linkedin.com', 'path': '/',
         'httpOnly': True, 'secure': True, 'sameSite': 'Lax'}
        for n, v in cookies.items()
    ]
    storage_state = {'cookies': cookie_list, 'origins': []}

    # Save to SocialAuto account
    async with async_session_maker() as db:
        r = await db.execute(select(SocialAccount).where(
            SocialAccount.id == '2de16fca-90e6-4d2f-abf9-b02678df8eda'
        ))
        acc = r.scalars().first()
        meta = acc.meta_data or {}
        meta['browser_storage_state'] = storage_state
        acc.meta_data = meta
        flag_modified(acc, 'meta_data')  # Required for JSON column mutations
        await db.commit()

asyncio.run(save_session())
"
```

### Restore session after sidecar restart

```python
# After docker compose restart linkedin-browser-sidecar
docker compose exec -T social-api python3 -c "
import asyncio, httpx
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from sqlalchemy import select

async def restore():
    async with async_session_maker() as db:
        r = await db.execute(select(SocialAccount).where(
            SocialAccount.id == '2de16fca-90e6-4d2f-abf9-b02678df8eda'
        ))
        acc = r.scalars().first()
        storage = (acc.meta_data or {}).get('browser_storage_state')
        if storage:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post('http://linkedin-browser-sidecar:9225/session',
                                json={'storage_state': storage})
                print(f'Session: {r.json().get(\"status\")}')

asyncio.run(restore())
"
```

## SocialAuto API endpoints

All profile operations go through SocialAuto (not the sidecar directly):

```
GET  /api/v1/profile/{account_id}           — Read profile
PUT  /api/v1/profile/{account_id}           — Update profile
POST /api/v1/profile/{account_id}/login     — Login (username/password)
POST /api/v1/profile/{account_id}/picture   — Upload profile picture
POST /api/v1/profile/{account_id}/cover     — Upload cover photo
```

### LinkedIn personal account ID
```
2de16fca-90e6-4d2f-abf9-b02678df8eda
```

### LinkedIn company page account ID
```
4a8d9440-47d2-4bda-bd11-3776fd9022ba
```

## Profile update fields

The SocialAuto `ProfileUpdateRequest` schema supports:

| Field | LinkedIn Personal | LinkedIn Company |
|-------|-------------------|-----------------|
| headline | ✅ | ❌ |
| about | ✅ | ✅ (as about) |
| website | ✅ | ✅ |
| location | ✅ | ❌ |
| work | ✅ (array of WorkEntry) | ❌ |
| education | ✅ (array of EducationEntry) | ❌ |
| full_name | ❌ (ignored) | ❌ |
| biography | ❌ (ignored) | ❌ |
| phone | ❌ (ignored) | ❌ |
| email | ❌ (ignored) | ❌ |

## Fixed handlers (commit 37ccde47)

### Headline update (handleUpdateHeadline)

- Navigates directly to `{profile_url}/edit/intro` (more reliable than clicking edit button)
- Uses `div[role="textbox"]` contenteditable (not dialog textarea)
- Strips `?isSelfProfile=true` query before appending edit path
- Falls back to old approach (click "Update headline" / "Edit intro" button) if direct URL fails

### About update (handleUpdateAbout)

- Navigates to profile page and scrolls to trigger lazy-loaded About section
- Tries multiple strategies: `button[aria-label="Edit about"]`, pencil icon, edit/details URL
- Falls back to `{profile_url}edit/details/` if no edit button found
- Handles contenteditable div or textarea

### Profile read (handleReadProfile)

- Scrolls page before reading sections (LinkedIn lazy-loads About, Experience, Education)
- Waits 3s after navigation for page stabilization
- Wrapped `page.evaluate` calls in try/catch for navigation context destruction

### Company specialties (handleUpdateCompanySpecialties)

- Navigates to `/admin/edit/` directly (not `/about/`)
- Finds Specialties section by `h4:has-text("Specialties")`
- Uses `input.artdeco-pill__input` for specialty input
- Clicks "Add a specialty" ghost to activate input

## Common issues

| Issue | Cause | Fix |
|-------|-------|-----|
| "LinkedIn browser session not found" | No `browser_storage_state` in meta_data | Inject session (see above) |
| Profile page only 1080px tall | LinkedIn anti-bot or session degradation | Re-login via noVNC, re-capture session |
| "Could not locate Edit about button" | About section not loaded (lazy-load) | Scroll page before reading |
| "page.evaluate: Execution context destroyed" | Navigation during evaluate | Wrap in try/catch, wait for page settle |
| URL concatenation error (`?isSelfProfile=trueedit/`) | Query string not stripped | Use `.split('?')[0]` before appending paths |
| `edit/details/` returns "page doesn't exist" | LinkedIn removed this URL | Use profile page + scroll + click approach |

## Source files

- `linkedin-browser-sidecar/server.js` — Sidecar server (Playwright + Express)
- `social-automation/backend/app/services/linkedin_sidecar.py` — LinkedInSidecarClient
- `social-automation/backend/app/api/profile.py` — SocialAuto profile API

## Future enhancements

- Anti-detection improvements (stealth mode, human-like scrolling delays)
- Session auto-refresh (detect expired session and re-login)
- LinkedIn profile section detection (handle UI changes automatically)
- Experience/education bulk import from CV
- Skills addition via sidecar
- Profile completeness score
