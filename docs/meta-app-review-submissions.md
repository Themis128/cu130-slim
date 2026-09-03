# Meta App Review Submission Guide

## App Details
- **App ID**: 1936126137016578
- **App Name**: Cloudless
- **Submission URL**: https://developers.facebook.com/apps/1936126137016578/app-review/submissions/
- **Screencast video**: `/home/tbaltzakis/cu130-slim/docs/linkedin-api-demo/linkedin-api-demo.mp4` (1.2MB)
- **Alternative small test video**: `/home/tbaltzakis/cu130-slim/docs/test-screencast.mp4` (3.5KB)

## Steps to Submit

1. Go to https://developers.facebook.com/apps/1936126137016578/app-review/submissions/
2. Click **Next** to start the submission (submission_id=2047300442565813)
3. Complete the 5 sections:
   - **Verification** — Business verification (if not already done)
   - **App settings** — Verify app icon, privacy policy, etc.
   - **Allowed usage** — Fill in description + upload screencast for each permission (6 permissions)
   - **Data handling** — Answer data handling questions
   - **Reviewer instructions** — Add any additional instructions
4. Click **Submit for review**

## The 6 Permissions to Fill In

### 1. Human Agent
> **Note**: This requires `instagram_manage_messages` or `pages_messaging` to be in the submission. If you don't need Human Agent, you can remove it from the submission.

**Description**:
```
Cloudless uses Human Agent to help businesses respond to Instagram and Facebook messages automatically. Our platform integrates with Instagram messaging to allow social media managers to read and respond to DMs from a unified inbox. The Human Agent permission enables our app to send automated or manual replies on behalf of the connected Instagram Business account, improving response times and customer engagement.

How to test:
1. Visit https://social.cloudless.gr and log in (credentials: admin@cloudless.gr / Cloudless2026!)
2. Go to Settings > Accounts and connect an Instagram Business account
3. Navigate to the Messages inbox
4. Select a conversation and type a reply
5. The reply is sent via the Instagram messaging API using the Human Agent permission
```

### 2. instagram_business_basic
**Description**:
```
Cloudless is a social media management platform that helps businesses manage their Instagram Business/Creator accounts. We use instagram_business_basic to read the connected Instagram Business account profile metadata (username, ID, profile picture, follower count, media count) and display it in our dashboard. This permission is foundational — it is required as a dependency for instagram_business_manage_messages, instagram_business_content_publish, instagram_business_manage_insights, and instagram_business_manage_comments, all of which we are also requesting in this submission.

How to test:
1. Visit https://social.cloudless.gr and log in (credentials: admin@cloudless.gr / Cloudless2026!)
2. Go to Settings > Accounts and click 'Connect Instagram'
3. Authorize with your Instagram Business/Creator account via the OAuth flow
4. After connection, the dashboard displays the Instagram profile info (username, followers count, media count) retrieved via the instagram_business_basic permission
5. The profile information is shown in the Accounts page and used throughout the app for analytics and publishing workflows

This permission is used as a dependent permission for instagram_business_manage_messages, instagram_business_content_publish, instagram_business_manage_insights, and instagram_business_manage_comments.
```

### 3. instagram_business_manage_messages
**Description**:
```
Cloudless uses instagram_business_manage_messages to enable social media managers to read and respond to Instagram Direct Messages from within our unified inbox. This permission allows our app to retrieve message conversations, send replies, and manage message interactions on behalf of the connected Instagram Business account. This is a core feature of our social media management platform — it saves time by centralizing all social communications in one place.

How to test:
1. Visit https://social.cloudless.gr and log in (credentials: admin@cloudless.gr / Cloudless2026!)
2. Go to Settings > Accounts and connect an Instagram Business account
3. Navigate to the Messages/Inbox section
4. View existing conversations retrieved via the instagram_business_manage_messages permission
5. Select a conversation and send a reply — the message is delivered via the Instagram Graph API

This permission depends on instagram_business_basic (also requested in this submission).
```

### 4. instagram_business_content_publish
**Description**:
```
Cloudless uses instagram_business_content_publish to publish images, videos, carousels, and stories to the connected Instagram Business account. This is the core publishing feature of our social media management platform. Users can create posts in our content editor, schedule them for optimal times, and publish them directly to Instagram without leaving our platform. The permission enables: (1) single image posts, (2) video/reel posts, (3) carousel posts with multiple images/videos, (4) story posts with optional link stickers.

How to test:
1. Visit https://social.cloudless.gr and log in (credentials: admin@cloudless.gr / Cloudless2026!)
2. Go to Settings > Accounts and connect an Instagram Business account
3. Navigate to Content > New Post
4. Upload an image or video, add a caption and hashtags
5. Click 'Publish Now' or schedule for a future time
6. The post is published to the connected Instagram account via the instagram_business_content_publish permission
7. Verify the post appears on the Instagram profile

This permission depends on instagram_business_basic (also requested in this submission).
```

### 5. instagram_business_manage_insights
**Description**:
```
Cloudless uses instagram_business_manage_insights to retrieve analytics data for the connected Instagram Business account and its published media. This includes: (1) account-level insights (reach, impressions, follower demographics), (2) per-media insights (likes, comments, shares, saves, reach), (3) publishing quota checks (content_publishing_limit endpoint to monitor remaining daily publish quota). These insights are displayed in our Analytics dashboard and used to provide content performance recommendations.

How to test:
1. Visit https://social.cloudless.gr and log in (credentials: admin@cloudless.gr / Cloudless2026!)
2. Go to Settings > Accounts and connect an Instagram Business account
3. Navigate to Analytics > Instagram
4. View account-level insights (reach, impressions, follower count trends)
5. Click on individual posts to see per-media insights (likes, comments, shares)
6. Navigate to Content > New Post to see the publishing quota indicator (remaining posts today)

This permission depends on instagram_business_basic (also requested in this submission).
```

### 6. instagram_business_manage_comments
**Description**:
```
Cloudless uses instagram_business_manage_comments to help social media managers moderate and respond to comments on their Instagram Business account posts. This permission enables: (1) listing all comments on a media post, (2) replying to comments, (3) hiding inappropriate comments, (4) deleting spam comments. This is a critical engagement feature — it allows users to manage all their Instagram comments from a single dashboard without opening the Instagram app.

How to test:
1. Visit https://social.cloudless.gr and log in (credentials: admin@cloudless.gr / Cloudless2026!)
2. Go to Settings > Accounts and connect an Instagram Business account
3. Navigate to Content > [select a published post]
4. View the comments section — all comments are retrieved via instagram_business_manage_comments
5. Type a reply to a comment and click 'Reply'
6. Click 'Hide' on a comment to hide it from the public view
7. Click 'Delete' on a spam comment to remove it

This permission depends on instagram_business_basic (also requested in this submission).
```

## After Submission

- Meta's review typically takes **1-2 weeks**
- You'll receive email notifications about the review status
- If approved, all 5 Instagram features (quota, comments, stories, mentions, publishing) will work end-to-end
- If rejected, Meta will explain what needs to be fixed — common reasons are:
  - Incomplete screencast (must show OAuth flow + feature usage)
  - Vague description (must be specific about how each permission is used)
  - Missing app settings (privacy policy, data deletion policy, app icon)
