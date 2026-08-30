-- Aggregate-only postflight for 20260830130000_personal_strategy_confidence_contract_v3.sql.
-- Run manually in Supabase SQL Editor AFTER the forward migration commits.
-- It returns only aggregate check names/counts. It never returns private analysis,
-- stock codes, prompts, feedback, hashes, user IDs, or Worker-run payloads.

with target as (
  select to_regprocedure(
    'public.personal_publish_strategy_worker_result(text,text,text,jsonb,jsonb)'
  ) as proc_oid
), tables as (
  select 'personal_strategy_worker_runs'::text as name,
         to_regclass('public.personal_strategy_worker_runs') as rel_oid
  union all
  select 'personal_documents'::text,
         to_regclass('public.personal_documents')
), function_info as (
  select target.proc_oid,
         case when target.proc_oid is null then null
              else pg_get_functiondef(target.proc_oid::oid)
          end as definition,
         case when target.proc_oid is null then null
              else regexp_replace(pg_get_functiondef(target.proc_oid::oid), '[[:space:]]+', '', 'g')
          end as compact_definition
    from target
), v3_documents as (
  select d.owner_user_id, d.payload
    from public.personal_documents d
   where d.document_key = 'strategy_analysis'
     and d.payload->>'schemaVersion' = '3'
), v3_confidence_entries as (
  select d.owner_user_id, d.payload #> '{briefCommand,confidence}' as value
    from v3_documents d
  union all
  select d.owner_user_id, advice_item.value->'confidence'
    from v3_documents d
   cross join lateral jsonb_array_elements(
     case when jsonb_typeof(d.payload->'advice') = 'array'
          then d.payload->'advice'
          else '[]'::jsonb
      end
   ) as advice_item(value)
), checks(check_name, passed) as (
  select 'worker_publish_rpc_exists', target.proc_oid is not null
    from target
  union all
  select 'anon_cannot_execute_worker_rpc', coalesce(
    not has_function_privilege('anon', target.proc_oid::oid, 'EXECUTE'), false
  ) from target
  union all
  select 'authenticated_can_execute_worker_rpc', coalesce(
    has_function_privilege('authenticated', target.proc_oid::oid, 'EXECUTE'), false
  ) from target
  union all
  select 'authenticated_has_no_direct_write_table_privileges', coalesce(not exists (
    select 1 from tables
     where rel_oid is null
        or has_table_privilege('authenticated', rel_oid::oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')
  ), false)
  union all
  select 'worker_write_tables_have_rls_and_owner_column', not exists (
    select 1
      from tables t
      left join pg_class rel on rel.oid = t.rel_oid::oid
      left join pg_attribute attr on attr.attrelid = rel.oid
                                   and attr.attname = 'owner_user_id'
                                   and not attr.attisdropped
     where t.rel_oid is null or not rel.relrowsecurity or attr.attnum is null
  )
  union all
  select 'worker_rpc_is_security_definer', exists (
    select 1 from pg_proc proc join target on true
     where proc.oid = target.proc_oid::oid and proc.prosecdef
  )
  union all
  select 'worker_rpc_has_locked_search_path', exists (
    select 1
      from pg_proc proc
      join target on true,
           unnest(coalesce(proc.proconfig, array[]::text[])) as setting(value)
     where proc.oid = target.proc_oid::oid
       and regexp_replace(setting.value, '[[:space:]]+', '', 'g') = 'search_path=pg_catalog,public'
  )
  union all
  select 'worker_rpc_returns_jsonb', exists (
    select 1 from pg_proc proc join target on true
     where proc.oid = target.proc_oid::oid and proc.prorettype = 'jsonb'::regtype
  )
  union all
  select 'worker_rpc_requires_analysis_schema_v3', coalesce(
    position('p_analysis->>''schemaVersion''isdistinctfrom''3''' in function_info.compact_definition) > 0,
    false
  ) from function_info
  union all
  select 'worker_rpc_requires_research_match_scale', coalesce(
    position('research_match_percent_0_to_100' in function_info.compact_definition) > 0
    and position('worker_confidence_contract_invalid' in function_info.compact_definition) > 0,
    false
  ) from function_info
  union all
  select 'worker_rpc_requires_json_number_integer_confidence', coalesce(
    position('jsonb_typeof(p_analysis->''briefCommand''->''confidence'')<>''number''' in function_info.compact_definition) > 0
    and position('jsonb_typeof(item->''confidence'')<>''number''' in function_info.compact_definition) > 0
    and position('trunc(confidence_value)<>confidence_value' in function_info.compact_definition) > 0,
    false
  ) from function_info
  union all
  select 'v3_current_documents_use_valid_integer_research_match', not exists (
    select 1
      from v3_documents d
     where d.payload->>'confidenceScale' is distinct from 'research_match_percent_0_to_100'
        or d.payload->>'confidenceMeaning' is distinct from '研究匹配度，不是涨跌概率、收益概率或自动下单依据'
        or jsonb_typeof(d.payload->'briefCommand') is distinct from 'object'
        or jsonb_typeof(d.payload->'advice') is distinct from 'array'
  ) and not exists (
    select 1 from v3_confidence_entries entry
     where jsonb_typeof(entry.value) <> 'number'
        or not ((entry.value #>> '{}')::numeric between 0 and 100)
        or trunc((entry.value #>> '{}')::numeric) <> (entry.value #>> '{}')::numeric
  )
  union all
  select 'legacy_current_documents_are_allowed_pending_refresh', true
  union all
  select 'worker_rpc_does_not_reference_platform_key', coalesce(
    position('OPENAI_API_KEY' in function_info.definition) = 0, false
  ) from function_info
)
select count(*) as total_checks,
       count(*) filter (where passed) as passed_checks,
       count(*) filter (where not passed) as failed_checks,
       coalesce(jsonb_agg(check_name order by check_name) filter (where not passed), '[]'::jsonb) as failed_check_names
  from checks;
