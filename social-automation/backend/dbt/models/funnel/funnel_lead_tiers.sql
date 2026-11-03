{{ config(materialized='table') }}

select
    l.id as lead_id,
    l.email,
    l.source,
    l.interest,
    l.thread_id,
    l.updated_at,
    case
        when l.interest = 'audit'
          or l.thread_id is not null
          or l.updated_at > now() - interval '7 days' then 'hot'
        when l.interest is not null and l.email is not null then 'warm'
        else 'cold'
    end as tier,
    now() as scored_at
from {{ source('social', 'leads') }} l
