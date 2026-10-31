{{ config(materialized='table') }}

select
    c.id as conversion_id,
    c.email,
    c.utm_campaign,
    c.utm_source,
    c.platform,
    c.value,
    c.currency,
    c.provider,
    c.created_at,
    l.id as lead_id,
    l.interest as lead_interest
from {{ source('social', 'funnel_conversions') }} c
left join {{ source('social', 'leads') }} l
    on lower(l.email) = lower(c.email)
