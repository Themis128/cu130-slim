{{ config(materialized='table') }}

select *
from {{ source('social', 'mv_publish_success') }}
