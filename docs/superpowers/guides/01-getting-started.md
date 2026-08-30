# Getting started

This guide walks a first-time user from login to a healthy, ready-to-use SocialAuto workspace.

## 1. Open the dashboard

1. Start the Docker Compose stack (`docker compose up -d`).
2. Open `http://localhost:8082` in your browser.
3. Log in with the owner email and password created during setup.

## 2. Create or join a team

1. After login, the dashboard asks you to **Create a team**.
2. Enter a team name, e.g. `Cloudless Marketing`.
3. The creator becomes the **Owner**. Owners can invite **Admins**, **Editors**, and **Viewers**.

## 3. Check service health

1. Click **Settings** in the sidebar.
2. Look for the health status card. All services should show **Healthy**:
   - `social-api`
   - `social-worker`
   - `postgres`
   - `redis`
   - `n8n`
3. If any service is down, run `docker compose restart <service>` and wait 10 seconds.

## 4. Add your AI and storage credentials

1. Go to **Settings > Env Manager**.
2. Add the keys you will use:
   - `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` — required for Workers AI and R2.
   - `R2_BUCKET_NAME` and `R2_PUBLIC_URL` — optional; local storage is used if not set.
   - Platform-specific keys (e.g. `LINKEDIN_CLIENT_ID`, `TWITTER_CLIENT_ID`) — see [Connecting social accounts](02-connecting-accounts.md).
3. Click **Save** and **Restart social-api** so the changes are loaded.

## 5. Verify the media library is ready

1. Open **Media Library** from the sidebar.
2. Try the **Generate image** button with a simple prompt like `a modern office desk`.
3. If an image appears in the grid, Workers AI is working.

## Next steps

- [Connect a social account](02-connecting-accounts.md)
- [Upload your first media asset](03-media-library.md)
