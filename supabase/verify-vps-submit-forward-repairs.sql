-- Read-only verification for the vps_submit_whitelist_revision forward repairs.
-- This does not write data, activate the VPS, or call any provider/account path.
select
  proc.oid::regprocedure as function_signature,
  proc.proconfig as function_settings,
  position('max(existing_revision.revision_no)' in pg_get_functiondef(proc.oid)) > 0
    as revision_no_is_qualified,
  position('superseded_revision.status in (' in pg_get_functiondef(proc.oid)) > 0
    as status_is_qualified,
  proc.proconfig @> array['search_path=pg_catalog, extensions, public']
    as digest_search_path_is_preserved
from pg_proc as proc
join pg_namespace as namespace on namespace.oid = proc.pronamespace
where namespace.nspname = 'public'
  and proc.oid = 'public.vps_submit_whitelist_revision(text[],text)'::regprocedure;
