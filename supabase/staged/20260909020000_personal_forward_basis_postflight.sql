-- READ ONLY, aggregate metadata only. Run after the user applies staged DDL.
-- Does not read private payloads/credentials or impersonate an owner.
select 'forward_basis_table' as check_name,
       to_regclass('public.personal_forward_basis_snapshots') is not null as ok
union all
select 'rls_enabled', coalesce((select relrowsecurity from pg_class
  where oid = to_regclass('public.personal_forward_basis_snapshots')), false)
union all
select 'no_direct_table_access', not exists (
  select 1 from unnest(array['anon','authenticated','service_role']) r(role_name)
  cross join unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) p(privilege)
  where has_table_privilege(r.role_name,'public.personal_forward_basis_snapshots',p.privilege))
union all
select 'getter_and_writer_exist', to_regprocedure('public.personal_get_part4_v2()') is not null
  and to_regprocedure('public.personal_sync_forward_basis(text,jsonb,text)') is not null
union all
select 'authenticated_only_rpc_grants', bool_and(
  has_function_privilege('authenticated', p.oid,'EXECUTE')
  and not has_function_privilege('anon',p.oid,'EXECUTE')
  and not has_function_privilege('service_role',p.oid,'EXECUTE'))
  from pg_proc p where p.oid in (to_regprocedure('public.personal_get_part4_v2()'),
    to_regprocedure('public.personal_sync_forward_basis(text,jsonb,text)'))
union all
select 'security_definer_fixed_path', bool_and(p.prosecdef
  and 'search_path=pg_catalog, public' = any(p.proconfig))
  from pg_proc p where p.oid in (to_regprocedure('public.personal_get_part4_v2()'),
    to_regprocedure('public.personal_sync_forward_basis(text,jsonb,text)'))
union all
select 'legacy_functions_retained', to_regprocedure('public.personal_get_part4()') is not null
  and to_regprocedure('public.personal_sync_future_dividend_grid(text,jsonb,text)') is not null;

-- Compare these non-secret definitions with the preflight CSV if desired.
select p.proname as function_name, md5(pg_get_functiondef(p.oid)) as definition_md5
from pg_proc p where p.oid in (to_regprocedure('public.personal_get_part4()'),
  to_regprocedure('public.personal_sync_future_dividend_grid(text,jsonb,text)'))
order by p.proname;

select count(*) as stored_count,
       count(*) filter (where payload->>'status' = 'ready') as ready_count,
       count(*) filter (where payload->>'status' <> 'ready') as unavailable_count,
       min(as_of) as oldest_as_of, max(as_of) as newest_as_of
from public.personal_forward_basis_snapshots;
