# TikTok content posting

SocialAuto supports TikTok Upload Draft and Direct Post through the Content Posting API.

## Prerequisites

1. Add the **Content Posting API** product to the TikTok developer app.
2. Obtain approval for `video.upload` to use Upload Draft.
3. Obtain approval for `video.publish` to use Direct Post.
4. Reconnect the TikTok account after changing scopes so the access token includes the approved permissions.
5. Verify the HTTPS domain or URL prefix used by `MEDIA_PUBLIC_BASE_URL`. TikTok rejects `PULL_FROM_URL` media hosted outside a verified property.

## Upload a draft

1. Open **Content** and choose **New post**.
2. Select the TikTok account and add an MP4, MOV, WebM, or up to 35 photos.
3. Under **TikTok publishing**, select **Upload draft — finish in TikTok inbox**.
4. Publish or schedule the post.
5. Wait for the TikTok inbox notification.
6. Open TikTok, review the draft, finish editing, and publish it.

Upload Draft is the default. It uses `video.upload`. Video URLs are submitted to `/v2/post/publish/inbox/video/init/`; photos use `/v2/post/publish/content/init/` with `post_mode` set to `MEDIA_UPLOAD`.

## Publish directly

1. Confirm the developer app and connected account are approved for `video.publish`.
2. Open **Content** and choose **New post**.
3. Select TikTok and add media.
4. Under **TikTok publishing**, select **Direct publish**.
5. Select a privacy level offered by the creator account.
6. Publish or schedule the post.

SocialAuto queries `/v2/post/publish/creator_info/query/` before Direct Post and rejects privacy values not offered by TikTok. Videos use `/v2/post/publish/video/init/`; photos use `/v2/post/publish/content/init/` with `post_mode` set to `DIRECT_POST`.

## File and URL transfer

The normal publishing pipeline uses `PULL_FROM_URL`. The media URL must:

- use HTTPS;
- belong to a verified TikTok developer property;
- return the media without redirecting;
- remain accessible while TikTok downloads it.

The API client also supports `FILE_UPLOAD`. It initializes the upload with the byte size, chunk size, and chunk count, then uploads sequential chunks with `Content-Length`, `Content-Range`, and the correct video MIME type. Chunks follow TikTok's 5–64 MB limits, except that files smaller than 5 MB are uploaded whole.

## Statuses

SocialAuto polls `/v2/post/publish/status/fetch/` after initialization:

- `SEND_TO_USER_INBOX` means an Upload Draft is ready for review in TikTok.
- `PUBLISH_COMPLETE` means Direct Post completed, or the user completed an uploaded draft.
- `FAILED` or `CANCELLED` records the TikTok failure reason.
- Processing that exceeds the polling window is reported as a timeout while retaining the `publish_id` in the error.
