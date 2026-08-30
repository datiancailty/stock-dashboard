-- Aggregate postflight for the owner-scoped personal dashboard legacy import.
-- Returns one row and never returns record payloads, IDs, prices, positions or text.
with target as (
  select user_id
    from public.app_usernames
   where username_norm = 'admin' and status = 'active'
),
expected_tables(name) as (
  values
    ('personal_import_batches'), ('personal_documents'),
    ('personal_watchlist_items'), ('personal_news_items'),
    ('personal_trade_records'), ('personal_strategy_feedback'),
    ('personal_strategy_recommendations')
),
expected_functions(signature) as (
  values
    ('personal_current_user_is_active()'),
    ('personal_get_migration_state()'), ('personal_get_part1()'),
    ('personal_get_part2()'), ('personal_get_part4()'),
    ('personal_get_part5()'), ('personal_get_part6()')
),
checks(check_name, passed) as (
  select 'active_admin_owner', (select count(*) = 1 from target)
  union all
  select 'all_personal_tables_exist', count(*) = 7
    from expected_tables where to_regclass('public.' || name) is not null
  union all
  select 'all_personal_tables_have_rls', count(*) = 7
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relname in (select name from expected_tables)
     and c.relrowsecurity
  union all
  select 'anon_has_no_direct_table_privileges', count(*) = 0
    from expected_tables
   where has_table_privilege('anon', 'public.' || name, 'SELECT,INSERT,UPDATE,DELETE')
  union all
  select 'authenticated_has_no_direct_table_privileges', count(*) = 0
    from expected_tables
   where has_table_privilege('authenticated', 'public.' || name, 'SELECT,INSERT,UPDATE,DELETE')
  union all
  select 'all_personal_rpcs_exist', count(*) = 7
    from expected_functions where to_regprocedure('public.' || signature) is not null
  union all
  select 'anon_cannot_execute_personal_rpcs', count(*) = 0
    from expected_functions
   where has_function_privilege('anon', 'public.' || signature, 'EXECUTE')
  union all
  select 'authenticated_can_execute_personal_rpcs', count(*) = 7
    from expected_functions
   where has_function_privilege('authenticated', 'public.' || signature, 'EXECUTE')
  union all
  select 'source_file_count_13', count(*) = 13
    from public.personal_import_batches where owner_user_id = (select user_id from target)
  union all
  select 'document_count_12', count(*) = 12
    from public.personal_documents where owner_user_id = (select user_id from target)
  union all
  select 'watchlist_count_20', count(*) = 20
    from public.personal_watchlist_items where owner_user_id = (select user_id from target)
  union all
  select 'news_count_8', count(*) = 8
    from public.personal_news_items where owner_user_id = (select user_id from target)
  union all
  select 'trade_count_384', count(*) = 384
    from public.personal_trade_records where owner_user_id = (select user_id from target)
  union all
  select 'feedback_count_9', count(*) = 9
    from public.personal_strategy_feedback where owner_user_id = (select user_id from target)
  union all
  select 'recommendation_count_46', count(*) = 46
    from public.personal_strategy_recommendations where owner_user_id = (select user_id from target)
  union all
  select 'watchlist_id_digest_matches', exists (
    select 1 from public.personal_import_batches b
     where b.owner_user_id = (select user_id from target)
       and b.source_path = 'data/stocks.json'
       and b.source_record_count = (select count(*) from public.personal_watchlist_items w where w.owner_user_id = b.owner_user_id)
       and b.stable_id_set_sha256 = (select encode(extensions.digest(string_agg(w.source_id, E'\n' order by w.source_id), 'sha256'), 'hex') from public.personal_watchlist_items w where w.owner_user_id = b.owner_user_id)
  )
  union all
  select 'news_id_digest_matches', exists (
    select 1 from public.personal_import_batches b
     where b.owner_user_id = (select user_id from target)
       and b.source_path = 'data/news-memory.json'
       and b.source_record_count = (select count(*) from public.personal_news_items n where n.owner_user_id = b.owner_user_id)
       and b.stable_id_set_sha256 = (select encode(extensions.digest(string_agg(n.source_id, E'\n' order by n.source_id), 'sha256'), 'hex') from public.personal_news_items n where n.owner_user_id = b.owner_user_id)
  )
  union all
  select 'trade_id_digest_matches', exists (
    select 1 from public.personal_import_batches b
     where b.owner_user_id = (select user_id from target)
       and b.source_path = 'data/trade-records.json'
       and b.source_record_count = (select count(*) from public.personal_trade_records t where t.owner_user_id = b.owner_user_id)
       and b.stable_id_set_sha256 = (select encode(extensions.digest(string_agg(t.source_id, E'\n' order by t.source_id), 'sha256'), 'hex') from public.personal_trade_records t where t.owner_user_id = b.owner_user_id)
  )
  union all
  select 'feedback_id_digest_matches', exists (
    select 1 from public.personal_import_batches b
     where b.owner_user_id = (select user_id from target)
       and b.source_path = 'data/strategy-feedback.json'
       and b.source_record_count = (select count(*) from public.personal_strategy_feedback f where f.owner_user_id = b.owner_user_id)
       and b.stable_id_set_sha256 = (select encode(extensions.digest(string_agg(f.source_id, E'\n' order by f.source_id), 'sha256'), 'hex') from public.personal_strategy_feedback f where f.owner_user_id = b.owner_user_id)
  )
  union all
  select 'recommendation_id_digest_matches', exists (
    select 1 from public.personal_import_batches b
     where b.owner_user_id = (select user_id from target)
       and b.source_path = 'data/strategy-recommendations.json'
       and b.source_record_count = (select count(*) from public.personal_strategy_recommendations r where r.owner_user_id = b.owner_user_id)
       and b.stable_id_set_sha256 = (select encode(extensions.digest(string_agg(r.source_id, E'\n' order by r.source_id), 'sha256'), 'hex') from public.personal_strategy_recommendations r where r.owner_user_id = b.owner_user_id)
  )
  union all
  select 'all_import_rows_owned_by_admin', (
    (select count(distinct owner_user_id) from public.personal_import_batches) <= 1
    and not exists (select 1 from public.personal_import_batches where owner_user_id <> (select user_id from target))
    and not exists (select 1 from public.personal_documents where owner_user_id <> (select user_id from target))
    and not exists (select 1 from public.personal_watchlist_items where owner_user_id <> (select user_id from target))
    and not exists (select 1 from public.personal_news_items where owner_user_id <> (select user_id from target))
    and not exists (select 1 from public.personal_trade_records where owner_user_id <> (select user_id from target))
    and not exists (select 1 from public.personal_strategy_feedback where owner_user_id <> (select user_id from target))
    and not exists (select 1 from public.personal_strategy_recommendations where owner_user_id <> (select user_id from target))
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
