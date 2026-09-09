# Threads Operations

Day-to-day operations for the Threads platform in the SocialAuto Cloudless stack: connect accounts, switch between Instagram/Threads profiles, publish text and media posts, and debug OAuth/tester issues.

## When to use

- Connect a Threads account to SocialAuto.
- Switch from one Threads account to another.
- Diagnose `threads_basic` permission / tester errors.
- Verify the Threads app and OAuth configuration.
- Post a draft to Threads manually or via script.
- Update Threads bio or display name via browser bridge.
- Get Threads profile info (bio, username, avatar, token status).

## Quick commands

```bash
# Check Threads app status and connected accounts
bash .devin/skills/threads-ops/scripts/check-threads-app.sh

# Get Threads profile info (bio, username, avatar, token)
bash .devin/skills/threads-ops/scripts/get-threads-profile.sh

# Switch to the cloudless.gr Threads/Instagram profile
bash .devin/skills/threads-ops/scripts/switch-threads-account.sh cloudless.gr

# Start the OAuth flow for the default team
bash .devin/skills/threads-ops/scripts/connect-threads.sh

# Post a text thread to the first connected Threads account
bash .devin/skills/threads-ops/scripts/post-threads.sh "Hello from the Cloudless social stack"

# Update Threads bio via browser bridge
bash .devin/skills/threads-ops/scripts/update-threads-bio.sh cloudless_gr "Clear skies. Zero friction."

# Update Threads display name via browser bridge (max 2 changes per 14 days)
bash .devin/skills/threads-ops/scripts/update-threads-name.sh cloudless_gr "Cloudless"
```

## Background

Threads uses the **same identity as Instagram**. Every Threads profile is linked to one Instagram account. There is no way to switch Threads accounts without logging out of Instagram and logging back in with the other account. The SocialAuto OAuth flow opens `https://threads.net/oauth/authorize`, which redirects to an Instagram login if the user is not already logged in to the correct Instagram account.

The Cloudless Threads app (`THREADS_CLIENT_ID`) is a child of the main Meta app. In development mode, only **Threads Testers** can grant the `threads_basic` and `threads_content_publish` permissions. App Review is required to let any user grant these permissions.

## Connect a Threads account

1. Make sure the correct Instagram profile is active:
   ```bash
   bash .devin/skills/threads-ops/scripts/switch-threads-account.sh cloudless.gr
   ```
2. Start the OAuth flow:
   ```bash
   bash .devin/skills/threads-ops/scripts/connect-threads.sh
   ```
3. Open the returned URL in the browser-novnc viewer (`http://localhost:6080/vnc.html`) or let the browser bridge navigate automatically.
4. Click **Continue As {username}** in the VNC browser.
5. Wait for the callback to return to `https://social.cloudless.gr/api/v1/auth/oauth/threads/callback`.
6. Verify the account appears in SocialAuto:
   ```bash
   bash .devin/skills/threads-ops/scripts/check-threads-app.sh
   ```

### Common OAuth errors

| Error | Cause | Fix |
|---|---|---|
| `This action requires the threads_basic permission. You must submit for app review, or your user must be in the list of Threads testers.` | The Threads user who clicked Continue is not a Threads Tester in the Meta app. | Add the user to **App roles > Threads Testers** in `https://developers.facebook.com/apps/{APP_ID}/roles/roles/` and have the user accept the invite in Threads settings → Website permissions. |
| `Error validating application. Cannot get application info due to a system error.` | `THREADS_CLIENT_SECRET` is wrong or the app is misconfigured. | Verify `THREADS_CLIENT_ID` and `THREADS_CLIENT_SECRET` in `.env` match the values in the Meta developer console. |
| `Invalid redirect_uri` | The redirect URI in the request does not match the app settings. | Ensure `THREADS_REDIRECT_URI=https://social.cloudless.gr/api/v1/auth/oauth/threads/callback` is registered in the Threads app settings. |
| `Continue As t_baltzakis` instead of `cloudless.gr` | The VNC browser is logged in to a different Instagram account. | Use `switch-threads-account.sh cloudless.gr` first. |

## Switch Threads accounts

Because Threads shares Instagram sessions, switching requires clearing the Instagram/Threads session and logging back in with the desired account.

```bash
bash .devin/skills/threads-ops/scripts/switch-threads-account.sh cloudless.gr
bash .devin/skills/threads-ops/scripts/switch-threads-account.sh t_baltzakis
bash .devin/skills/threads-ops/scripts/switch-threads-account.sh cloudless_gr
```

The script navigates to the Instagram profile picker and clicks the named account. If the profile is not in the saved list, log in manually via VNC.

## Publish to Threads

Text, image, video and carousel posts are supported by the `ThreadsAPIClient` in `app/services/threads_api.py`. The skill script posts plain text for now; for media posts, use the SocialAuto content API or the API endpoint.

```bash
# Post a simple text thread
bash .devin/skills/threads-ops/scripts/post-threads.sh "Your post text here"

# Post using a specific account ID
bash .devin/skills/threads-ops/scripts/post-threads.sh \
  "Your post text here" \
  8ef86e64-80b9-479d-b445-1fba0c0d2844
```

## Verify the Threads app setup

```bash
bash .devin/skills/threads-ops/scripts/verify-threads-tester.sh
```

This script:
1. Checks `THREADS_CLIENT_ID` and `THREADS_CLIENT_SECRET` are set in `.env`.
2. Generates the Threads OAuth URL for the default team.
3. Reports the redirect URI and scopes.
4. Checks whether there is already a connected Threads account in SocialAuto.

## Meta App Review checklist

Before non-test users can connect Threads:

1. Go to `https://developers.facebook.com/apps/{APP_ID}/use_cases/`.
2. Customize **Access the Threads API**.
3. Add the needed permissions:
   - `threads_basic` (required)
   - `threads_content_publish` (required for posting)
   - `threads_manage_insights` (optional)
   - `threads_manage_replies` (optional)
4. Go to `App roles > Threads Testers` and add the Instagram usernames that need to test.
5. Each user must accept the tester invitation in Threads → Settings → Account → Website permissions.
6. Submit for App Review once the integration is ready for public use.

## Files

- `scripts/check-threads-app.sh` — list connected accounts, app status and token health.
- `scripts/connect-threads.sh` — start the SocialAuto OAuth flow and show the authorization URL.
- `scripts/get-threads-profile.sh` — get Threads profile info via API (bio, username, avatar, token).
- `scripts/switch-threads-account.sh` — switch the VNC browser to a different Instagram/Threads profile.
- `scripts/post-threads.sh` — publish a simple text thread.
- `scripts/update-threads-bio.sh` — update Threads bio via browser bridge.
- `scripts/update-threads-name.sh` — update Threads display name via browser bridge (max 2 per 14 days).
- `scripts/verify-threads-tester.sh` — verify the app configuration and OAuth URL.
