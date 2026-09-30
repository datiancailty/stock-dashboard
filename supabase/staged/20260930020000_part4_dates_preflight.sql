-- Part4日期事件升级：第1步，只读catalog预检。只由用户在SQL Editor手动执行。
-- 不调用任何业务RPC，不读取业务payload，不修改结构、权限或数据。
with funcs as (
 select signature, to_regprocedure(signature)::oid as oid
 from (values
  ('public.personal_get_part4()'),
  ('public.personal_get_part4_v4()'),
  ('public.personal_sync_part4_dividend_notices(text,text,text,integer,integer,integer,jsonb,text,text,jsonb)')
 ) f(signature)
), relations as (
 select name, to_regclass('public.'||name)::oid as oid
 from (values
  ('personal_part4_dividend_notices'),
  ('personal_part4_sync_writer_credentials'),
  ('personal_watchlist_items'),
  ('personal_documents')
 ) t(name)
), checks as (
 select 'active_helper_present' as name,
  to_regprocedure('public.personal_current_user_is_active()') is not null as pass
 union all select 'trusted_digest_present',to_regprocedure('extensions.digest(text,text)') is not null
 union all select r.name||'_rls',coalesce(c.relkind='r' and c.relrowsecurity,false)
  from relations r left join pg_class c on c.oid=r.oid
 union all select f.signature||'_contract',coalesce(
  p.prosecdef and p.prorettype='jsonb'::regtype
  and p.proconfig @> array['search_path=pg_catalog, public']
  and p.provolatile=case when f.signature like '%sync_%' then 'v'::"char" else 's'::"char" end
  and has_function_privilege('authenticated',p.oid,'EXECUTE')
  and not has_function_privilege('anon',p.oid,'EXECUTE'),false)
  from funcs f left join pg_proc p on p.oid=f.oid
 union all select r.name||'_rpc_only',coalesce(
  not has_table_privilege('anon',r.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
  and not has_table_privilege('authenticated',r.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
  and not has_table_privilege('service_role',r.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),false)
  from relations r where r.name in ('personal_part4_dividend_notices','personal_part4_sync_writer_credentials')
 union all select 'new_date_table_absent',to_regclass('public.personal_part4_dividend_date_events') is null
 union all select 'new_date_writer_absent',not exists(
  select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and p.proname='personal_sync_part4_dividend_dates')
), fingerprints as (
 select jsonb_object_agg(f.signature,jsonb_build_object(
  'source_md5',md5(p.prosrc),'owner',pg_get_userbyid(p.proowner),
  'security_definer',p.prosecdef,'volatility',p.provolatile,'config',p.proconfig
 )) as value from funcs f left join pg_proc p on p.oid=f.oid
), schema_shapes as (
 select jsonb_object_agg(r.name,jsonb_build_object(
  'columns',(select jsonb_agg(jsonb_build_array(a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull) order by a.attnum)
    from pg_attribute a where a.attrelid=r.oid and a.attnum>0 and not a.attisdropped),
  'constraints_md5',(select md5(string_agg(pg_get_constraintdef(k.oid),E'\n' order by pg_get_constraintdef(k.oid)))
    from pg_constraint k where k.conrelid=r.oid)
 )) as value from relations r
)
select count(*) as check_count,
 count(*) filter(where pass is distinct from true) as failed_count,
 coalesce(jsonb_agg(name order by name) filter(where pass is distinct from true),'[]'::jsonb) as failed_checks,
 (select value from fingerprints) as function_fingerprints,
 (select value from schema_shapes) as schema_shapes
from checks;
