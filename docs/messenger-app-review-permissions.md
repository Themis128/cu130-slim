# Messenger App Review — Permission Descriptions

## pages_messaging

**Description:**
Cloudless uses pages_messaging to enable our AI-powered Messenger bot for Facebook Pages. When a user sends a message to a connected Facebook Page, our platform receives the message via webhook, generates a brand-aware AI response, and sends the reply back to the user. This is the core feature of our Messenger integration — it allows businesses to provide instant, automated customer support on Messenger 24/7. The bot uses the business's brand voice, messaging pillars, and product knowledge to generate relevant responses.

**How to test:**
1. Open Facebook Messenger (mobile or web)
2. Search for "cloudless.gr" Page and start a conversation
3. Send any text message (e.g., "Hello, what services do you offer?")
4. The bot will reply within 3-5 seconds with a brand-aware response
5. Try the persistent menu buttons (Home, Contact, Pricing, Free Audit)
6. Try the ice breaker questions (What services? How much? Free audit? Timeline?)

**Screencast:** Shows a user messaging the Page and receiving an AI-generated reply.

---

## pages_show_list

**Description:**
Cloudless uses pages_show_list to display the list of Facebook Pages that the user manages, so they can select which Page(s) to connect to our platform. This is the first step in the Messenger setup flow — without this permission, users cannot see or select their Pages to enable the bot.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Accounts page
3. Click "Connect Messenger" — this initiates the Facebook OAuth flow
4. After authorization, the user's Facebook Pages are displayed
5. Select a Page to connect — the Page appears in the connected accounts list

**Screencast:** Shows the OAuth flow and Page selection.

---

## pages_manage_metadata

**Description:**
Cloudless uses pages_manage_metadata to configure the Messenger Profile for connected Facebook Pages. This includes setting up the Get Started button, persistent menu, ice breakers, and whitelisted domains. These profile settings are what make the bot interactive — without them, the bot would just respond to text messages with no menu or guided conversation starters.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Accounts → Messenger inbox for a connected Page
3. The Messenger Profile is configured with:
   - Get Started button (payload: GET_STARTED)
   - Persistent menu: Home, Contact, Pricing, Free Audit, Website
   - Ice breakers: 4 conversation starter questions
   - Whitelisted domain: https://cloudless.gr
4. Open Messenger and verify the menu and ice breakers appear

**Screencast:** Shows the Messenger Profile configuration and the menu in Messenger.

---

## pages_read_engagement

**Description:**
Cloudless uses pages_read_engagement to read message engagement data (deliveries, reads, reactions) for conversations in the Messenger inbox. This allows businesses to track how their bot responses are performing — whether messages are being read, reacted to, or ignored. This data is displayed in the analytics dashboard.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Analytics → Messenger
3. View engagement metrics for bot conversations (messages sent, delivered, read)
4. The metrics are retrieved via the pages_read_engagement permission

**Screencast:** Shows the analytics dashboard with Messenger engagement data.

---

## pages_manage_posts

**Description:**
Cloudless uses pages_manage_posts to publish, schedule, and manage social media posts on connected Facebook Pages. This is a separate feature from Messenger — it allows businesses to create content (text, images, videos) and publish it to their Facebook Page directly from our platform. Users can schedule posts for optimal times, create recurring posts, and manage their content calendar.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Content → New Post
3. Compose a post with text and/or media
4. Select the Facebook Page as the target
5. Click "Publish Now" or schedule for a future time
6. The post appears on the Facebook Page

**Screencast:** Shows creating and publishing a post to a Facebook Page.

---

## pages_read_user_content

**Description:**
Cloudless uses pages_read_user_content to read user-generated content on connected Facebook Pages, such as comments and posts by Page visitors. This allows businesses to monitor and respond to user engagement from our unified inbox, rather than switching between Facebook and our platform.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to the inbox/engagement section
3. View comments and user posts on the connected Page
4. Respond to comments directly from the platform

