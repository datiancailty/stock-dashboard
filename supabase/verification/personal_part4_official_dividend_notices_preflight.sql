-- Aggregate-only preflight for 20260831010000_personal_part4_official_dividend_notices.sql.
-- Run manually in Supabase SQL Editor BEFORE the forward migration.
-- This returns only counts/booleans. It never returns private event payloads,
-- stock lists, titles, account IDs, credentials, or source URLs.

with target as (
  select user_id
    from public.app_usernames
   where username_norm = 'admin' and status = 'active'
), market as (
  select d.payload
    from public.personal_documents d
   where d.owner_user_id = (select user_id from target)
     and d.document_key = 'market'
)
select (select count(*) from target) as active_owner_count,
       (select count(*) from public.personal_watchlist_items w where w.owner_user_id = (select user_id from target)) as private_watchlist_count,
       coalesce((select jsonb_array_length(payload->'stocks') from market), 0) as base_market_stock_count,
       coalesce((select jsonb_array_length(payload->'events') from market), 0) as base_calendar_event_count,
       (to_regclass('public.personal_part4_dividend_notices') is not null) as notice_ledger_already_present,
       (to_regclass('public.personal_part4_dividend_notice_sync_runs') is not null) as sync_run_ledger_already_present,
       (to_regprocedure('public.personal_sync_part4_dividend_notices(text,text,text,integer,integer,integer,jsonb,text,text,jsonb)') is not null) as sync_rpc_already_present;
