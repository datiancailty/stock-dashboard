-- USER MANUAL SQL EDITOR ONLY. Forward-only function-definition repair.
-- The API-side WHERE safety gate rejected the legacy symbol-snapshot DELETE.
-- Preserve intentional full snapshot replacement, including an empty snapshot:
-- select the existing snapshot's primary keys, then delete only those keys.
-- This is NOT incremental pruning and does not preserve old snapshot rows.
-- No safety setting is disabled, no privilege is granted, and executing this
-- migration does not ingest a report, publish an ACK, or modify business rows.
begin;
set local lock_timeout = '3s';
set local statement_timeout = '20s';

do $repair$
declare
  legacy_oid oid := to_regprocedure('public.vps_sync_ingest_report_legacy(text,jsonb)');
  expected record;
  actual_oid oid;
  body_md5 text;
  definition text;
  prior_metadata jsonb;
  next_metadata jsonb;
  table_name text;
  relation_oid oid;
  symbol_attnum smallint;
  old_statement text;
  new_statement text;
  role_name text;
begin
  -- Reject partial installs, unexpected owners/signatures, and source drift.
  for expected in select * from (values
    ('public.vps_sync_publish_request(text,text,text,jsonb)', array['bb89f86ac5975945171582adf749632c']::text[]),
    ('public.vps_sync_ingest_report(text,jsonb)', array['748d66c6628a525f1dffaee97111f18e']::text[]),
    ('public.vps_sync_ingest_report_v2_base(text,jsonb)', array['99cee6ae98df57f2b7dbd8275c6fd02d']::text[]),
    ('public.vps_sync_ingest_report_v1(text,jsonb)', array['f2b5a08adf7c2253431cb4ba22eb2cda']::text[]),
    ('public.vps_sync_ingest_report_legacy(text,jsonb)', array['58134fff85fda576d209c0ca400aada1','10d789647e579ffdf03c94e0bbfb40e1']::text[])
  ) as expected_functions(signature, hashes) loop
    actual_oid := to_regprocedure(expected.signature);
    if actual_oid is null or not exists (
      select 1 from pg_proc as p
      where p.oid=actual_oid and md5(p.prosrc)=any(expected.hashes)
        and p.prosecdef and not p.proretset and p.prorettype='jsonb'::regtype
        and pg_get_userbyid(p.proowner)='postgres'
    ) then
      raise exception 'Snapshot repair source changed or signature/owner differs: %', expected.signature;
    end if;
  end loop;

  foreach table_name in array array['vps_symbol_states','vps_sim_positions'] loop
    relation_oid := to_regclass(format('public.%I', table_name));
    symbol_attnum := null;
    select a.attnum into symbol_attnum from pg_attribute as a
    where a.attrelid=relation_oid and a.attname='symbol' and a.attnotnull
      and a.atttypid='text'::regtype and not a.attisdropped;
    if relation_oid is null or symbol_attnum is null or not exists (
      select 1 from pg_constraint as c where c.conrelid=relation_oid
        and c.contype='p' and c.conkey=array[symbol_attnum]::smallint[]
    ) then
      raise exception 'Snapshot primary-key contract differs: %', table_name;
    end if;
  end loop;

  foreach role_name in array array['anon','authenticated','service_role'] loop
    if has_function_privilege(role_name,legacy_oid,'EXECUTE') then
      raise exception 'Legacy direct execution is not closed: %', role_name;
    end if;
  end loop;
  if has_function_privilege('anon','public.vps_sync_publish_request(text,text,text,jsonb)','EXECUTE')
    or has_function_privilege('authenticated','public.vps_sync_publish_request(text,text,text,jsonb)','EXECUTE')
    or not has_function_privilege('service_role','public.vps_sync_publish_request(text,text,text,jsonb)','EXECUTE') then
    raise exception 'Publish entry ACL differs';
  end if;

  select md5(p.prosrc), pg_get_functiondef(p.oid), to_jsonb(p)-'prosrc'
    into body_md5, definition, prior_metadata from pg_proc as p where p.oid=legacy_oid;
  if body_md5='58134fff85fda576d209c0ca400aada1' then
    foreach table_name in array array['vps_symbol_states','vps_sim_positions'] loop
      old_statement := format('delete from public.%I;',table_name);
      new_statement := format('delete from public.%I as snapshot%s   where snapshot.symbol in (select existing.symbol from public.%I as existing);',table_name,chr(10),table_name);
      if (length(definition)-length(replace(definition,old_statement,'')))<>length(old_statement) then
        raise exception 'Expected exactly one snapshot DELETE: %', table_name;
      end if;
      definition := replace(definition,old_statement,new_statement);
    end loop;
    execute definition;
  end if;

  select to_jsonb(p)-'prosrc' into next_metadata from pg_proc as p where p.oid=legacy_oid;
  if prior_metadata is distinct from next_metadata then
    raise exception 'Snapshot repair changed function metadata';
  end if;
  if (select md5(p.prosrc) from pg_proc as p where p.oid=legacy_oid)
      is distinct from '10d789647e579ffdf03c94e0bbfb40e1' then
    raise exception 'Snapshot repair body fingerprint differs';
  end if;
end;
$repair$;

select jsonb_build_object(
  'snapshot_delete_body_matches', (select md5(p.prosrc)='10d789647e579ffdf03c94e0bbfb40e1' from pg_proc as p where p.oid='public.vps_sync_ingest_report_legacy(text,jsonb)'::regprocedure),
  'legacy_security_definer', (select p.prosecdef from pg_proc as p where p.oid='public.vps_sync_ingest_report_legacy(text,jsonb)'::regprocedure),
  'legacy_function_settings', (select to_jsonb(p.proconfig) from pg_proc as p where p.oid='public.vps_sync_ingest_report_legacy(text,jsonb)'::regprocedure),
  'legacy_anon_execute', has_function_privilege('anon','public.vps_sync_ingest_report_legacy(text,jsonb)','EXECUTE'),
  'legacy_authenticated_execute', has_function_privilege('authenticated','public.vps_sync_ingest_report_legacy(text,jsonb)','EXECUTE'),
  'legacy_service_role_execute', has_function_privilege('service_role','public.vps_sync_ingest_report_legacy(text,jsonb)','EXECUTE'),
  'publish_service_role_execute', has_function_privilege('service_role','public.vps_sync_publish_request(text,text,text,jsonb)','EXECUTE')
) as snapshot_delete_repair_postflight;
commit;
