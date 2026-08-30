-- Aggregate-only verification for the forward Part 6 feedback RPC repair.
-- Run after 20260830100000_personal_feedback_rpc_repair.sql.
-- This query does not call the RPC and never returns feedback payloads or IDs.
with expected_functions(signature) as (
  values
    ('personal_append_strategy_feedback(jsonb)'),
    ('personal_append_strategy_feedback_v2(jsonb)')
),
checks(check_name, passed) as (
  select 'all_feedback_rpc_versions_exist', count(*) = 2
    from expected_functions
   where to_regprocedure('public.' || signature) is not null
  union all
  select 'anon_cannot_execute_feedback_rpcs', count(*) = 0
    from expected_functions
   where has_function_privilege('anon', 'public.' || signature, 'EXECUTE')
  union all
  select 'authenticated_can_execute_feedback_rpcs', count(*) = 2
    from expected_functions
   where has_function_privilege('authenticated', 'public.' || signature, 'EXECUTE')
  union all
  select 'feedback_rpc_has_locked_digest_path', exists (
    select 1
      from pg_proc proc
     where proc.oid = 'public.personal_append_strategy_feedback_v2(jsonb)'::regprocedure
       and array_to_string(proc.proconfig, E'\n') like 'search_path=pg_catalog,%'
  )
  union all
  select 'authenticated_has_no_direct_feedback_table_dml', not (
    has_table_privilege('authenticated', 'public.personal_strategy_feedback', 'INSERT')
    or has_table_privilege('authenticated', 'public.personal_strategy_feedback', 'UPDATE')
    or has_table_privilege('authenticated', 'public.personal_strategy_feedback', 'DELETE')
  )
  union all
  select 'personal_table_still_owner_scoped', to_regclass('public.personal_strategy_feedback') is not null
)
select count(*) as total_checks,
       count(*) filter (where passed) as passed_checks,
       count(*) filter (where not passed) as failed_checks,
       coalesce(jsonb_agg(check_name order by check_name) filter (where not passed), '[]'::jsonb) as failed_check_names
  from checks;
