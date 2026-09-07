# Instagram Profile Manager

Manage Instagram business/creator profile metadata through the aiograpi-rest sidecar:
biography, external URL, profile picture, name, and account info.

## When to use

- Update Instagram bio text
- Update Instagram profile picture
- Set/change external URL in bio
- Read current profile info (username, bio, follower count, etc.)
- Login via saved sessionid or username/password

## Sidecar

```
http://localhost:8011   (host port)
http://instagram-private-api:8000  (internal Docker network)
```

## Authentication

All authenticated endpoints require an `X-Session-ID` header.
Sessions are stored in `/data/db.json` inside the container (persisted via Docker volume).

### Login methods

1. **By sessionid** (preferred — no 2FA, no password): `POST /auth/login/by/sessionid`
2. **By username/password**: `POST /auth/login` (may trigger 2FA or challenge)
3. **Restore from saved settings**: `PATCH /auth/relogin`

### Finding a saved sessionid

The sidecar stores sessions in `/data/db.json`. Each session has a `sessionid` field.
To list available sessions:

```bash
.devin/skills/instagram-profile-manager/scripts/list-sessions.sh
```

## Key endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/auth/login/by/sessionid` | Login with a sessionid cookie |
| POST | `/auth/login` | Login with username/password |
| PATCH | `/auth/relogin` | Refresh current session |
| GET | `/account` | Get authenticated account info |
| PATCH | `/account` | Update profile (full_name, biography, external_url, phone_number, email) |
| PATCH | `/account/biography` | Update biography text only |
| PATCH | `/account/external-url` | Set website URL in bio |
| DELETE | `/account/external-url` | Remove website URL from bio |
| PATCH | `/account/picture` | Update profile picture (multipart file upload) |
| GET | `/user?username=...` | Get any user's profile (public) |

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# List saved sessions in the sidecar
.devin/skills/instagram-profile-manager/scripts/list-sessions.sh

# Login using a sessionid
.devin/skills/instagram-profile-manager/scripts/login-by-sessionid.sh <sessionid>

# Login using username and password
.devin/skills/instagram-profile-manager/scripts/login.sh [username] [password]

# Get current account info
.devin/skills/instagram-profile-manager/scripts/get-account.sh [session_id]

# Update biography
.devin/skills/instagram-profile-manager/scripts/update-bio.sh [session_id] "new bio text"

# Update external URL
.devin/skills/instagram-profile-manager/scripts/update-url.sh [session_id] "https://example.com"

# Update profile picture
.devin/skills/instagram-profile-manager/scripts/update-picture.sh [session_id] <image_file>

# Get any user's profile
.devin/skills/instagram-profile-manager/scripts/get-user.sh [session_id] <username>
```

## Instagram bio limits

- **150 characters** max (emojis count as 2 chars each in Instagram's count)
- Newlines supported (`\n`)
- No clickable links in bio text — use the external URL field instead

## Important notes

- The sessionid is httpOnly and cannot be retrieved via browser JS.
- To get a fresh sessionid: log in via browser, then extract cookies from the
  browser context (not document.cookie).
- Sessions expire after ~90 days. The sidecar auto-refreshes after 6 days.
- Instagram aggressively blocks datacenter IPs. Use WARP proxy
  (`socks5://warp-proxy:1080`) for login attempts.
- Never log or commit session IDs, settings JSON, or passwords.
