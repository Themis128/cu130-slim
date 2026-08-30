# Media library

The media library is a team-scoped digital asset manager backed by Cloudflare R2 (free tier) or local disk. Every text field is spell-checked on save.

## 1. Upload media

1. Open **Media Library**.
2. Drag and drop files onto the grid, or click **Upload**.
3. Supported: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.mp4`, `.webm`, `.mov`, `.avif`, `.heic`.
4. *Optional:* enter **Alt text** and **Tags** in the upload dialog.
5. Click **Upload**. The file is stored in R2 if `R2_BUCKET_NAME` is set, otherwise on local disk.

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

## 5. Search and filter

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
