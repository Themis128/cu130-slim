# Meta organic growth through SocialAuto

SocialAuto can prepare and queue one valid media-backed post for connected
Facebook, Instagram, and Threads accounts without creating an ad or spending
money.

## Check readiness

Call `GET /api/v1/meta-growth/readiness` with the team context. The response
lists connected Meta accounts, free actions supported by SocialAuto, and
actions that Meta does not expose through a supported API.

## Queue an organic campaign

Call `POST /api/v1/meta-growth/organic-campaign`:

```json
{
  "content_text": "Follow Cloudless for practical cloud and AI automation tips.",
  "media_ids": ["MEDIA_ASSET_UUID"],
  "link_url": "https://cloudless.gr",
  "target_account_ids": ["FACEBOOK_ACCOUNT_UUID", "INSTAGRAM_ACCOUNT_UUID", "THREADS_ACCOUNT_UUID"],
  "scheduled_at": "2026-10-09T09:00:00+03:00",
  "hashtags": ["cloudless", "selfhosting", "automation"]
}
```

The worker validates the media for each platform and publishes only to active
owned accounts. If `target_account_ids` is omitted, all active Meta accounts in
the team are selected.

## Important limits

- A media asset is required; SocialAuto never degrades a Meta campaign into a
  text-only post.
- Page-follower invitations are not available through the supported Meta API.
  The readiness response reports this instead of pretending an invitation was
  sent.
- Boosts and ads are intentionally excluded. They require an explicit budget,
  audience, objective, and spend approval.
