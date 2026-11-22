-- LinkedIn post → engagement → booking attribution chain
-- Run in Metabase against the SocialAuto Postgres.
-- Sources: posts/post_targets (org posts), meta_data.linkedin_stats (CM
-- socialMetadata from linkedin_engagement_score), engagement_candidates
-- (commenters), and downstream the audit-booking surface (consultation
-- bookings are captured by the Cal.com webhook → conversionEvents).

-- 1. Per-post engagement snapshot (from the hourly scorer's writes)
SELECT
  pt.platform_post_id                         AS post_urn,
  p.id                                        AS post_id,
  LEFT(COALESCE(p.content_text, ''), 120)     AS content,
  pt.published_at,
  (p.meta_data->'linkedin_stats'->>'impressionCount')::int    AS impressions,
  (p.meta_data->'linkedin_stats'->>'likeCount')::int          AS reactions,
  (p.meta_data->'linkedin_stats'->>'commentCount')::int       AS comments,
  (p.meta_data->'linkedin_stats'->>'shareCount')::int         AS shares,
  (p.meta_data->>'linkedin_score')::numeric                   AS weighted_er,
  (p.meta_data->>'boost_candidate')::boolean                  AS boosted
FROM post_targets pt
JOIN posts p ON p.id = pt.post_id
JOIN social_accounts sa ON sa.id = pt.social_account_id
WHERE sa.platform = 'linkedin'
  AND sa.account_type = 'organization'
  AND pt.status = 'published'
  AND pt.published_at > now() - interval '30 days'
ORDER BY pt.published_at DESC;

-- 2. Top commenters → warm leads (from the same scorer)
SELECT
  meta_data->>'org_post_urn'                  AS post_urn,
  author_handle                               AS commenter,
  LEFT(post_text, 160)                        AS comment,
  posted_at                                   AS commented_at,
  in_crm,
  is_connection
FROM engagement_candidates
WHERE platform = 'linkedin'
  AND meta_data ? 'org_post_urn'
ORDER BY posted_at DESC;

-- 3. Booking-side — join on UTM once the conversions table lands.
--    Until then the webhook logs stream audit_booked; surface count-by-day:
SELECT date_trunc('day', created_at) AS day, count(*) AS audit_bookings
FROM <audit_bookings_table>
GROUP BY 1 ORDER BY 1 DESC;
