# Connecting social accounts

Connect one or more social platforms to a team. Each platform needs its own OAuth app, created outside SocialAuto.

## Before you start

1. You must be the **Owner** or an **Admin** of the team.
2. You need a developer account on each platform you want to connect.
3. Copy the OAuth **Client ID** and **Client Secret** into the Env Manager or `.env` file.

## 1. LinkedIn

1. Go to **Accounts > LinkedIn**.
2. Click **Connect LinkedIn**.
3. In the OAuth popup, log in and authorise the app.
4. Choose the **Company Page** you want to post to, or leave it as personal if that is what you need.
5. The account card turns green. The default Cloudless Company Page URN is pre-filled when you use the carousel flow.

## 2. X / Twitter

1. Go to **Accounts > X / Twitter**.
2. Click **Connect X**.
3. Authorise the app in the popup.
4. After redirect, the account appears in the list with its handle and scopes.

## 3. TikTok

1. Go to **Accounts > TikTok**.
2. Click **Connect TikTok**.
3. Log in with a TikTok for Business account and approve the scopes.
4. TikTok accounts use `publish_video` or `content_post` scopes depending on the integration.

## 4. Facebook, Instagram, and Threads

1. Go to **Accounts > Meta**.
2. Click **Connect Facebook/Instagram/Threads**.
3. The Meta OAuth flow lets you select which Pages and Instagram accounts to authorise.
4. Each selected account appears as a separate card in SocialAuto.

## 5. Verify connection health

1. In the **Accounts** list, each card shows:
   - **Status** (connected / expired)
   - **Scopes** granted
   - **Follower count** when available
2. Click the **Refresh** icon to re-sync an account.

## Troubleshooting

- **Token expired**: click **Reconnect** and repeat the OAuth flow.
- **Missing scope**: delete the connection and reconnect, making sure to approve every requested permission.
- **Company Page not listed**: ensure the LinkedIn app has the `w_organization_social` product and that you are an admin of the page.
