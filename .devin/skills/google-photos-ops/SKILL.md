# google-photos-ops

Google Photos access via `@thenavidm/google-photos-mcp-cli` — the Picker-API-based
MCP server (the only compliant read path after Google revoked whole-library
Library API reads on 2025-03-31). Registered in `.devin/mcp_config.json` as
`google-photos`.

## One-time setup (human gate — OAuth consent)

1. Google Cloud Console → create/select a project → enable **Google Photos
   Picker API** (and Photos Library API if uploads are ever needed).
2. OAuth consent screen → External → add yourself as a test user.
3. Create OAuth 2.0 credentials → **Desktop app** (or Web with
   `http://localhost` redirect).
4. Add to `.env`:
   ```
   GOOGLE_PHOTOS_CLIENT_ID=...
   GOOGLE_PHOTOS_CLIENT_SECRET=...
   ```
5. Mint the refresh token once (opens browser for consent):

   ```bash
   export GOOGLE_PHOTOS_CLIENT_ID=... GOOGLE_PHOTOS_CLIENT_SECRET=...
   npx -y @thenavidm/google-photos-mcp-cli google-photos-mcp login
   # → prints GOOGLE_PHOTOS_REFRESH_TOKEN=... → add to .env
   ```

   or use `scripts/login.sh` which wraps this and appends the token to `.env`.

## After setup — pick photos

The agent starts a Picker session → user gets a Google-hosted URL → user selects
photos in their own browser → the agent downloads only the picked items.

```bash
# CLI surface (same names as the MCP tools)
npx -y @thenavidm/google-photos-mcp-cli google-photos-cli          # list commands
npx -y @thenavidm/google-photos-mcp-cli google-photos-mcp doctor   # verify setup
```

MCP tools (once the server is running): start_picker_session,
check_picker_session, list_picked_media, download_picked_media, plus album /
upload / enrichment tools.

## Privacy model

- The picker is Google-hosted — the user chooses exactly which items the agent
  can see. No whole-library crawl.
- `GOOGLE_PHOTOS_READ_ONLY=1` hides every write tool if wanted.
- Tokens stay in `.env` — never printed or committed.

## Use in the video pipeline

1. `start_picker_session` → send user the picker URL (Telegram/Slack).
2. User picks source photos (e.g. headshots for cartoon-avatar video).
3. `download_picked_media` → save under `/app/uploads` → register as
   `media_assets` rows → feed into ComfyUI FLUX img2img (cartoon style) →
   LTX/Wan2.1 image-to-video → mux voiceover → media-quality gates.
