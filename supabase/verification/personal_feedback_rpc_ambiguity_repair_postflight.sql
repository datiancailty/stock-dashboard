-- Aggregate-only postflight for the Part 6 feedback RPC ambiguity repair.
-- Run only after 20260830120000_personal_feedback_rpc_ambiguity_repair.sql.
-- It reads catalog metadata only; it does not invoke either feedback RPC and
-- never returns personal feedback payloads, IDs, hashes, or row contents.

with functions(signature) as (
  values
    ('personal_append_strategy_feedback(jsonb)'),
    ('personal_append_strategy_feedback_v2(jsonb)')
),
checks(check_name, passed) as (
  select 'all_feedback_rpc_versions_exist', count(*) = 2
    from functions
   where to_regprocedure('public.' || signature) is not null
  union all
  select 'anon_cannot_execute_feedback_rpcs', count(*) = 0
    from functions
   where has_function_privilege('anon', 'public.' || signature, 'EXECUTE')
  union all
  select 'authenticated_can_execute_feedback_rpcs', count(*) = 2
    from functions
   where has_function_privilege('authenticated', 'public.' || signature, 'EXECUTE')
  union all
  select 'feedback_v2_is_security_definer', exists (
    select 1
      from pg_proc proc
     where proc.oid = 'public.personal_append_strategy_feedback_v2(jsonb)'::regprocedure
       and proc.prosecdef
  )
  union all
  select 'feedback_v2_has_constrained_digest_search_path', exists (
    select 1
      from pg_proc proc,
           unnest(coalesce(proc.proconfig, array[]::text[])) as setting(value)
     where proc.oid = 'public.personal_append_strategy_feedback_v2(jsonb)'::regprocedure
       and regexp_replace(setting.value, '[[:space:]]+', '', 'g')
           ~ '^search_path=pg_catalog,[^,]+,public$'
  )
  union all
  select 'feedback_v2_returns_jsonb', exists (
    select 1
      from pg_proc proc
     where proc.oid = 'public.personal_append_strategy_feedback_v2(jsonb)'::regprocedure
       and proc.prorettype = 'jsonb'::regtype
  )
  union all
  select 'feedback_v2_uses_unambiguous_locals', position(
    'v_source_id text;' in pg_get_functiondef(
      'public.personal_append_strategy_feedback_v2(jsonb)'::regprocedure
    )
  ) > 0
  union all
  select 'feedback_v2_uses_named_primary_key_conflict_target', position(
    'on conflict on constraint personal_strategy_feedback_pkey do nothing' in lower(pg_get_functiondef(
      'public.personal_append_strategy_feedback_v2(jsonb)'::regprocedure
    ))
  ) > 0
  union all
  select 'legacy_feedback_rpc_delegates_to_v2', position(
    'personal_append_strategy_feedback_v2' in pg_get_functiondef(
      'public.personal_append_strategy_feedback(jsonb)'::regprocedure
    )
  ) > 0
  union all
  select 'authenticated_has_no_direct_feedback_table_dml', not (
    has_table_privilege('authenticated', 'public.personal_strategy_feedback', 'INSERT')
    or has_table_privilege('authenticated', 'public.personal_strategy_feedback', 'UPDATE')
    or has_table_privilege('authenticated', 'public.personal_strategy_feedback', 'DELETE')
  )
  union all
  select 'feedback_table_is_owner_scoped_and_rls_enabled', exists (
    select 1
      from pg_class rel
      join pg_namespace namespace on namespace.oid = rel.relnamespace
     where namespace.nspname = 'public'
       and rel.relname = 'personal_strategy_feedback'
       and rel.relrowsecurity
       and exists (
         select 1
           from pg_attribute attr
          where attr.attrelid = rel.oid
            and attr.attname = 'owner_user_id'
            and not attr.attisdropped
       )
  )
)
select count(*) as total_checks,
       count(*) filter (where passed) as passed_checks,
       count(*) filter (where not passed) as failed_checks,
       coalesce(jsonb_agg(check_name order by check_name) filter (where not passed), '[]'::jsonb) as failed_check_names
  from checks;