**Screencast:** Shows reading and responding to user comments.

---

## pages_manage_engagement

**Description:**
Cloudless uses pages_manage_engagement to help businesses moderate and respond to comments on their Facebook Page posts. This includes replying to comments, hiding inappropriate comments, and deleting spam. This is part of our unified engagement management feature.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Navigate to a published post on the connected Page
3. View comments in the engagement panel
4. Reply to a comment, hide a comment, or delete spam

**Screencast:** Shows moderating comments from the platform.

---

## pages_utility_messaging

**Description:**
Cloudless uses pages_utility_messaging to send utility messages outside the 24-hour messaging window when necessary. This includes account updates, order confirmations, and important notifications that cannot wait for the user to send a message first. We only use this for legitimate utility messages, never for promotional content.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Messenger inbox for a connected Page
3. Send a utility message (e.g., account update notification) to a user
4. The message is delivered with the appropriate message tag

**Screencast:** Shows sending a utility message with a message tag.

---

## business_management

**Description:**
Cloudless uses business_management to manage the business portfolio connection and ensure proper business verification for the app. This permission allows us to associate the app with the user's business portfolio, which is required for App Review and production access.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Accounts → Connect Facebook
3. The OAuth flow includes business_management scope
4. After authorization, the business portfolio is linked

**Screencast:** Shows the OAuth flow with business portfolio connection.

---

## instagram_business_basic

**Description:**
Cloudless uses instagram_business_basic to read the connected Instagram Business account profile metadata (username, ID, profile picture, follower count, media count) and display it in our dashboard. This permission is foundational — it is required as a dependency for instagram_business_manage_messages, instagram_business_content_publish, instagram_business_manage_insights, and instagram_business_manage_comments.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Accounts and connect an Instagram Business account
3. After connection, the dashboard displays the Instagram profile info
4. The profile information is shown in the Accounts page

**Screencast:** Shows connecting an Instagram account and viewing profile info.

---

## instagram_business_manage_messages

**Description:**
Cloudless uses instagram_business_manage_messages to enable social media managers to read and respond to Instagram Direct Messages from within our unified inbox. This permission allows our app to retrieve message conversations, send replies, and manage message interactions on behalf of the connected Instagram Business account.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Accounts and connect an Instagram Business account
3. Navigate to the Messages/Inbox section
4. View existing conversations and send a reply

**Screencast:** Shows reading and responding to Instagram DMs.

---

## instagram_business_content_publish

**Description:**
Cloudless uses instagram_business_content_publish to publish images, videos, carousels, and stories to the connected Instagram Business account. Users can create posts in our content editor, schedule them for optimal times, and publish them directly to Instagram without leaving our platform.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Content → New Post
3. Upload an image or video, add a caption and hashtags
4. Click 'Publish Now' or schedule for a future time
5. The post is published to the connected Instagram account

**Screencast:** Shows creating and publishing a post to Instagram.

---

## instagram_business_manage_insights

**Description:**
Cloudless uses instagram_business_manage_insights to retrieve analytics data for the connected Instagram Business account and its published media. This includes account-level insights (reach, impressions, follower demographics) and per-media insights (likes, comments, shares, saves, reach).

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Analytics → Instagram
3. View account-level insights and per-media insights

**Screencast:** Shows the Instagram analytics dashboard.

---

## instagram_business_manage_comments

**Description:**
Cloudless uses instagram_business_manage_comments to help social media managers moderate and respond to comments on their Instagram Business account posts. This enables listing comments, replying to comments, hiding inappropriate comments, and deleting spam comments.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Navigate to a published Instagram post
3. View comments, reply to a comment, hide or delete a comment

**Screencast:** Shows moderating Instagram comments.

---

## instagram_manage_messages

