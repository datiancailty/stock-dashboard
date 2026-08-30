-- Aggregate-only verification for the private 000423 dividend-calendar patch.
-- Run after the private market event patch SQL has committed.
-- This query returns no payloads, prices, IDs, or announcement text.
with target as (
  select user_id
    from public.app_usernames
   where username_norm = 'admin' and status = 'active'
),
checks(check_name, passed) as (
  select 'active_admin_owner', (select count(*) = 1 from target)
  union all
  select 'private_market_has_20_stocks_90_events', exists (
    select 1
      from public.personal_documents d
     where d.owner_user_id = (select user_id from target)
       and d.document_key = 'market'
       and jsonb_array_length(d.payload->'stocks') = 20
       and jsonb_array_length(d.payload->'events') = 90
  )
  union all
  select 'dong_e_e_jiao_current_distribution_has_three_events', (
    select count(*) = 3
      from public.personal_documents d,
           jsonb_array_elements(d.payload->'events') event
     where d.owner_user_id = (select user_id from target)
       and d.document_key = 'market'
       and event->>'code' = '000423'
       and event->>'date' in ('2026-08-28', '2026-08-31')
       and event->>'type' in ('股权登记日', '除权除息日', '派息日')
  )
  union all
  select 'dong_e_e_jiao_pay_and_ex_date_are_present', (
    select count(*) = 2
      from public.personal_documents d,
           jsonb_array_elements(d.payload->'events') event
     where d.owner_user_id = (select user_id from target)
       and d.document_key = 'market'
       and event->>'code' = '000423'
       and event->>'date' = '2026-08-31'
       and event->>'type' in ('除权除息日', '派息日')
  )
  union all
  select 'vps_namespace_still_present',
    to_regclass('public.vps_private_scopes') is not null
)
select count(*) as total_checks,
       count(*) filter (where passed) as passed_checks,
       count(*) filter (where not passed) as failed_checks,
       coalesce(jsonb_agg(check_name order by check_name) filter (where not passed), '[]'::jsonb) as failed_check_names
  from checks;
