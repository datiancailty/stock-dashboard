-- Forward-only repair of the expected-base (three-argument) whitelist RPC.
-- Only qualify table-column references that collide with RETURNS TABLE names.
-- No schema/data/ACL/policy/timer/ARM changes. No RPC is invoked by this file.
-- Run manually in Supabase SQL Editor, after the readonly fingerprint preflight.
begin;

do $guard$
declare
  target oid := pg_catalog.to_regprocedure('public.vps_submit_whitelist_revision(text[],text,bigint)');
  body_hash text;
  is_definer boolean;
  settings text[];
  argument_names text[];
begin
  if target is null then raise exception 'Expected three-argument RPC is absent'; end if;
  select pg_catalog.md5(p.prosrc), p.prosecdef, p.proconfig, p.proargnames
    into body_hash, is_definer, settings, argument_names
    from pg_catalog.pg_proc as p where p.oid=target;
  if body_hash not in ('17ff3d784f7695565f678fb22c7945fc', '5a5e440ab70ab2a46e9d1aa0cff0f457') then
    raise exception 'RPC source changed; abort and repeat readonly preflight';
  end if;
  if not is_definer or settings is distinct from array['search_path=pg_catalog, public']::text[]
    or argument_names is distinct from array['p_symbols','p_request_note','p_expected_base_revision_no','revision_id','revision_no','status','base_revision_no']::text[] then
    raise exception 'RPC security or argument contract changed';
  end if;
  if not pg_catalog.has_function_privilege('authenticated',target,'EXECUTE')
    or pg_catalog.has_function_privilege('anon',target,'EXECUTE')
    or pg_catalog.has_function_privilege('service_role',target,'EXECUTE') then
    raise exception 'RPC privilege preflight changed';
  end if;
end;
$guard$;

