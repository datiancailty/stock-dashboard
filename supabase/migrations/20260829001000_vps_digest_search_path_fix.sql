begin;

-- Forward repair for Supabase's pgcrypto placement.
--
-- The hosted database has pgcrypto available, but its digest(text, text)
-- function is installed outside the locked-down `pg_catalog, public` search
-- path used by the SECURITY DEFINER functions below. Resolve the actual schema
-- from pg_proc, then place that trusted schema before public for only the
-- functions that execute digest(). No table data, revision state, grants, or
-- historical migration is changed.
do $$
declare
  digest_schema text;
begin
  select namespace.nspname
    into digest_schema
    from pg_proc as proc
    join pg_namespace as namespace on namespace.oid = proc.pronamespace
   where proc.proname = 'digest'
     and proc.prokind = 'f'
     and proc.pronargs = 2
     and proc.proargtypes[0] = 'text'::regtype::oid
     and proc.proargtypes[1] = 'text'::regtype::oid
   order by case when namespace.nspname = 'extensions' then 0 else 1 end,
            namespace.nspname
   limit 1;

  if digest_schema is null then
    raise exception 'pgcrypto digest(text, text) is not available in the hosted database';
  end if;

  execute format(
    'alter function public.vps_members_sha256(text[]) set search_path = pg_catalog, %I, public',
    digest_schema
  );
  execute format(
    'alter function public.vps_assert_v2_revision_item_consistency() set search_path = pg_catalog, %I, public',
    digest_schema
  );
  execute format(
    'alter function public.vps_submit_whitelist_revision(text[], text) set search_path = pg_catalog, %I, public',
    digest_schema
  );
  execute format(
    'alter function public.vps_sync_ingest_report_v1(text, jsonb) set search_path = pg_catalog, %I, public',
    digest_schema
  );
  execute format(
    'alter function public.vps_sync_ingest_report(text, jsonb) set search_path = pg_catalog, %I, public',
    digest_schema
  );
end;
$$;

commit;
