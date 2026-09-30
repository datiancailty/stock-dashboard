-- Part4日期事件：用户手工执行的只读后检；一个聚合结果行。
-- 只读catalog，不调用RPC，不读取业务payload，不写入任何数据。
-- 成功门：failed_count=0 且 failed_checks=[]。不等于业务发布/浏览器验收。
with expected_functions(signature, source_md5, volatility, language_name) as (
  values
    ('public.personal_get_part4()', '6a68e6da427b3e9530d5fce01026b9e0', 's', 'sql'),
    ('public.personal_get_part4_v4()', 'd1b5d9e8497ed45ad1314097cf547404', 's', 'plpgsql'),
    ('public.personal_sync_part4_dividend_notices(text,text,text,integer,integer,integer,jsonb,text,text,jsonb)', 'a38d4203f46c1e3e15f9da345d3f6f50', 'v', 'plpgsql'),
    ('public.personal_sync_part4_dividend_dates(text,text,jsonb,jsonb,text)', '17d67d294e949e212b0a16ab5d5a8908', 'v', 'plpgsql')
), funcs as (
  select e.*, p.* from expected_functions e
    left join pg_proc p on p.oid = to_regprocedure(e.signature)::oid
), expected_tables(name, columns, primary_key, parent_fk) as (
  values
    ('personal_part4_dividend_date_events',
     '[["owner_user_id","uuid",true],["source_id","text",true],["stock_code","text",true],["event_date","date",true],["notice_id","text",true],["payload","jsonb",true],["observed_at","timestamp with time zone",true],["updated_at","timestamp with time zone",true]]'::jsonb,
     'PRIMARY KEY (owner_user_id, source_id)',
     'FOREIGN KEY (owner_user_id, notice_id) REFERENCES personal_part4_dividend_notices(owner_user_id, source_id)'),
    ('personal_part4_dividend_date_sync_state',
     '[["owner_user_id","uuid",true],["run_id","text",true],["observed_at","timestamp with time zone",true],["request_sha256","text",true],["event_count","integer",true],["updated_at","timestamp with time zone",true]]'::jsonb,
     'PRIMARY KEY (owner_user_id)',
     'FOREIGN KEY (owner_user_id, run_id) REFERENCES personal_part4_dividend_notice_sync_runs(owner_user_id, run_id)')
), relations as (
  select e.*, c.* from expected_tables e left join pg_class c on c.oid = to_regclass('public.' || e.name)::oid
), checks as (
  select f.signature || '_contract_source_acl' as name, coalesce(
    md5(f.prosrc) = f.source_md5 and pg_get_userbyid(f.proowner) = 'postgres'
    and f.prosecdef and not f.proretset and f.prorettype = 'jsonb'::regtype
    and f.provolatile::text = f.volatility
    and f.proconfig = array['search_path=pg_catalog, public']::text[]
    and (select lanname from pg_language where oid = f.prolang) = f.language_name
    and has_function_privilege('authenticated', f.oid, 'EXECUTE')
    and not has_function_privilege('anon', f.oid, 'EXECUTE')
    and not has_function_privilege('service_role', f.oid, 'EXECUTE')
    and not exists (select 1 from aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) a
      where a.grantee not in (f.proowner, 'authenticated'::regrole::oid)
         or (a.grantee <> f.proowner and a.is_grantable)), false) as pass
  from funcs f
  union all select r.name || '_rls_owner', coalesce(
    r.relkind = 'r' and r.relrowsecurity and pg_get_userbyid(r.relowner) = 'postgres', false) from relations r
  union all select r.name || '_columns', coalesce((
    select jsonb_agg(jsonb_build_array(a.attname, format_type(a.atttypid,a.atttypmod), a.attnotnull) order by a.attnum)
      from pg_attribute a where a.attrelid = r.oid and a.attnum > 0 and not a.attisdropped) = r.columns, false)
    from relations r
  union all select r.name || '_primary_key', exists (
    select 1 from pg_constraint k where k.conrelid = r.oid and k.contype = 'p' and k.convalidated
      and pg_get_constraintdef(k.oid) = r.primary_key) from relations r
  union all select r.name || '_owner_fk', exists (
    select 1 from pg_constraint k where k.conrelid = r.oid and k.contype = 'f' and k.convalidated
      and pg_get_constraintdef(k.oid) = 'FOREIGN KEY (owner_user_id) REFERENCES auth.users(id) ON DELETE CASCADE') from relations r
  union all select r.name || '_same_owner_parent_fk', exists (
    select 1 from pg_constraint k where k.conrelid = r.oid and k.contype = 'f' and k.convalidated
      and replace(pg_get_constraintdef(k.oid), 'public.', '') = r.parent_fk) from relations r
  union all select r.name || '_constraints_valid', r.oid is not null and (
    select count(*) = 6 and bool_and(k.convalidated)
      from pg_constraint k where k.conrelid = r.oid and k.contype in ('p','f','c')) from relations r
  union all select r.name || '_rpc_only', coalesce(
    not has_table_privilege('anon',r.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    and not has_table_privilege('authenticated',r.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    and not has_table_privilege('service_role',r.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    and not exists (select 1 from aclexplode(coalesce(r.relacl,acldefault('r',r.relowner))) a where a.grantee <> r.relowner)
    and not exists (select 1 from pg_attribute a, lateral aclexplode(a.attacl) x
                    where a.attrelid = r.oid and x.grantee <> r.relowner), false) from relations r
  union all select r.name || '_no_policies_or_hooks', r.oid is not null
    and not exists (select 1 from pg_policy p where p.polrelid = r.oid)
    and not exists (select 1 from pg_trigger t where t.tgrelid = r.oid and not t.tgisinternal)
    and not exists (select 1 from pg_rewrite w where w.ev_class = r.oid) from relations r
  union all select 'date_writer_single_signature', count(*) = 1
    from pg_proc p join pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname='personal_sync_part4_dividend_dates'
  union all select 'date_owner_index', exists (
    select 1 from pg_index i where i.indexrelid = to_regclass('public.personal_part4_dividend_date_events_owner_date_idx')::oid
      and i.indrelid = to_regclass('public.personal_part4_dividend_date_events')::oid
      and i.indisvalid and i.indisready)
)
select count(*) as check_count,
  count(*) filter (where pass is distinct from true) as failed_count,
  coalesce(jsonb_agg(name order by name) filter (where pass is distinct from true), '[]'::jsonb) as failed_checks
from checks;
