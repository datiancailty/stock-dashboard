-- MANUAL HOSTED POSTFLIGHT ONLY. Aggregate/read-only; no auth tokens, secrets,
-- owner IDs, stock identities, news text or raw document payloads are emitted.
-- This verifies schema/security plus aggregate storage. It does NOT prove a
-- provider search ran: after fresh authorization the worker must publish and
-- verify its authenticated Part5 readback; browser acceptance is another gate.
begin transaction read only;

with rpc as (
  select to_regprocedure('public.personal_sync_news(text,text,text,jsonb,jsonb,jsonb,text,jsonb)')::oid as oid
)
select rpc.oid is not null as news_rpc_installed,
       (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
         where n.nspname='public' and p.proname='personal_sync_news') as news_rpc_overload_count,
       coalesce(p.prosecdef, false) as security_definer,
       p.proconfig as function_settings,
       pg_get_function_identity_arguments(p.oid) as exact_arguments,
       coalesce(has_function_privilege('authenticated', p.oid, 'EXECUTE'), false) as authenticated_execute,
       coalesce(has_function_privilege('anon', p.oid, 'EXECUTE'), false) as anon_execute,
       coalesce(has_function_privilege('service_role', p.oid, 'EXECUTE'), false) as service_role_execute,
       exists(select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
               where a.grantee=0 and a.privilege_type='EXECUTE') as public_execute,
       md5(pg_get_functiondef(p.oid)) as function_definition_md5
  from rpc left join pg_proc p on p.oid=rpc.oid;
-- Expected: installed=true; overload_count=1; security_definer=true;
-- search_path=pg_catalog, public; authenticated=true; anon/service_role/PUBLIC=false.

select c.relname as table_name, c.relrowsecurity as rls_enabled,
       c.relforcerowsecurity as rls_forced,
       has_table_privilege('authenticated',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') as authenticated_any_table_privilege,
       has_table_privilege('anon',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') as anon_any_table_privilege,
       has_table_privilege('service_role',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') as service_any_table_privilege
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
 where n.nspname='public' and c.relname in
   ('personal_news_items','personal_documents','personal_watchlist_items','personal_part4_sync_writer_credentials')
 order by c.relname;
-- Expected: 4 rows, all rls_enabled=true, all three direct privilege flags=false.
-- rls_forced remains as before; this migration does not change any table ACL/RLS.

select count(*) as total_news_rows,
       count(*) filter(where payload->>'sourceClass'='news_search') as tagged_search_rows,
       count(*) filter(where payload->>'sourceClass'='news_search'
          and ((payload->'officialVerified') is distinct from 'false'::jsonb
               or payload->>'id' is distinct from source_id
               or payload->>'code' is distinct from stock_code)) as tagged_contract_violations,
       (select count(*) from (select owner_user_id,source_id from public.personal_news_items
          group by owner_user_id,source_id having count(*)>1) duplicates) as duplicate_owner_source_ids
  from public.personal_news_items;
-- Expected: contract violations=0, duplicates=0. Older untagged history survives.

select count(*) as news_metadata_documents,
       count(*) filter(where payload->>'coverageMeaning'='successful_search_batches_not_exhaustive_results') as new_worker_metadata_documents,
       count(*) filter(where payload->>'coverageMeaning'='successful_search_batches_not_exhaustive_results'
         and (jsonb_typeof(payload->'lastScanAt') is distinct from 'string'
           or jsonb_typeof(payload->'scanCompletedAt') is distinct from 'string'
           or jsonb_typeof(payload->'trackedStockCodes') is distinct from 'array'
           or payload->'coverageComplete' is distinct from 'true'::jsonb
           or payload->'officialVerified' is distinct from 'false'::jsonb
           or payload->'attemptedBatchCount' is distinct from payload->'successfulBatchCount'
           or payload->'scannedWatchlistCount' is distinct from payload->'expectedWatchlistCount')) as new_metadata_contract_violations
  from public.personal_documents where document_key='news_meta';
-- Expected: new_metadata_contract_violations=0. Before the first authorized publish
-- new_worker_metadata_documents can legitimately be zero; no fake freshness.
commit;
