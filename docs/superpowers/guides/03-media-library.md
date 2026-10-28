# Media library

The media library is a team-scoped digital asset manager backed by Cloudflare R2 (free tier) or local disk. Every text field is spell-checked on save.

## 1. Upload media

### Server-side upload (default)

1. Open **Media Library**.
2. Drag and drop files onto the grid, or click **Upload**.
3. Supported: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.mp4`, `.webm`, `.mov`, `.avif`, `.heic`.
4. *Optional:* enter **Alt text** and **Tags** in the upload dialog.
5. Click **Upload**. The file is stored in R2 if `R2_BUCKET_NAME` is set, otherwise on local disk.

### Direct R2 upload (presigned URL)

1. Set `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` in the Env Manager.
2. The frontend calls `POST /api/v1/media/upload/prepare` with the filename, MIME type, and size.
3. The API returns a presigned R2 PUT URL and the object key.
4. The browser PUTs the file directly to that URL.
5. After the upload, the browser calls `POST /api/v1/media/upload/complete` with the key and metadata.
6. This avoids sending large files through the API server.

## 2. Generate an image with AI

1. Click **Generate image**.
2. Enter a prompt, e.g. `a minimalist product shot with soft shadows`.
3. *Optional:* set width, height, steps, or negative prompt.
4. Click **Generate**. The platform tries Cloudflare Workers AI first, then Pixazo, Together, and Hugging Face free tiers.
5. The generated image appears in the grid with `source: ai-generated`.

## 3. Edit metadata

1. Click an asset to open the details panel.
2. Edit **Filename**, **Alt text**, or **Tags**.
3. Click **Save**. The text is normalised and spell-checked.

## 4. Organise with collections

1. Click **New collection**.
2. Name the collection, e.g. `Q4 Product Photos`.
3. Select assets and click **Add to collection**.
4. *Optional:* mark an asset as **Favourite** (star) or **Archive** it when no longer active.

## 5. AI auto-tagging

1. Every image uploaded or generated is automatically queued for AI tagging.
2. Cloudflare Workers AI (`@cf/moondream/moondream3.1-9B-A2B`) writes an `ai_caption` and `ai_tags`.
3. If Cloudflare is unavailable, the worker falls back to Ollama `llava`.
4. The caption and tags are spell-checked before they are saved.
5. To re-run tagging manually, call `POST /api/v1/media/assets/{id}/tag`.

## 6. Similar assets and Chroma search

1. AI captions and tags are embedded and stored in the team's Chroma collection.
2. Open an asset and click **Find similar** to see visually related assets.
3. This uses `GET /api/v1/media/assets/{id}/similar`.
4. Chroma + Ollama embeddings power semantic search and duplicate discovery.

## 7. Search and filter

1. Use the search bar to find by filename, tag, alt text, or AI caption.
2. Filter by:
   - MIME type
   - Source (upload / ai-generated)
   - Collection
   - Favourite / Archived
   - Date range
3. Results update as you type.

## 6. Use media in a post

1. Open the **Post composer**.
2. Click the **Media** icon.
3. Select one or more assets from the library.
4. The composer picks the correct preview for each platform.

## Storage and costs

- **R2** gives 10 GB free storage and free egress. Set `R2_PUBLIC_URL` to a public custom domain or the default `r2.dev` URL.
- **Local disk** is used when R2 is not configured. In that case, media is served through `/api/v1/media/view?path=...`.

## API quick reference

- `POST /api/v1/media/upload` — server-side upload.
- `POST /api/v1/media/upload/prepare` — request a presigned R2 PUT URL.
- `POST /api/v1/media/upload/complete` — finalise a direct R2 upload.
- `GET /api/v1/media/search` — search and filter assets.
- `PATCH /api/v1/media/assets/{id}` — update asset metadata.
- `POST /api/v1/media/assets/{id}/tag` — re-run AI auto-tagging.
- `GET /api/v1/media/assets/{id}/similar` — find similar assets via Chroma.
- `POST /api/v1/media/collections` — create a collection.
- `POST /api/v1/media/collections/{id}/assets` — add an asset to a collection.
- `DELETE /api/v1/media/collections/{id}/assets/{asset_id}` — remove an asset from a collection.

## Troubleshooting

### AI auto-tagging fails with "Cloudflare vision error 401"

The `CLOUDFLARE_API_TOKEN` needs **both** of these permissions on the account:

- **Workers AI → Read**
- **Workers AI → Run**

`Workers AI → Edit` alone does **not** grant the right to call `/ai/run`. If the token only has `Edit`, edit it in the Cloudflare dashboard and add `Run`, then copy the regenerated token value into `.env` and restart `social-api` and `social-worker`.

Verify the token with:

```bash
source .env
curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/tokens/verify"
```

You should see `"status":"active"`. If the inference call still fails after adding `Run`, ensure the token is scoped to the correct account (`fb7dc7b69b662480cd5961a4d1913c78`).

### R2 presigned URLs generate but uploads fail

Check that `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` come from **R2 → Manage API Tokens** and that the token has `Object Read & Write` for `app-media-bucket`.
