-- Aggregate-only post-sync verification for the Part 4 official notice ledger.
-- Run manually in Supabase SQL Editor AFTER a successful local sync response.
-- It returns counts/check names only; no private payloads, titles, codes, URLs,
-- account IDs, tokens, or source hashes are returned.

with owner as (
  select user_id
    from public.app_usernames
   where username_norm = 'admin' and status = 'active'
), latest_run as (
  select r.*
    from public.personal_part4_dividend_notice_sync_runs r
   where r.owner_user_id = (select user_id from owner)
   order by r.updated_at desc, r.run_id desc
   limit 1
), checks(check_name, passed) as (
  select 'active_admin_owner_exists', (select count(*) = 1 from owner)
  union all
  select 'latest_sync_run_exists', exists (select 1 from latest_run)
  union all
  select 'latest_sync_covered_current_watchlist', exists (
    select 1 from latest_run r
     where r.expected_watchlist_count = (select count(*) from public.personal_watchlist_items w where w.owner_user_id = (select user_id from owner))
       and r.scanned_watchlist_count = r.expected_watchlist_count
  )
  union all
  select 'latest_sync_had_complete_official_discovery_before_selection', exists (
    select 1 from latest_run r
     where r.official_notice_count >= r.selected_notice_count
       and r.official_notice_count > 0
  )
  union all
  select 'latest_sync_selected_count_matches_run_manifest', exists (
    select 1 from latest_run r
     where r.selected_notice_count = (
       select count(*) from public.personal_part4_dividend_notice_run_items i
        where i.owner_user_id = r.owner_user_id and i.run_id = r.run_id
     )
  )
  union all
  select 'latest_run_source_ids_all_exist_in_active_ledger', not exists (
    select 1
      from latest_run r
      join public.personal_part4_dividend_notice_run_items i
        on i.owner_user_id = r.owner_user_id and i.run_id = r.run_id
      left join public.personal_part4_dividend_notices n
        on n.owner_user_id = i.owner_user_id and n.source_id = i.source_id and n.archived_at is null
     where n.source_id is null
  )
  union all
  select 'removed_watchlist_notices_are_archived', not exists (
    select 1
      from public.personal_part4_dividend_notices n
     where n.owner_user_id = (select user_id from owner)
       and n.archived_at is null
       and not exists (
         select 1 from public.personal_watchlist_items w
          where w.owner_user_id = n.owner_user_id and w.stock_code = n.stock_code
       )
  )
  union all
  select 'stored_notice_source_ids_and_urls_remain_constrained', not exists (
    select 1
      from public.personal_part4_dividend_notices n
     where n.owner_user_id = (select user_id from owner)
       and (n.source_id !~ '^eastmoney:AN[0-9]{12,32}$'
         or n.source_url !~ '^https://data\.eastmoney\.com/notices/detail/[0-9]{6}/AN[0-9]{12,32}\.html$')
  )
  union all
  select 'stored_notice_payloads_keep_supported_event_contract', not exists (
    select 1
      from public.personal_part4_dividend_notices n
     where n.owner_user_id = (select user_id from owner)
       and (jsonb_typeof(n.payload) <> 'object'
         or not (n.payload ?& array['id', 'date', 'code', 'name', 'type', 'stage', 'title', 'description', 'source', 'sourceUrl', 'sourceHash'])
         or (select count(*) from jsonb_object_keys(n.payload)) <> 11)
  )
  union all
  select 'base_market_document_fingerprint_matches_migration_baseline', exists (
    select 1
      from public.personal_part4_market_document_baselines b
      join public.personal_documents d
        on d.owner_user_id = b.owner_user_id and d.document_key = 'market'
     where b.owner_user_id = (select user_id from owner)
       and b.market_sha256 = encode(extensions.digest(d.payload::text, 'sha256'), 'hex')
  )
  union all
  select 'current_watchlist_has_complete_private_quote_snapshot', exists (
    select 1
      from public.personal_watchlist_items w
     where w.owner_user_id = (select user_id from owner)
  ) and not exists (
    select 1
      from public.personal_watchlist_items w
      left join public.personal_market_quote_snapshots q
        on q.owner_user_id = w.owner_user_id and q.stock_code = w.stock_code
     where w.owner_user_id = (select user_id from owner)
       and (q.stock_code is null or q.price <= 0 or q.as_of < now() - interval '3 days')
  )
  union all
  select 'current_watchlist_has_complete_private_future_grid_snapshot', exists (
    select 1
      from public.personal_watchlist_items w
     where w.owner_user_id = (select user_id from owner)
  ) and not exists (
    select 1
      from public.personal_watchlist_items w
      left join public.personal_future_dividend_grid_snapshots f
        on f.owner_user_id = w.owner_user_id and f.stock_code = w.stock_code
     where w.owner_user_id = (select user_id from owner)
       and (f.stock_code is null
         or f.future_dividend < 0
         or f.as_of < now() - interval '3 days'
         or (f.future_dividend = 0 and f.status is not null)
         or (f.future_dividend > 0 and f.status <> '已公告待实施'))
  )
)
select count(*) as total_checks,
       count(*) filter (where passed) as passed_checks,
       count(*) filter (where not passed) as failed_checks,
       coalesce(jsonb_agg(check_name order by check_name) filter (where not passed), '[]'::jsonb) as failed_check_names
  from checks;
