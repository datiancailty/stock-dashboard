begin;

-- Forward repair for the already-applied protocol-v2 function.
-- The RETURNS TABLE output column revision_no conflicted with the table column
-- in max(revision_no).  Qualify the table reference; preserve the v2 contract,
-- immutable revision semantics, and existing function ACLs.
create or replace function public.vps_submit_whitelist_revision(
  p_symbols text[],
  p_request_note text default null
)
returns table (
  revision_id uuid,
  revision_no bigint,
  status public.vps_whitelist_status
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
  if p_request_note is not null and (char_length(p_request_note) > 500 or not public.vps_text_is_safe(p_request_note)) then
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

  perform pg_advisory_xact_lock(hashtext('public.vps_whitelist_revisions'));
  select coalesce(max(existing_revision.revision_no), 0) + 1 into next_revision_no from public.vps_whitelist_revisions as existing_revision;
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
  payload_sha := encode(digest(payload_text, 'sha256'), 'hex');
  raw_contract := public.vps_revision_raw_contract_text(payload_text, payload_sha);
  raw_contract_sha := encode(digest(raw_contract, 'sha256'), 'hex');

  insert into public.vps_whitelist_revisions (
    revision_no, status, requested_by, request_note, desired_symbol_count,
    contract_protocol_version, target_device_id, contract_adapter_id, contract_mode,
    source_policy_sha256, members_sha256, required_snapshot_id, required_snapshot_sha256,
    contract_created_at, contract_expires_at, control_payload_sha256,
    control_raw_contract, control_raw_contract_sha256
  ) values (
    next_revision_no, 'submitted', auth.uid(), nullif(btrim(coalesce(p_request_note, '')), ''), cardinality(normalized_symbols),
    2, settings.target_device_id, settings.expected_adapter_id, 'DRY_RUN',
    settings.expected_source_policy_sha256, members_sha, settings.required_snapshot_id, settings.required_snapshot_sha256,
    contract_created, contract_expires, payload_sha, raw_contract, raw_contract_sha
  ) returning id into created_revision_id;

  insert into public.vps_whitelist_revision_items(revision_id, symbol, sort_order)
  select created_revision_id, item.symbol, item.ordinality - 1
    from unnest(normalized_symbols) with ordinality as item(symbol, ordinality);

  update public.vps_whitelist_revisions
     set status = 'superseded', superseded_by = created_revision_id
   where id <> created_revision_id
     and status in ('submitted', 'sync_pending', 'preparing');

  return query select created_revision_id, next_revision_no, 'submitted'::public.vps_whitelist_status;
end;
$$;

commit;
