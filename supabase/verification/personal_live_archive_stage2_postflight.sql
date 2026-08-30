-- Aggregate postflight for the private live-archive overlay and restored controls.
-- Run only after the forward migration and private replacement import.
-- Returns one row and never returns payloads, IDs, titles, prices or text.
with target as (
  select user_id
    from public.app_usernames
   where username_norm = 'admin' and status = 'active'
),
expected_functions(signature) as (
  values
    ('personal_current_user_is_active()'),
    ('personal_get_migration_state()'),
    ('personal_get_part1()'),
    ('personal_get_part2()'),
    ('personal_get_part4()'),
    ('personal_get_part5()'),
    ('personal_get_part6()'),
    ('personal_replace_watchlist(jsonb)'),
    ('personal_append_trade_records(jsonb)'),
    ('personal_delete_trade_record(text)'),
    ('personal_append_strategy_feedback(jsonb)')
),
checks(check_name, passed) as (
  select 'active_admin_owner', (select count(*) = 1 from target)
  union all
  select 'live_source_path_constraint_present', exists (
    select 1
      from pg_constraint c
     where c.conrelid = 'public.personal_import_batches'::regclass
       and c.conname = 'personal_import_batches_path'
       and pg_get_constraintdef(c.oid) like '%live%'
  )
  union all
  select 'all_personal_rpcs_exist', count(*) = 11
    from expected_functions
   where to_regprocedure('public.' || signature) is not null
  union all
  select 'anon_cannot_execute_personal_rpcs', count(*) = 0
    from expected_functions
   where has_function_privilege('anon', 'public.' || signature, 'EXECUTE')
  union all
  select 'authenticated_can_execute_personal_rpcs', count(*) = 11
    from expected_functions
   where has_function_privilege('authenticated', 'public.' || signature, 'EXECUTE')
  union all
  select 'authenticated_has_no_direct_personal_table_dml', count(*) = 0
    from unnest(array[
      'personal_import_batches', 'personal_documents', 'personal_watchlist_items',
      'personal_news_items', 'personal_trade_records',
      'personal_strategy_feedback', 'personal_strategy_recommendations'
    ]) as names(name)
   where has_table_privilege('authenticated', 'public.' || name, 'SELECT,INSERT,UPDATE,DELETE')
  union all
  select 'live_market_batch_present', exists (
    select 1 from public.personal_import_batches b
     where b.owner_user_id = (select user_id from target)
       and b.source_path = 'live/market.json'
       and b.source_record_count is null
  )
  union all
  select 'live_news_batch_452', exists (
    select 1 from public.personal_import_batches b
     where b.owner_user_id = (select user_id from target)
       and b.source_path = 'live/news-memory.json'
       and b.source_record_count = 452
  )
  union all
  select 'live_strategy_batch_present', exists (
    select 1 from public.personal_import_batches b
     where b.owner_user_id = (select user_id from target)
       and b.source_path = 'live/strategy-analysis.json'
  )
  union all
  select 'live_api_health_batch_present', exists (
    select 1 from public.personal_import_batches b
     where b.owner_user_id = (select user_id from target)
       and b.source_path = 'live/strategy-api-health.json'
  )
  union all
  select 'watchlist_count_20', (
    select count(*) = 20 from public.personal_watchlist_items where owner_user_id = (select user_id from target)
  )
  union all
  select 'news_count_452', (
    select count(*) = 452 from public.personal_news_items where owner_user_id = (select user_id from target)
  )
  union all
  select 'trade_count_384', (
    select count(*) = 384 from public.personal_trade_records where owner_user_id = (select user_id from target)
  )
  union all
  select 'feedback_count_9', (
    select count(*) = 9 from public.personal_strategy_feedback where owner_user_id = (select user_id from target)
  )
  union all
  select 'recommendation_count_47', (
    select count(*) = 47 from public.personal_strategy_recommendations where owner_user_id = (select user_id from target)
  )
  union all
  select 'market_snapshot_20_stocks_87_events_19_boll', exists (
    select 1
      from public.personal_documents d
     where d.owner_user_id = (select user_id from target)
       and d.document_key = 'market'
       and jsonb_array_length(d.payload->'stocks') = 20
       and jsonb_array_length(d.payload->'events') = 87
       and (
         select count(*)
           from jsonb_array_elements(d.payload->'stocks') s
          where jsonb_typeof(s->'weeklyBoll') = 'object'
       ) = 19
  )
  union all
  select 'strategy_performance_present', exists (
    select 1
      from public.personal_documents d
     where d.owner_user_id = (select user_id from target)
       and d.document_key = 'strategy_analysis'
       and jsonb_typeof(d.payload->'recommendationPerformance') = 'object'
       and d.payload->'recommendationPerformance' ? 'successRate'
       and d.payload->'recommendationPerformance' ? 'avgTradingDaysToHit'
  )
  union all
  select 'vps_namespace_still_present',
    to_regclass('public.vps_private_scopes') is not null
    and to_regprocedure('public.vps_private_get_portfolio()') is not null
    and to_regprocedure('public.vps_submit_whitelist_revision(text[],text,bigint)') is not null
)
select count(*) as total_checks,
       count(*) filter (where passed) as passed_checks,
       count(*) filter (where not passed) as failed_checks,
       coalesce(jsonb_agg(check_name order by check_name) filter (where not passed), '[]'::jsonb) as failed_check_names
  from checks;
