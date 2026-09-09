-- USER-MANUAL POSTFLIGHT. Aggregate schema/ACL facts only; no business rows,
-- credentials, tokens or writer invocation. This does not prove a real refresh.
with objects as (
  select to_regclass('public.personal_confirmed_dividend_snapshots') dividends,
         to_regclass('public.personal_refresh_health') health,
         to_regprocedure('public.personal_sync_confirmed_dividends(text,jsonb,text)') dividend_writer,
         to_regprocedure('public.personal_sync_refresh_health(text,jsonb,text)') health_writer,
         to_regprocedure('public.personal_get_refresh_health()') health_getter,
         to_regprocedure('public.personal_get_part4_v4()') part4_getter
), tabs as (
  select dividends oid from objects union all select health from objects
), funcs as (
  select dividend_writer oid from objects union all select health_writer from objects
  union all select health_getter from objects union all select part4_getter from objects
), checks as (
  select 'two_new_tables_exist' name, bool_and(oid is not null) passed from tabs
  union all select 'four_new_rpcs_exist',bool_and(oid is not null) from funcs
  union all select 'tables_rls_deny_by_default',bool_and(coalesce((select c.relrowsecurity and c.relkind = 'r' from pg_class c where c.oid = t.oid),false)
    and not exists(select 1 from pg_policy where polrelid = t.oid)
    and not exists(select 1 from pg_trigger where tgrelid = t.oid and not tgisinternal)) from tabs t
  union all select 'authenticated_rpc_execute_only',bool_and(coalesce(has_function_privilege('authenticated',oid,'EXECUTE'),false)) from funcs
  union all select 'no_public_anon_service_rpc_execute',bool_and(coalesce(not has_function_privilege('anon',f.oid,'EXECUTE') and not has_function_privilege('service_role',f.oid,'EXECUTE'),false)
    and not exists(select 1 from pg_proc p cross join lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a where p.oid = f.oid and a.grantee = 0 and a.privilege_type = 'EXECUTE')) from funcs f
  union all select 'no_direct_table_or_column_access',bool_and(coalesce(not has_table_privilege(r.role_name,t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    and not has_any_column_privilege(r.role_name,t.oid,'SELECT,INSERT,UPDATE,REFERENCES'),false)
    and not exists(select 1 from pg_class c cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a where c.oid=t.oid and a.grantee=0))
    from tabs t cross join (values('anon'),('authenticated'),('service_role')) r(role_name)
  union all select 'security_definer_fixed_search_path',4=(select count(*) from pg_proc where oid in(select oid from funcs) and prosecdef and 'search_path=pg_catalog, public'=any(proconfig))
  union all select 'table_shapes_and_owner_keys',
    (select count(*) from pg_attribute where attrelid=o.dividends and attnum>0 and not attisdropped)=5
    and (select count(*) from pg_attribute where attrelid=o.health and attnum>0 and not attisdropped)=4
    and not exists(select 1 from (values
      ('d','owner_user_id','uuid',true),('d','stock_code','text',true),('d','payload','jsonb',true),('d','as_of','timestamp with time zone',true),('d','updated_at','timestamp with time zone',true),
      ('h','owner_user_id','uuid',true),('h','payload','jsonb',true),('h','last_success_at','timestamp with time zone',false),('h','updated_at','timestamp with time zone',true)) e(tab,col,typ,nn)
      where not exists(select 1 from pg_attribute a where a.attrelid=case when e.tab='d' then o.dividends else o.health end and a.attname=e.col and a.attnotnull=e.nn and format_type(a.atttypid,a.atttypmod)=e.typ))
    and exists(select 1 from pg_constraint where conrelid=o.dividends and contype='p' and pg_get_constraintdef(oid)='PRIMARY KEY (owner_user_id, stock_code)')
    and exists(select 1 from pg_constraint where conrelid=o.health and contype='p' and pg_get_constraintdef(oid)='PRIMARY KEY (owner_user_id)')
    and 2=(select count(*) from pg_constraint where conrelid in(o.dividends,o.health) and contype='f' and convalidated and pg_get_constraintdef(oid)='FOREIGN KEY (owner_user_id) REFERENCES auth.users(id) ON DELETE CASCADE') from objects o
  union all select 'existing_v3_and_writer_prerequisites_present',
    to_regprocedure('public.personal_get_part4_v3()') is not null
    and to_regprocedure('public.personal_get_part4_v2()') is not null
    and to_regprocedure('public.personal_get_part4()') is not null
    and to_regprocedure('public.personal_sync_technical_snapshot(text,jsonb,text)') is not null
    and to_regprocedure('public.personal_sync_forward_basis(text,jsonb,text)') is not null
    and to_regprocedure('public.personal_current_user_is_active()') is not null
    and to_regprocedure('extensions.digest(text,text)') is not null
    and to_regclass('public.personal_part4_sync_writer_credentials') is not null
)
select bool_and(coalesce(passed,false)) all_checks_passed,
       count(*) total_checks,count(*) filter(where passed) passed_checks,
       coalesce(jsonb_agg(name order by name) filter(where passed is distinct from true),'[]'::jsonb) failed_checks
from checks;
