-- 第三步：手动迁移之后的只读结构/权限验收。仅输出聚合结果。
with checks(name,ok) as (
 values
 ('table_exists',to_regclass('public.personal_part0_monitor') is not null),
 ('row_security',coalesce((select relrowsecurity from pg_class where oid=to_regclass('public.personal_part0_monitor')),false)),
 ('no_table_policies',not exists(select 1 from pg_policy where polrelid=to_regclass('public.personal_part0_monitor'))),
 ('authenticated_no_table_access',not has_table_privilege('authenticated',to_regclass('public.personal_part0_monitor'),'SELECT,INSERT,UPDATE,DELETE')),
 ('anonymous_no_table_access',not has_table_privilege('anon',to_regclass('public.personal_part0_monitor'),'SELECT,INSERT,UPDATE,DELETE')),
 ('service_role_no_table_access',not has_table_privilege('service_role',to_regclass('public.personal_part0_monitor'),'SELECT,INSERT,UPDATE,DELETE')),
 ('getter_security_definer',coalesce((select prosecdef and proconfig @> array['search_path=pg_catalog, public'] from pg_proc where oid=to_regprocedure('public.personal_get_part0_monitor()')),false)),
 ('writer_security_definer',coalesce((select prosecdef and proconfig @> array['search_path=pg_catalog, public'] from pg_proc where oid=to_regprocedure('public.personal_sync_part0_monitor(jsonb,text)')),false)),
 ('authenticated_getter',has_function_privilege('authenticated',to_regprocedure('public.personal_get_part0_monitor()'),'EXECUTE')),
 ('authenticated_capability_writer',has_function_privilege('authenticated',to_regprocedure('public.personal_sync_part0_monitor(jsonb,text)'),'EXECUTE')),
 ('anonymous_getter_denied',not has_function_privilege('anon',to_regprocedure('public.personal_get_part0_monitor()'),'EXECUTE')),
 ('anonymous_writer_denied',not has_function_privilege('anon',to_regprocedure('public.personal_sync_part0_monitor(jsonb,text)'),'EXECUTE')),
 ('helpers_not_browser_callable',not has_function_privilege('authenticated',to_regprocedure('public.personal_part0_keys(jsonb,text[])'),'EXECUTE') and not has_function_privilege('authenticated',to_regprocedure('public.personal_part0_number(jsonb,boolean)'),'EXECUTE') and not has_function_privilege('authenticated',to_regprocedure('public.personal_part0_time(jsonb,boolean)'),'EXECUTE'))
)
select count(*) as check_count,count(*) filter(where not coalesce(ok,false)) as failed_count,
coalesce(jsonb_agg(name) filter(where not coalesce(ok,false)),'[]'::jsonb) as failed_checks from checks;
