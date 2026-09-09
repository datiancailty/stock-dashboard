-- Run manually after the technical snapshot installation. Aggregate only.
with objects as (
  select to_regclass('public.personal_technical_snapshots') as tab,
         to_regprocedure('public.personal_sync_technical_snapshot(text,jsonb,text)') as writer,
         to_regprocedure('public.personal_get_part4_v3()') as getter
), checks as (
 select 'new_table_exists' name, tab is not null passed from objects
 union all select 'two_functions_exist',writer is not null and getter is not null from objects
 union all select 'rls_enabled',coalesce((select relrowsecurity from pg_class where oid=tab),false) from objects
 union all select 'deny_by_default_no_policies',tab is not null and not exists(select 1 from pg_policy where polrelid=tab) from objects
 union all select 'authenticated_rpc_only',coalesce(has_function_privilege('authenticated',writer,'EXECUTE') and has_function_privilege('authenticated',getter,'EXECUTE'),false) from objects
 union all select 'anon_and_service_denied',coalesce(not has_function_privilege('anon',writer,'EXECUTE') and not has_function_privilege('anon',getter,'EXECUTE') and not has_function_privilege('service_role',writer,'EXECUTE') and not has_function_privilege('service_role',getter,'EXECUTE'),false) from objects
 union all select 'authenticated_no_direct_table',coalesce(not has_table_privilege('authenticated',tab,'SELECT,INSERT,UPDATE,DELETE'),false) from objects
 union all select 'security_definer_fixed_path',2=(select count(*) from pg_proc where oid in(writer,getter) and prosecdef and 'search_path=pg_catalog, public'=any(proconfig)) from objects
 union all select 'v2_preserved',to_regprocedure('public.personal_get_part4_v2()') is not null
)
select bool_and(passed) all_checks_passed,count(*) total_checks,count(*) filter(where passed) passed_checks,
       coalesce(jsonb_agg(name order by name) filter(where not passed),'[]'::jsonb) failed_checks
from checks;
