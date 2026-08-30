-- Aggregate-only verification for the ChatGPT Plus/Codex private worker bridge.
-- Run after 20260830110000_personal_chatgpt_plus_worker.sql.
-- This query never returns personal payloads, hashes, IDs, prompts, or model text.
with checks(check_name, passed) as (
  select 'worker_run_table_exists', to_regclass('public.personal_strategy_worker_runs') is not null
  union all
  select 'worker_publish_rpc_exists', to_regprocedure(
    'public.personal_publish_strategy_worker_result(text,text,text,jsonb,jsonb)'
  ) is not null
  union all
  select 'anon_cannot_execute_worker_rpc', not has_function_privilege(
    'anon',
    'public.personal_publish_strategy_worker_result(text,text,text,jsonb,jsonb)',
    'EXECUTE'
  )
  union all
  select 'authenticated_can_execute_worker_rpc', has_function_privilege(
    'authenticated',
    'public.personal_publish_strategy_worker_result(text,text,text,jsonb,jsonb)',
    'EXECUTE'
  )
  union all
  select 'authenticated_has_no_direct_worker_table_privileges', not (
    has_table_privilege('authenticated', 'public.personal_strategy_worker_runs', 'SELECT')
    or has_table_privilege('authenticated', 'public.personal_strategy_worker_runs', 'INSERT')
    or has_table_privilege('authenticated', 'public.personal_strategy_worker_runs', 'UPDATE')
    or has_table_privilege('authenticated', 'public.personal_strategy_worker_runs', 'DELETE')
  )
  union all
  select 'worker_rpc_is_security_definer', exists (
    select 1
      from pg_proc proc
     where proc.oid = 'public.personal_publish_strategy_worker_result(text,text,text,jsonb,jsonb)'::regprocedure
       and proc.prosecdef
  )
  union all
  -- PostgreSQL stores a list-valued search_path setting canonically as
  -- `search_path=pg_catalog, public` on many hosted versions. Normalize
  -- whitespace before testing the exact trusted schema list so a display
  -- space after the comma cannot produce a false negative.
  select 'worker_rpc_has_locked_search_path', exists (
    select 1
      from pg_proc proc,
           unnest(coalesce(proc.proconfig, array[]::text[])) as setting(value)
     where proc.oid = 'public.personal_publish_strategy_worker_result(text,text,text,jsonb,jsonb)'::regprocedure
       and regexp_replace(setting.value, '[[:space:]]+', '', 'g') = 'search_path=pg_catalog,public'
  )
  union all
  select 'worker_table_is_owner_scoped_and_rls_enabled', exists (
    select 1
      from pg_class rel
      join pg_namespace namespace on namespace.oid = rel.relnamespace
     where namespace.nspname = 'public'
       and rel.relname = 'personal_strategy_worker_runs'
       and rel.relrowsecurity
       and exists (
         select 1
           from pg_attribute attr
          where attr.attrelid = rel.oid
            and attr.attname = 'owner_user_id'
            and not attr.attisdropped
       )
  )
  union all
  select 'worker_rpc_returns_jsonb', exists (
    select 1
      from pg_proc proc
     where proc.oid = 'public.personal_publish_strategy_worker_result(text,text,text,jsonb,jsonb)'::regprocedure
       and proc.prorettype = 'jsonb'::regtype
  )
  union all
  select 'worker_rpc_does_not_reference_platform_key', position(
    'OPENAI_API_KEY' in pg_get_functiondef(
      'public.personal_publish_strategy_worker_result(text,text,text,jsonb,jsonb)'::regprocedure
    )
  ) = 0
)
select count(*) as total_checks,
       count(*) filter (where passed) as passed_checks,
       count(*) filter (where not passed) as failed_checks,
       coalesce(jsonb_agg(check_name order by check_name) filter (where not passed), '[]'::jsonb) as failed_check_names
  from checks;
