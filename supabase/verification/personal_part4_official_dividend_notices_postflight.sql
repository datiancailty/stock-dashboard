-- Aggregate-only postflight for 20260831010000_personal_part4_official_dividend_notices.sql.
-- Run manually in Supabase SQL Editor AFTER the forward migration commits and
-- BEFORE the first local official-announcement sync.
-- It returns check names/counts only; it never returns private calendar events,
-- titles, stock lists, account IDs, source URLs, credentials, or raw payloads.

with target as (
  select to_regprocedure(
    'public.personal_sync_part4_dividend_notices(text,text,text,integer,integer,integer,jsonb,text,text,jsonb)'
  ) as sync_proc,
  to_regprocedure('public.personal_get_part4()') as part4_proc,
  to_regprocedure('public.personal_sync_market_snapshot(text,jsonb,text)') as market_snapshot_proc,
  to_regprocedure('public.personal_sync_future_dividend_grid(text,jsonb,text)') as future_dividend_proc,
  to_regprocedure('public.personal_replace_watchlist(jsonb)') as watchlist_replace_proc
), tables as (
  select 'personal_part4_dividend_notices'::text as name,
         to_regclass('public.personal_part4_dividend_notices') as rel_oid
  union all
  select 'personal_part4_dividend_notice_sync_runs'::text,
         to_regclass('public.personal_part4_dividend_notice_sync_runs')
  union all
  select 'personal_part4_dividend_notice_run_items'::text,
         to_regclass('public.personal_part4_dividend_notice_run_items')
  union all
  select 'personal_part4_sync_writer_credentials'::text,
         to_regclass('public.personal_part4_sync_writer_credentials')
  union all
  select 'personal_market_quote_snapshots'::text,
         to_regclass('public.personal_market_quote_snapshots')
  union all
  select 'personal_future_dividend_grid_snapshots'::text,
         to_regclass('public.personal_future_dividend_grid_snapshots')
  union all
  select 'personal_part4_market_document_baselines'::text,
         to_regclass('public.personal_part4_market_document_baselines')
), function_info as (
  select target.sync_proc,
         target.part4_proc,
         target.market_snapshot_proc,
         target.future_dividend_proc,
         case when target.sync_proc is null then null else pg_get_functiondef(target.sync_proc::oid) end as sync_definition,
         case when target.part4_proc is null then null else pg_get_functiondef(target.part4_proc::oid) end as part4_definition,
         case when target.market_snapshot_proc is null then null else pg_get_functiondef(target.market_snapshot_proc::oid) end as market_snapshot_definition,
         case when target.future_dividend_proc is null then null else pg_get_functiondef(target.future_dividend_proc::oid) end as future_dividend_definition
    from target
), owner as (
  select user_id
    from public.app_usernames
   where username_norm = 'admin' and status = 'active'
), checks(check_name, passed) as (
  select 'active_admin_owner_exists', (select count(*) = 1 from owner)
  union all
  select 'sync_rpc_exists', target.sync_proc is not null from target
  union all
  select 'market_snapshot_rpc_exists', target.market_snapshot_proc is not null from target
  union all
  select 'future_dividend_rpc_exists', target.future_dividend_proc is not null from target
  union all
  select 'watchlist_replace_rpc_uses_snapshot_lock', coalesce(
    has_function_privilege('authenticated', target.watchlist_replace_proc::oid, 'EXECUTE')
      and not has_function_privilege('anon', target.watchlist_replace_proc::oid, 'EXECUTE')
      and position('personal_snapshot:' in pg_get_functiondef(target.watchlist_replace_proc::oid)) > 0,
    false
  ) from target
  union all
  select 'market_snapshot_rpc_authenticated_only_with_trusted_writer', coalesce(
    has_function_privilege('authenticated', target.market_snapshot_proc::oid, 'EXECUTE')
      and not has_function_privilege('anon', target.market_snapshot_proc::oid, 'EXECUTE')
      and position('market_snapshot_trusted_writer_required' in pg_get_functiondef(target.market_snapshot_proc::oid)) > 0
      and exists (
        select 1 from pg_proc p
         where p.oid = target.market_snapshot_proc::oid and p.prosecdef
           and exists (
             select 1 from unnest(coalesce(p.proconfig, array[]::text[])) setting(value)
              where regexp_replace(setting.value, '[[:space:]]+', '', 'g') = 'search_path=pg_catalog,public'
           )
      ),
    false
  ) from target
  union all
  select 'future_dividend_rpc_authenticated_only_with_strict_future_contract', coalesce(
    has_function_privilege('authenticated', target.future_dividend_proc::oid, 'EXECUTE')
      and not has_function_privilege('anon', target.future_dividend_proc::oid, 'EXECUTE')
      and position('future_dividend_trusted_writer_required' in pg_get_functiondef(target.future_dividend_proc::oid)) > 0
      and position('future_dividend_coverage_incomplete' in pg_get_functiondef(target.future_dividend_proc::oid)) > 0
      and position('future_dividend_time_regression' in pg_get_functiondef(target.future_dividend_proc::oid)) > 0
      and position('已公告待实施' in pg_get_functiondef(target.future_dividend_proc::oid)) > 0
      and exists (
        select 1 from pg_proc p
         where p.oid = target.future_dividend_proc::oid and p.prosecdef
           and exists (
             select 1 from unnest(coalesce(p.proconfig, array[]::text[])) setting(value)
              where regexp_replace(setting.value, '[[:space:]]+', '', 'g') = 'search_path=pg_catalog,public'
           )
      ),
    false
  ) from target
  union all
  select 'part4_read_rpc_exists', target.part4_proc is not null from target
  union all
  select 'all_private_ledger_and_integrity_tables_exist', not exists (
    select 1 from tables where rel_oid is null
  )
  union all
  select 'ledger_tables_have_rls_and_owner_column', not exists (
    select 1
      from tables t
      left join pg_class rel on rel.oid = t.rel_oid::oid
      left join pg_attribute attr on attr.attrelid = rel.oid
                                   and attr.attname = 'owner_user_id'
                                   and not attr.attisdropped
     where t.rel_oid is null or not rel.relrowsecurity or attr.attnum is null
  )
  union all
  select 'authenticated_has_no_direct_ledger_table_privileges', coalesce(not exists (
    select 1 from tables
     where rel_oid is null
        or has_table_privilege('authenticated', rel_oid::oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')
  ), false)
  union all
  select 'anon_cannot_execute_sync_rpc', coalesce(
    not has_function_privilege('anon', target.sync_proc::oid, 'EXECUTE'), false
  ) from target
  union all
  select 'authenticated_can_execute_sync_rpc', coalesce(
    has_function_privilege('authenticated', target.sync_proc::oid, 'EXECUTE'), false
  ) from target
  union all
  select 'sync_rpc_is_security_definer', exists (
    select 1 from pg_proc p join target on true
     where p.oid = target.sync_proc::oid and p.prosecdef
  )
  union all
  select 'sync_rpc_has_locked_search_path', exists (
    select 1
      from pg_proc p
      join target on true,
           unnest(coalesce(p.proconfig, array[]::text[])) as setting(value)
     where p.oid = target.sync_proc::oid
       and regexp_replace(setting.value, '[[:space:]]+', '', 'g') = 'search_path=pg_catalog,public'
  )
  union all
  select 'part4_read_rpc_is_security_definer_and_authenticated_only', coalesce(
    has_function_privilege('authenticated', target.part4_proc::oid, 'EXECUTE')
      and not has_function_privilege('anon', target.part4_proc::oid, 'EXECUTE')
      and exists (select 1 from pg_proc p where p.oid = target.part4_proc::oid and p.prosecdef),
    false
  ) from target
  union all
  select 'sync_rpc_definition_enforces_watchlist_coverage', coalesce(
    position('part4_sync_watchlist_coverage_incomplete' in function_info.sync_definition) > 0
      and position('personal_watchlist_items' in function_info.sync_definition) > 0,
    false
  ) from function_info
  union all
  -- The RPC enforces the official identifier and URL with anchored regexes.
  -- Probe stable exception markers rather than a rendered regex fragment: PostgreSQL
  -- preserves backslash escaping in pg_get_functiondef(), so literal URL substring
  -- probes are not portable across Hosted formatting versions.
  select 'sync_rpc_definition_enforces_official_source_identity', coalesce(
    position('part4_sync_event_id_invalid' in function_info.sync_definition) > 0
      and position('part4_sync_event_source_invalid' in function_info.sync_definition) > 0
      and position('part4_sync_event_source_url_invalid' in function_info.sync_definition) > 0,
    false
  ) from function_info
  union all
  select 'part4_read_rpc_merges_separate_ledger_without_base_rewrite', coalesce(
    position('personal_part4_dividend_notices' in function_info.part4_definition) > 0
      and position('jsonb_set' in function_info.part4_definition) > 0
      and position('update public.personal_documents' in lower(function_info.part4_definition)) = 0,
    false
  ) from function_info
  union all
  select 'part4_read_rpc_filters_to_current_private_watchlist_and_overlays_private_snapshots', coalesce(
    position('personal_market_quote_snapshots' in function_info.part4_definition) > 0
      and position('personal_future_dividend_grid_snapshots' in function_info.part4_definition) > 0
      and position('personal_watchlist_items w' in function_info.part4_definition) > 0,
    false
  ) from function_info
  union all
  select 'sync_rpc_definition_requires_trusted_local_writer_capability', coalesce(
    position('part4_sync_trusted_writer_required' in function_info.sync_definition) > 0
      and position('personal_part4_sync_writer_credentials' in function_info.sync_definition) > 0,
    false
  ) from function_info
  union all
  select 'base_private_market_document_matches_migration_fingerprint', exists (
    select 1
      from public.personal_part4_market_document_baselines b
      join public.personal_documents d
        on d.owner_user_id = b.owner_user_id and d.document_key = 'market'
     where b.owner_user_id = (select user_id from owner)
       and b.market_sha256 = encode(extensions.digest(d.payload::text, 'sha256'), 'hex')
  )
  union all
  select 'notice_ledger_empty_before_first_sync', (select count(*) = 0 from public.personal_part4_dividend_notices)
  union all
  select 'sync_run_ledger_empty_before_first_sync', (select count(*) = 0 from public.personal_part4_dividend_notice_sync_runs)
)
select count(*) as total_checks,
       count(*) filter (where passed) as passed_checks,
       count(*) filter (where not passed) as failed_checks,
       coalesce(jsonb_agg(check_name order by check_name) filter (where not passed), '[]'::jsonb) as failed_check_names
  from checks;