**Description:**
Cloudless uses instagram_manage_messages to manage Instagram Direct Messages through our unified inbox. This allows businesses to read, respond to, and organize Instagram DMs from a single dashboard alongside their Messenger and other social media conversations.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to the Messenger/Inbox section
3. Select the Instagram tab
4. View and respond to Instagram DMs

**Screencast:** Shows managing Instagram DMs in the unified inbox.

---

## threads_basic

**Description:**
Cloudless uses threads_basic to publish text and media posts to connected Threads accounts. This allows businesses to maintain a presence on Threads alongside their other social media platforms, all managed from a single dashboard.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Accounts and connect a Threads account
3. Create a new post and select Threads as the target
4. Publish the post to Threads

**Screencast:** Shows publishing a post to Threads.

---

## instagram_content_publish

**Description:**
Cloudless uses instagram_content_publish to publish content to Instagram Business accounts. This is the legacy permission name for the same functionality as instagram_business_content_publish — publishing images, videos, carousels, and stories.

**How to test:**
Same as instagram_business_content_publish above.

**Screencast:** Same as instagram_business_content_publish.

---

## instagram_manage_insights

**Description:**
Cloudless uses instagram_manage_insights to retrieve analytics data for Instagram accounts. This is the legacy permission name for the same functionality as instagram_business_manage_insights.

**How to test:**
Same as instagram_business_manage_insights above.

**Screencast:** Same as instagram_business_manage_insights.

---

## instagram_basic

**Description:**
Cloudless uses instagram_basic to read basic profile information for connected Instagram accounts. This is the legacy permission name for the same functionality as instagram_business_basic.

**How to test:**
Same as instagram_business_basic above.

**Screencast:** Same as instagram_business_basic.

---

## instagram_business_manage_comments

**Description:**
Cloudless uses instagram_business_manage_comments to moderate and respond to comments on Instagram Business account posts. This enables listing, replying, hiding, and deleting comments.

**How to test:**
Same as instagram_business_manage_comments above.

**Screencast:** Same as instagram_business_manage_comments.

---

## read_insights

**Description:**
Cloudless uses read_insights to retrieve analytics data for Facebook Pages and Instagram accounts, including reach, impressions, engagement metrics, and follower demographics. This data powers our analytics dashboard and content performance recommendations.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Analytics
3. View Facebook Page and Instagram insights

**Screencast:** Shows the analytics dashboard with insights data.

---

## ads_read

**Description:**
Cloudless uses ads_read to read advertising analytics data for connected ad accounts, allowing businesses to track ad performance alongside their organic social media metrics in a unified analytics dashboard.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Analytics → Ads
3. View ad performance metrics

**Screencast:** Shows the ads analytics dashboard.

---

## ads_management

**Description:**
Cloudless uses ads_management to create, edit, and manage advertising campaigns for connected ad accounts. This allows businesses to manage their social media advertising alongside their organic content from a single platform.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to Ads → Create Campaign
3. Create and launch an ad campaign

**Screencast:** Shows creating and managing an ad campaign.

---

## public_profile

**Description:**
Cloudless uses public_profile to read the user's basic profile information (name, profile picture) after they log in with Facebook. This is used to personalize the platform experience and display the user's identity in the dashboard.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. The user's name and profile picture appear in the header
3. This information is retrieved via the public_profile permission

**Screencast:** Shows the user profile in the dashboard.

---

## Human Agent

**Description:**
Cloudless uses Human Agent to allow businesses to respond to Instagram and Facebook messages outside the 24-hour messaging window when a human agent is handling the conversation. This is used when the AI auto-reply is paused and a human takes over the conversation. The Human Agent tag ensures messages are delivered even outside the standard messaging window.

**How to test:**
1. Visit https://social.cloudless.gr and log in
2. Go to the Messenger inbox
3. Select a conversation and pause AI auto-reply
4. Type a manual reply — it is sent with the HUMAN_AGENT tag
5. The message is delivered even if the 24-hour window has passed

**Screencast:** Shows a human agent replying to a conversation with the HUMAN_AGENT tag.
