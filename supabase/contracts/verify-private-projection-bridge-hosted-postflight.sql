-- Private projection bridge Hosted postflight — READ ONLY.
--
-- Run this in Supabase SQL Editor after manually applying
-- 20260829005000_vps_sync_private_projection_bridge.sql. It returns one
-- aggregate row and does not return function bodies, private rows or secrets.

with checks as (
  select 'wrapper_exists'::text as check_name,
         to_regprocedure('public.vps_sync_ingest_report(text,jsonb)') is not null as passed
  union all
  select 'base_v2_function_preserved',
         to_regprocedure('public.vps_sync_ingest_report_v2_base(text,jsonb)') is not null
  union all
  select 'wrapper_is_security_definer_and_calls_private_writer',
         exists (
           select 1
             from pg_proc as p
             join pg_namespace as n on n.oid = p.pronamespace
            where n.nspname = 'public'
              and p.oid = to_regprocedure('public.vps_sync_ingest_report(text,jsonb)')
              and p.prosecdef
              and pg_get_functiondef(p.oid) like '%vps_sync_replace_private_projection%'
         )
  union all
  select 'private_writer_exists',
         to_regprocedure('public.vps_sync_replace_private_projection(text,text,jsonb)') is not null
  union all
  select 'private_writer_service_role_only',
         has_function_privilege('service_role', 'public.vps_sync_replace_private_projection(text,text,jsonb)', 'EXECUTE')
         and not has_function_privilege('anon', 'public.vps_sync_replace_private_projection(text,text,jsonb)', 'EXECUTE')
         and not has_function_privilege('authenticated', 'public.vps_sync_replace_private_projection(text,text,jsonb)', 'EXECUTE')
  union all
  select 'wrapper_direct_execute_revoked',
         not has_function_privilege('anon', 'public.vps_sync_ingest_report(text,jsonb)', 'EXECUTE')
         and not has_function_privilege('authenticated', 'public.vps_sync_ingest_report(text,jsonb)', 'EXECUTE')
         and not has_function_privilege('service_role', 'public.vps_sync_ingest_report(text,jsonb)', 'EXECUTE')
  union all
  select 'base_direct_execute_revoked',
         not has_function_privilege('anon', 'public.vps_sync_ingest_report_v2_base(text,jsonb)', 'EXECUTE')
         and not has_function_privilege('authenticated', 'public.vps_sync_ingest_report_v2_base(text,jsonb)', 'EXECUTE')
         and not has_function_privilege('service_role', 'public.vps_sync_ingest_report_v2_base(text,jsonb)', 'EXECUTE')
  union all
  select 'publish_gateway_service_role_execute',
         has_function_privilege('service_role', 'public.vps_sync_publish_request(text,text,text,jsonb)', 'EXECUTE')
         and not has_function_privilege('anon', 'public.vps_sync_publish_request(text,text,text,jsonb)', 'EXECUTE')
         and not has_function_privilege('authenticated', 'public.vps_sync_publish_request(text,text,text,jsonb)', 'EXECUTE')
  union all
  select 'no_private_table_direct_dml_for_browser',
         has_table_privilege('anon', 'public.vps_private_projection_state', 'SELECT') = false
         and has_table_privilege('authenticated', 'public.vps_private_projection_state', 'SELECT') = false
         and has_table_privilege('anon', 'public.vps_private_sim_positions', 'SELECT') = false
         and has_table_privilege('authenticated', 'public.vps_private_sim_positions', 'SELECT') = false
)
select count(*) as total_checks,
       count(*) filter (where passed) as passed_checks,
       count(*) filter (where not passed) as failed_checks,
       coalesce(
         jsonb_agg(
           jsonb_build_object('check', check_name, 'passed', passed)
           order by check_name
         ) filter (where not passed),
         '[]'::jsonb
       ) as failed_check_names
  from checks;