create or replace function public.vps_submit_whitelist_revision(
  p_symbols text[],
  p_request_note text,
  p_expected_base_revision_no bigint
)
returns table (
  revision_id uuid,
  revision_no bigint,
  status public.vps_whitelist_status,
  base_revision_no bigint
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  raw_symbol text;
  canonical_symbol text;
  normalized_symbols text[] := array[]::text[];
  settings public.vps_control_settings%rowtype;
  current_desired public.vps_whitelist_revisions%rowtype;
  current_active public.vps_whitelist_revisions%rowtype;
  current_base_revision_no bigint;
  next_revision_no bigint;
  created_revision_id uuid;
  contract_created timestamptz;
  contract_expires timestamptz;
  created_text text;
  expires_text text;
  members_sha text;
  payload_text text;
  payload_sha text;
  raw_contract text;
  raw_contract_sha text;
begin
  if not public.vps_is_admin() then
    raise exception 'VPS dashboard administrator access is required' using errcode = '42501';
  end if;
  if p_request_note is not null
    and (char_length(p_request_note) > 500 or not public.vps_text_is_safe(p_request_note)) then
    raise exception 'Whitelist request note is invalid';
  end if;
  if p_symbols is null or cardinality(p_symbols) not between 1 and 50 then
    raise exception 'Whitelist must contain 1 to 50 canonical A-share symbols';
  end if;

  foreach raw_symbol in array p_symbols loop
    canonical_symbol := upper(btrim(raw_symbol));
    if canonical_symbol is null or canonical_symbol !~ '^[0-9]{6}\.(SH|SZ)$' then
      raise exception 'Unsupported canonical A-share symbol';
    end if;
    if canonical_symbol = any(normalized_symbols) then
      raise exception 'Duplicate canonical A-share symbol';
    end if;
    normalized_symbols := array_append(normalized_symbols, canonical_symbol);
  end loop;

  select * into settings
    from public.vps_control_settings
   where id = true
   for update;
  if not found
    or settings.gateway_protocol_version <> 2
    or settings.target_device_id !~ '^[a-z0-9][a-z0-9._-]{2,79}$'
    or settings.expected_adapter_id <> 'v31f-15m-miaoxiang-sim-adapter'
    or settings.expected_source_policy_sha256 !~ '^[0-9a-f]{64}$'
    or settings.required_snapshot_id is null
    or settings.required_snapshot_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
    or settings.required_snapshot_sha256 is null
    or settings.required_snapshot_sha256 !~ '^[0-9a-f]{64}$'
    or settings.revision_ttl_seconds not between 300 and 2592000 then
    raise exception 'VPS control contract is not ready for a formal whitelist revision';
  end if;

  perform pg_advisory_xact_lock(hashtext('public.vps_whitelist_revisions:' || settings.target_device_id));
  select * into current_desired
    from public.vps_whitelist_revisions as desired_revision
   where desired_revision.target_device_id = settings.target_device_id
     and desired_revision.status in ('submitted', 'sync_pending', 'preparing')
   order by desired_revision.revision_no desc
   limit 1;
  if found then
    current_base_revision_no := current_desired.revision_no;
  else
    select * into current_active
      from public.vps_whitelist_revisions as active_revision
     where active_revision.target_device_id = settings.target_device_id
       and active_revision.status = 'active'
     order by active_revision.revision_no desc
     limit 1;
    if found then
      current_base_revision_no := current_active.revision_no;
    else
      current_base_revision_no := null;
    end if;
  end if;
  if p_expected_base_revision_no is distinct from current_base_revision_no then
    raise exception 'Whitelist base revision changed; reload the control state before submitting';
  end if;

  select coalesce(max(existing_revision.revision_no), 0) + 1
    into next_revision_no
    from public.vps_whitelist_revisions as existing_revision;
  contract_created := date_trunc('second', clock_timestamp());
  contract_expires := contract_created + make_interval(secs => settings.revision_ttl_seconds);
  created_text := public.vps_contract_time_text(contract_created);
  expires_text := public.vps_contract_time_text(contract_expires);
  members_sha := public.vps_members_sha256(normalized_symbols);
  payload_text := public.vps_revision_payload_text(
    next_revision_no, settings.target_device_id, settings.expected_adapter_id, 'DRY_RUN',
    created_text, expires_text, settings.expected_source_policy_sha256, members_sha,
    settings.required_snapshot_id, settings.required_snapshot_sha256, normalized_symbols
  );
  payload_sha := encode(extensions.digest(payload_text, 'sha256'), 'hex');
  raw_contract := public.vps_revision_raw_contract_text(payload_text, payload_sha);
  raw_contract_sha := encode(extensions.digest(raw_contract, 'sha256'), 'hex');

  insert into public.vps_whitelist_revisions (
    revision_no, status, requested_by, request_note, desired_symbol_count,
    contract_protocol_version, target_device_id, contract_adapter_id, contract_mode,
    source_policy_sha256, members_sha256, required_snapshot_id, required_snapshot_sha256,
    contract_created_at, contract_expires_at, control_payload_sha256,
    control_raw_contract, control_raw_contract_sha256
  ) values (
    next_revision_no, 'submitted', auth.uid(), nullif(btrim(coalesce(p_request_note, '')), ''),
    cardinality(normalized_symbols), 2, settings.target_device_id,
    settings.expected_adapter_id, 'DRY_RUN', settings.expected_source_policy_sha256,
    members_sha, settings.required_snapshot_id, settings.required_snapshot_sha256,
    contract_created, contract_expires, payload_sha, raw_contract, raw_contract_sha
  ) returning id into created_revision_id;

  insert into public.vps_whitelist_revision_items(revision_id, symbol, sort_order)
  select created_revision_id, item.symbol, item.ordinality - 1
    from unnest(normalized_symbols) with ordinality as item(symbol, ordinality);

  update public.vps_whitelist_revisions as superseded_revision
     set status = 'superseded', superseded_by = created_revision_id
   where superseded_revision.id <> created_revision_id
     and superseded_revision.target_device_id = settings.target_device_id
     and superseded_revision.status in ('submitted', 'sync_pending', 'preparing');

  return query
  select created_revision_id, next_revision_no,
         'submitted'::public.vps_whitelist_status,
         current_base_revision_no;
end;
$$;

do $verify$
begin
  if (select pg_catalog.md5(p.prosrc) from pg_catalog.pg_proc as p
      where p.oid=pg_catalog.to_regprocedure('public.vps_submit_whitelist_revision(text[],text,bigint)'))
      is distinct from '5a5e440ab70ab2a46e9d1aa0cff0f457' then
    raise exception 'Repaired function body verification failed';
  end if;
end;
$verify$;

commit;

select 'sync28_submit_rpc_repair_v1' as check_name,
  pg_catalog.md5(p.prosrc) = '5a5e440ab70ab2a46e9d1aa0cff0f457' as repaired_body_matches,
  p.prosecdef as security_definer,
  p.proconfig as function_settings,
  pg_catalog.has_function_privilege('authenticated',p.oid,'EXECUTE') as authenticated_execute,
  pg_catalog.has_function_privilege('anon',p.oid,'EXECUTE') as anon_execute,
  pg_catalog.has_function_privilege('service_role',p.oid,'EXECUTE') as service_role_execute
from pg_catalog.pg_proc as p
where p.oid=pg_catalog.to_regprocedure('public.vps_submit_whitelist_revision(text[],text,bigint)');
