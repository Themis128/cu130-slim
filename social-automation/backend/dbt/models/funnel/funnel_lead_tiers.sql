{{ config(materialized='table') }}

select
    l.id as lead_id,
    l.email,
    l.source,
    count(ea.id) filter (where ea.created_at > now() - interval '14 days') as recent_actions,
    case
        when count(ea.id) filter (where ea.created_at > now() - interval '14 days') >= 3 then 'hot'
        when count(ea.id) filter (where ea.created_at > now() - interval '14 days') >= 1 then 'warm'
        else 'cold'
    end as tier,
    now() as scored_at
from {{ source('social', 'leads') }} l
left join {{ source('social', 'engagement_actions') }} ea on ea.lead_id = l.id
group by l.id, l.email, l.source
