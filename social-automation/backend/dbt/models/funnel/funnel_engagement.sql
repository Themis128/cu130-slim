{{ config(materialized='table') }}

select *
from {{ source('social', 'mv_engagement_rate') }}
