-- Forward-only ACK-ingest repair. USER MANUAL SQL EDITOR ONLY.
-- Changes no business rows, control targets, account projections, modes or timers.
-- Exact function fingerprints were manually read before this repair.
-- Fix an ON CONFLICT variable collision and two partial-index conflict targets,
-- restore legacy digest resolution, and
-- close direct PUBLIC/anon/authenticated/service_role access to the legacy core.
begin;
do $$
declare
  e record;
  actual_md5 text;
  secured boolean;
  pk_columns text[];
  is_deferred boolean;
  crypto_schema text;
begin
  for e in select * from (values
      ('public.vps_sync_ingest_report(text,jsonb)',array['748d66c6628a525f1dffaee97111f18e']::text[]),
      ('public.vps_sync_ingest_report_legacy(text,jsonb)',array['e396ffb9b9ccd2b3c58871272ff8b44b','58134fff85fda576d209c0ca400aada1']::text[]),
      ('public.vps_sync_publish_request(text,text,text,jsonb)',array['bb89f86ac5975945171582adf749632c']::text[]),
      ('public.vps_sync_ingest_report_v1(text,jsonb)',array['f2b5a08adf7c2253431cb4ba22eb2cda']::text[]),
      ('public.vps_sync_ingest_report_v2_base(text,jsonb)',array['ae7bd4e683766bc1c49b74fc1ec05d53','99cee6ae98df57f2b7dbd8275c6fd02d']::text[])
    ) as expected(signature,allowed_md5)
  loop
    select md5(p.prosrc),p.prosecdef into actual_md5,secured
      from pg_proc p where p.oid=to_regprocedure(e.signature);
    if not found or not secured or not (actual_md5=any(e.allowed_md5)) then
      raise exception 'ACK ingest source changed; stop for manual review';
    end if;
  end loop;
  select array_agg(a.attname::text order by k.n),bool_or(c.condeferrable)
    into pk_columns,is_deferred
    from pg_constraint c
    cross join lateral unnest(c.conkey) with ordinality as k(attnum,n)
    join pg_attribute a on a.attrelid=c.conrelid and a.attnum=k.attnum
   where c.conrelid='public.vps_sync_ack_receipts'::regclass
     and c.conname='vps_sync_ack_receipts_pkey' and c.contype='p';
  if pk_columns is distinct from array['device_id','ack_id']::text[] or is_deferred is distinct from false then
    raise exception 'ACK receipt primary key changed; stop for manual review';
  end if;
  for e in select * from (values
      ('public.vps_sync_acks','public.vps_sync_acks_source_ack_key_unique','source_ack_key'),
      ('public.vps_runtime_events','public.vps_runtime_events_source_event_key_unique','source_event_key')
    ) as expected(table_name,index_name,column_name)
  loop
    if not exists (
      select 1 from pg_index i where i.indexrelid=to_regclass(e.index_name)
        and i.indrelid=to_regclass(e.table_name) and i.indisunique and i.indisvalid and i.indisready
        and i.indnkeyatts=1 and pg_get_indexdef(i.indexrelid,1,true)=e.column_name
        and replace(replace(lower(pg_get_expr(i.indpred,i.indrelid)),'(',''),')','')=e.column_name || ' is not null'
    ) then
      raise exception 'ACK/event partial index changed; stop for manual review';
    end if;
  end loop;
  select n.nspname into crypto_schema from pg_extension x join pg_namespace n on n.oid=x.extnamespace where x.extname='pgcrypto';
  if crypto_schema is null or crypto_schema not in ('extensions','public')
    or to_regprocedure(format('%I.digest(text,text)',crypto_schema)) is null then
    raise exception 'ACK digest namespace changed; stop for manual review';
  end if;
  if has_function_privilege('anon','public.vps_sync_publish_request(text,text,text,jsonb)','EXECUTE')
    or has_function_privilege('authenticated','public.vps_sync_publish_request(text,text,text,jsonb)','EXECUTE')
    or not has_function_privilege('service_role','public.vps_sync_publish_request(text,text,text,jsonb)','EXECUTE') then
    raise exception 'ACK publish entry ACL changed; stop for manual review';
  end if;
end;
$$;

create or replace function public.vps_sync_ingest_report_v2_base(
  p_device_id text,
  p_report jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  item jsonb;
  ack_id text;
  ack_body_sha text;
  stored_ack_body_sha text;
  ack_receipt_rows integer;
  ack_revision_no bigint;
  ack_stage text;
  ack_generation bigint;
  ack_pack_sha text;
  revision_row public.vps_whitelist_revisions%rowtype;
  legacy_report jsonb;
  legacy_acks jsonb := '[]'::jsonb;
  accepted_ack_ids jsonb := '[]'::jsonb;
  seen_ack_ids text[] := array[]::text[];
  v1_result jsonb;
  active_revision_no bigint;
begin
  if not exists (select 1 from public.vps_sync_devices where device_id = p_device_id and enabled = true) then
    raise exception 'VPS gateway device is disabled';
  end if;
  if jsonb_typeof(p_report) <> 'object'
    or p_report ->> 'schema_version' <> '2'
    or p_report ->> 'adapter_id' <> 'v31f-15m-miaoxiang-sim-adapter'
    or p_report ->> 'mode' <> 'DRY_RUN'
    or jsonb_typeof(coalesce(p_report -> 'acks', '[]'::jsonb)) <> 'array'
    or jsonb_array_length(coalesce(p_report -> 'acks', '[]'::jsonb)) > 8 then
    raise exception 'VPS runtime v2 report is invalid';
  end if;

  -- Verify the entire active runtime linkage before any legacy state mutation.
  active_revision_no := nullif(p_report ->> 'active_revision_no', '')::bigint;
  if active_revision_no is null then
    if nullif(p_report ->> 'active_generation', '') is not null
      or nullif(p_report ->> 'active_pack_sha256', '') is not null
      or nullif(p_report ->> 'active_control_payload_sha256', '') is not null
      or nullif(p_report ->> 'active_control_raw_contract_sha256', '') is not null
      or nullif(p_report ->> 'active_members_sha256', '') is not null
      or nullif(p_report ->> 'active_snapshot_id', '') is not null
      or nullif(p_report ->> 'active_snapshot_sha256', '') is not null then
      raise exception 'VPS report has orphan active revision evidence';
    end if;
  else
    select * into revision_row from public.vps_whitelist_revisions where revision_no = active_revision_no for update;
    if not found
      or revision_row.status <> 'active'
      or revision_row.active_generation is distinct from nullif(p_report ->> 'active_generation', '')::bigint
      or revision_row.active_pack_sha256 is distinct from nullif(p_report ->> 'active_pack_sha256', '')
      or revision_row.control_payload_sha256 is distinct from nullif(p_report ->> 'active_control_payload_sha256', '')
      or revision_row.control_raw_contract_sha256 is distinct from nullif(p_report ->> 'active_control_raw_contract_sha256', '')
      or revision_row.members_sha256 is distinct from nullif(p_report ->> 'active_members_sha256', '')
      or revision_row.required_snapshot_id is distinct from nullif(p_report ->> 'active_snapshot_id', '')
      or revision_row.required_snapshot_sha256 is distinct from nullif(p_report ->> 'active_snapshot_sha256', '') then
      -- An activation ACK in the same report is handled below; do not reject it
      -- yet merely because the row has not transitioned to active.
      if not exists (
        select 1 from jsonb_array_elements(coalesce(p_report -> 'acks', '[]'::jsonb)) as ack(value)
         where ack.value ->> 'sync_stage' = 'activated'
           and nullif(ack.value ->> 'revision_no', '')::bigint = active_revision_no
      ) then
        raise exception 'VPS runtime does not match confirmed active revision';
      end if;
    end if;
  end if;

  for item in select value from jsonb_array_elements(coalesce(p_report -> 'acks', '[]'::jsonb)) loop
    ack_id := item ->> 'ack_id';
    ack_stage := item ->> 'sync_stage';
    begin
      ack_revision_no := (item ->> 'revision_no')::bigint;
      ack_generation := nullif(item ->> 'generation', '')::bigint;
      ack_pack_sha := nullif(item ->> 'pack_sha256', '');
    exception when others then
      raise exception 'VPS acknowledgement identity is invalid';
    end;
    if ack_id !~ '^[0-9a-f]{64}$'
      or ack_id = any(seen_ack_ids)
      or ack_revision_no < 1
      or ack_stage not in ('received', 'preparing', 'activated', 'rejected')
      or item ->> 'adapter_id' <> 'v31f-15m-miaoxiang-sim-adapter'
      or item ->> 'mode' <> 'DRY_RUN' then
      raise exception 'VPS acknowledgement is invalid';
    end if;
    seen_ack_ids := array_append(seen_ack_ids, ack_id);
    select * into revision_row from public.vps_whitelist_revisions where revision_no = ack_revision_no for update;
    if not found
      or revision_row.contract_protocol_version <> 2
      or revision_row.control_payload_sha256 is distinct from item ->> 'control_payload_sha256'
      or revision_row.control_raw_contract_sha256 is distinct from item ->> 'control_raw_contract_sha256'
      or revision_row.members_sha256 is distinct from item ->> 'members_sha256'
      or revision_row.required_snapshot_id is distinct from item ->> 'required_snapshot_id'
      or revision_row.required_snapshot_sha256 is distinct from item ->> 'required_snapshot_sha256'
      or revision_row.contract_adapter_id <> item ->> 'adapter_id'
      or revision_row.contract_mode <> item ->> 'mode' then
      raise exception 'VPS acknowledgement immutable evidence is invalid';
    end if;
    if revision_row.contract_expires_at <= now() and ack_stage <> 'rejected' then
      raise exception 'VPS acknowledgement references an expired control revision';
    end if;
    if ack_stage = 'activated' then
      if ack_generation is null or ack_pack_sha is null
        or active_revision_no is distinct from ack_revision_no
        or nullif(p_report ->> 'active_generation', '')::bigint is distinct from ack_generation
        or nullif(p_report ->> 'active_pack_sha256', '') is distinct from ack_pack_sha
        or nullif(p_report ->> 'active_control_payload_sha256', '') is distinct from item ->> 'control_payload_sha256'
        or nullif(p_report ->> 'active_control_raw_contract_sha256', '') is distinct from item ->> 'control_raw_contract_sha256'
        or nullif(p_report ->> 'active_members_sha256', '') is distinct from item ->> 'members_sha256'
        or nullif(p_report ->> 'active_snapshot_id', '') is distinct from item ->> 'required_snapshot_id'
        or nullif(p_report ->> 'active_snapshot_sha256', '') is distinct from item ->> 'required_snapshot_sha256'
      then
        raise exception 'Activated VPS acknowledgement does not match runtime linkage';
      end if;
      if revision_row.status in ('rejected', 'superseded') then
        raise exception 'Activated VPS acknowledgement references a stale revision';
      end if;
      if revision_row.status = 'active' then
        if revision_row.active_generation is distinct from ack_generation
          or revision_row.active_pack_sha256 is distinct from ack_pack_sha then
          raise exception 'Activated VPS acknowledgement attempts same-revision generation or pack rollback';
        end if;
      else
        if exists (
          select 1 from public.vps_whitelist_revisions
           where status = 'active'
             and revision_no <> ack_revision_no
             and active_generation >= ack_generation
        ) then
          raise exception 'Activated VPS acknowledgement generation regresses';
        end if;
      end if;
    end if;

    ack_body_sha := encode(digest(item::text, 'sha256'), 'hex');
    insert into public.vps_sync_ack_receipts(device_id, ack_id, ack_body_sha256)
    values (p_device_id, ack_id, ack_body_sha)
    on conflict on constraint vps_sync_ack_receipts_pkey do nothing;
    get diagnostics ack_receipt_rows = row_count;
    select receipt.ack_body_sha256 into stored_ack_body_sha
      from public.vps_sync_ack_receipts as receipt
     where receipt.device_id = p_device_id and receipt.ack_id = item ->> 'ack_id'
     for update;
    if stored_ack_body_sha is distinct from ack_body_sha then
      raise exception 'VPS acknowledgement id conflicts with previous content';
    end if;
    accepted_ack_ids := accepted_ack_ids || jsonb_build_array(ack_id);
    if ack_receipt_rows = 1 then
      legacy_acks := legacy_acks || jsonb_build_array(item);
    end if;
  end loop;

  -- v1 still owns bounded event/symbol/position projection and its historical
  -- status transition logic.  It receives the same allow-listed body with only
  -- the schema marker downgraded; unknown v2 evidence is ignored by v1 and is
  -- cross-checked by this wrapper before/after the call.
  legacy_report := jsonb_set(p_report, '{schema_version}', '1'::jsonb, true);
  legacy_report := jsonb_set(legacy_report, '{acks}', legacy_acks, true);
  v1_result := public.vps_sync_ingest_report_v1(p_device_id, legacy_report);

  if active_revision_no is not null then
    select * into revision_row from public.vps_whitelist_revisions where revision_no = active_revision_no;
    if not found
      or revision_row.status <> 'active'
      or revision_row.active_generation is distinct from nullif(p_report ->> 'active_generation', '')::bigint
      or revision_row.active_pack_sha256 is distinct from nullif(p_report ->> 'active_pack_sha256', '') then
      raise exception 'VPS runtime active revision transition was not confirmed';
    end if;
  end if;

  update public.vps_runtime_snapshot
     set active_control_payload_sha256 = nullif(p_report ->> 'active_control_payload_sha256', ''),
         active_control_raw_contract_sha256 = nullif(p_report ->> 'active_control_raw_contract_sha256', ''),
         active_members_sha256 = nullif(p_report ->> 'active_members_sha256', ''),
         active_snapshot_id = nullif(p_report ->> 'active_snapshot_id', ''),
         active_snapshot_sha256 = nullif(p_report ->> 'active_snapshot_sha256', '')
   where id = true;

  -- Link the immutable VPS ACK identifier to the existing bounded ACK record.
  -- The source_ack_key was inserted by v1; this update never rewrites content.
  for item in select value from jsonb_array_elements(coalesce(p_report -> 'acks', '[]'::jsonb)) loop
    update public.vps_sync_acks
       set vps_ack_id = item ->> 'ack_id',
           control_payload_sha256 = item ->> 'control_payload_sha256',
           control_raw_contract_sha256 = item ->> 'control_raw_contract_sha256',
           members_sha256 = item ->> 'members_sha256',
           required_snapshot_id = item ->> 'required_snapshot_id',
           required_snapshot_sha256 = item ->> 'required_snapshot_sha256',
           adapter_id = item ->> 'adapter_id',
           mode = item ->> 'mode'
     where revision_no = (item ->> 'revision_no')::bigint
       and sync_stage::text = item ->> 'sync_stage'
       and generation is not distinct from nullif(item ->> 'generation', '')::bigint
       and pack_sha256 is not distinct from nullif(item ->> 'pack_sha256', '')
       and reported_at = (item ->> 'reported_at_cn')::timestamptz
       and message is not distinct from nullif(item ->> 'message', '')
       and rejection_code is not distinct from nullif(item ->> 'rejection_code', '');
  end loop;

  return jsonb_build_object(
    'accepted_ack_ids', accepted_ack_ids
  );
end;
$$;

create or replace function public.vps_sync_ingest_report_legacy(
  p_device_id text,
  p_report jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  report_generated_at timestamptz;
  report_mode text;
  report_health text;
  active_revision_no bigint;
  active_revision_id uuid;
  active_generation bigint;
  active_pack_sha text;
  item jsonb;
  item_symbol text;
  item_status text;
  item_reason text;
  item_timestamp timestamptz;
  item_fresh_at timestamptz;
  item_active_revision bigint;
  item_bool boolean;
  item_quantity bigint;
  item_available bigint;
  item_position_state text;
  ack_stage text;
  ack_revision_no bigint;
  ack_generation bigint;
  ack_pack_sha text;
  ack_reported_at timestamptz;
  ack_message text;
  ack_rejection_code text;
  ack_key text;
  event_severity text;
  event_code text;
  event_message text;
  event_symbol text;
  event_action text;
  event_revision_no bigint;
  event_generation bigint;
  event_timestamp timestamptz;
  event_key text;
  accepted_ack_keys jsonb := '[]'::jsonb;
  active_status public.vps_whitelist_status;
begin
  if not exists (
    select 1 from public.vps_sync_devices
    where device_id = p_device_id and enabled = true
  ) then
    raise exception 'VPS gateway device is disabled';
  end if;
  if jsonb_typeof(p_report) <> 'object' then
    raise exception 'VPS runtime report must be an object';
  end if;
  if coalesce(p_report ->> 'schema_version', '') <> '1' then
    raise exception 'Unsupported VPS runtime report schema';
  end if;
  if p_report ->> 'adapter_id' <> 'v31f-15m-miaoxiang-sim-adapter' then
    raise exception 'VPS adapter does not match control plane';
  end if;
  report_mode := coalesce(p_report ->> 'mode', '');
  if report_mode <> 'DRY_RUN' then
    raise exception 'Stage 2 accepts DRY_RUN reports only';
  end if;
  report_health := coalesce(p_report ->> 'health_status', '');
  if report_health not in ('ok', 'degraded', 'failed', 'unknown') then
    raise exception 'VPS runtime health status is invalid';
  end if;
  begin
    report_generated_at := (p_report ->> 'generated_at_cn')::timestamptz;
  exception when others then
    raise exception 'VPS runtime generated_at_cn is invalid';
  end;

  active_revision_no := nullif(p_report ->> 'active_revision_no', '')::bigint;
  active_generation := nullif(p_report ->> 'active_generation', '')::bigint;
  active_pack_sha := nullif(p_report ->> 'active_pack_sha256', '');
  if active_revision_no is not null and active_revision_no < 1 then
    raise exception 'VPS active revision is invalid';
  end if;
  if active_generation is not null and active_generation < 1 then
    raise exception 'VPS active generation is invalid';
  end if;
  if active_pack_sha is not null and active_pack_sha !~ '^[0-9a-f]{64}$' then
    raise exception 'VPS active pack hash is invalid';
  end if;

  -- Process immutable acknowledgements first. A stale/superseded revision can
  -- never reactivate itself merely because an old VPS report arrives late.
  for item in select value from jsonb_array_elements(coalesce(p_report -> 'acks', '[]'::jsonb)) loop
    ack_revision_no := nullif(item ->> 'revision_no', '')::bigint;
    ack_stage := coalesce(item ->> 'sync_stage', '');
    ack_generation := nullif(item ->> 'generation', '')::bigint;
    ack_pack_sha := nullif(item ->> 'pack_sha256', '');
    ack_message := nullif(item ->> 'message', '');
    ack_rejection_code := nullif(item ->> 'rejection_code', '');
    begin
      ack_reported_at := (item ->> 'reported_at_cn')::timestamptz;
    exception when others then
      raise exception 'VPS acknowledgement timestamp is invalid';
    end;
    if ack_revision_no is null or ack_revision_no < 1 or ack_stage not in ('received', 'preparing', 'activated', 'rejected') then
      raise exception 'VPS acknowledgement is invalid';
    end if;
    if ack_generation is not null and ack_generation < 1 then
      raise exception 'VPS acknowledgement generation is invalid';
    end if;
    if ack_pack_sha is not null and ack_pack_sha !~ '^[0-9a-f]{64}$' then
      raise exception 'VPS acknowledgement pack hash is invalid';
    end if;
    if ack_message is not null and (char_length(ack_message) > 500 or not public.vps_text_is_safe(ack_message)) then
      raise exception 'VPS acknowledgement message is unsafe';
    end if;
    if ack_rejection_code is not null and ack_rejection_code !~ '^[a-z0-9_.-]{1,80}$' then
      raise exception 'VPS acknowledgement rejection code is invalid';
    end if;
    if not exists (select 1 from public.vps_whitelist_revisions where revision_no = ack_revision_no) then
      raise exception 'VPS acknowledgement references an unknown whitelist revision';
    end if;

    ack_key := encode(digest(concat_ws('|', p_device_id, ack_revision_no::text, ack_stage, coalesce(ack_generation::text, ''), coalesce(ack_pack_sha, ''), ack_reported_at::text, coalesce(ack_rejection_code, ''), coalesce(ack_message, '')), 'sha256'), 'hex');
    insert into public.vps_sync_acks (
      revision_id, revision_no, sync_stage, generation, pack_sha256,
      reported_at, message, rejection_code, source_ack_key
    )
    select id, revision_no, ack_stage::public.vps_sync_stage, ack_generation, ack_pack_sha,
      ack_reported_at, ack_message, ack_rejection_code, ack_key
      from public.vps_whitelist_revisions
     where revision_no = ack_revision_no
    on conflict (source_ack_key) where source_ack_key is not null do nothing;

    if found then
      accepted_ack_keys := accepted_ack_keys || jsonb_build_array(ack_key);
      if ack_stage = 'received' then
        update public.vps_whitelist_revisions
           set status = case when status = 'submitted' then 'sync_pending' else status end
         where revision_no = ack_revision_no;
      elsif ack_stage = 'preparing' then
        update public.vps_whitelist_revisions
           set status = 'preparing'
         where revision_no = ack_revision_no
           and status in ('submitted', 'sync_pending', 'preparing');
      elsif ack_stage = 'rejected' then
        update public.vps_whitelist_revisions
           set status = 'rejected',
               rejection_code = coalesce(ack_rejection_code, 'vps_rejected'),
               rejection_message = coalesce(ack_message, 'VPS rejected the requested whitelist revision.')
         where revision_no = ack_revision_no
           and status in ('submitted', 'sync_pending', 'preparing');
      elsif ack_stage = 'activated' then
        if ack_generation is null or ack_pack_sha is null then
          raise exception 'Activated acknowledgement requires generation and pack hash';
        end if;
        select status into active_status
          from public.vps_whitelist_revisions
         where revision_no = ack_revision_no
         for update;
        if active_status <> 'superseded' and active_status <> 'rejected' then
          update public.vps_whitelist_revisions
             set status = 'superseded'
           where status = 'active'
             and revision_no <> ack_revision_no;
          update public.vps_whitelist_revisions
             set status = 'active',
                 active_generation = ack_generation,
                 active_pack_sha256 = ack_pack_sha,
                 activated_at = ack_reported_at,
                 rejection_code = null,
                 rejection_message = null
           where revision_no = ack_revision_no;
        end if;
      end if;
    end if;
  end loop;

  if active_revision_no is not null then
    select id into active_revision_id
      from public.vps_whitelist_revisions
     where revision_no = active_revision_no
       and status = 'active';
  end if;

  if coalesce(p_report ->> 'state_summary', '') <> ''
    and (char_length(p_report ->> 'state_summary') > 500 or not public.vps_text_is_safe(p_report ->> 'state_summary')) then
    raise exception 'VPS runtime summary is unsafe';
  end if;
  if coalesce(p_report ->> 'sanitized_error', '') <> ''
    and (char_length(p_report ->> 'sanitized_error') > 500 or not public.vps_text_is_safe(p_report ->> 'sanitized_error')) then
    raise exception 'VPS runtime error summary is unsafe';
  end if;

  insert into public.vps_runtime_snapshot (
    id, schema_version, adapter_id, mode, health_status,
    active_revision_id, active_revision_no, active_generation, active_pack_sha256,
    generated_at, last_control_pull_at, last_strategy_cycle_at,
    last_quote_snapshot_at, last_account_snapshot_at, last_eod_at,
    provider_reads_used, provider_reads_cap, state_summary, sanitized_error
  )
  values (
    true, 1, 'v31f-15m-miaoxiang-sim-adapter', 'DRY_RUN', report_health,
    active_revision_id, active_revision_no, active_generation, active_pack_sha,
    report_generated_at,
    nullif(p_report ->> 'last_control_pull_at_cn', '')::timestamptz,
    nullif(p_report ->> 'last_strategy_cycle_at_cn', '')::timestamptz,
    nullif(p_report ->> 'last_quote_snapshot_at_cn', '')::timestamptz,
    nullif(p_report ->> 'last_account_snapshot_at_cn', '')::timestamptz,
    nullif(p_report ->> 'last_eod_at_cn', '')::timestamptz,
    nullif(p_report ->> 'provider_reads_used', '')::smallint,
    nullif(p_report ->> 'provider_reads_cap', '')::smallint,
    nullif(p_report ->> 'state_summary', ''),
    nullif(p_report ->> 'sanitized_error', '')
  )
  on conflict (id) do update set
    schema_version = excluded.schema_version,
    adapter_id = excluded.adapter_id,
    mode = excluded.mode,
    health_status = excluded.health_status,
    active_revision_id = excluded.active_revision_id,
    active_revision_no = excluded.active_revision_no,
    active_generation = excluded.active_generation,
    active_pack_sha256 = excluded.active_pack_sha256,
    generated_at = excluded.generated_at,
    last_control_pull_at = excluded.last_control_pull_at,
    last_strategy_cycle_at = excluded.last_strategy_cycle_at,
    last_quote_snapshot_at = excluded.last_quote_snapshot_at,
    last_account_snapshot_at = excluded.last_account_snapshot_at,
    last_eod_at = excluded.last_eod_at,
    provider_reads_used = excluded.provider_reads_used,
    provider_reads_cap = excluded.provider_reads_cap,
    state_summary = excluded.state_summary,
    sanitized_error = excluded.sanitized_error;

  delete from public.vps_symbol_states;
  for item in select value from jsonb_array_elements(coalesce(p_report -> 'symbol_states', '[]'::jsonb)) loop
    item_symbol := item ->> 'symbol';
    item_status := item ->> 'status_key';
    item_reason := nullif(item ->> 'status_reason', '');
    begin
      item_timestamp := (item ->> 'source_generated_at_cn')::timestamptz;
      item_fresh_at := nullif(item ->> 'data_fresh_at_cn', '')::timestamptz;
    exception when others then
      raise exception 'VPS symbol-state timestamp is invalid';
    end;
    item_active_revision := nullif(item ->> 'active_revision_no', '')::bigint;
    item_bool := coalesce((item ->> 'is_in_active_whitelist')::boolean, false);
    if item_symbol !~ '^[0-9]{6}\.(SH|SZ)$' or item_status !~ '^[a-z0-9_.-]{1,64}$' then
      raise exception 'VPS symbol state is invalid';
    end if;
    if item_reason is not null and (char_length(item_reason) > 500 or not public.vps_text_is_safe(item_reason)) then
      raise exception 'VPS symbol-state reason is unsafe';
    end if;
    insert into public.vps_symbol_states (
      symbol, status_key, status_reason, source_generated_at, data_fresh_at,
      active_revision_no, is_in_active_whitelist
    ) values (
      item_symbol, item_status, item_reason, item_timestamp, item_fresh_at,
      item_active_revision, item_bool
    );
  end loop;

  delete from public.vps_sim_positions;
  for item in select value from jsonb_array_elements(coalesce(p_report -> 'paper_positions', '[]'::jsonb)) loop
    item_symbol := item ->> 'symbol';
    item_quantity := nullif(item ->> 'held_quantity', '')::bigint;
    item_available := nullif(item ->> 'available_quantity', '')::bigint;
    item_position_state := item ->> 'position_state';
    begin
      item_timestamp := (item ->> 'source_generated_at_cn')::timestamptz;
    exception when others then
      raise exception 'VPS position timestamp is invalid';
    end;
    item_active_revision := nullif(item ->> 'active_revision_no', '')::bigint;
    if item_symbol !~ '^[0-9]{6}\.(SH|SZ)$'
      or item_quantity is null or item_quantity < 0
      or item_available is null or item_available < 0 or item_available > item_quantity
      or item_position_state !~ '^[a-z0-9_.-]{1,64}$' then
      raise exception 'VPS paper position is invalid';
    end if;
    insert into public.vps_sim_positions (
      symbol, held_quantity, available_quantity, position_state,
      source_generated_at, active_revision_no
    ) values (
      item_symbol, item_quantity, item_available, item_position_state,
      item_timestamp, item_active_revision
    );
  end loop;

  for item in select value from jsonb_array_elements(coalesce(p_report -> 'events', '[]'::jsonb)) loop
    begin
      event_timestamp := (item ->> 'occurred_at_cn')::timestamptz;
    exception when others then
      raise exception 'VPS event timestamp is invalid';
    end;
    event_severity := item ->> 'severity';
    event_code := item ->> 'event_code';
    event_message := item ->> 'message';
    event_symbol := nullif(item ->> 'symbol', '');
    event_action := nullif(item ->> 'action', '');
    event_revision_no := nullif(item ->> 'revision_no', '')::bigint;
    event_generation := nullif(item ->> 'generation', '')::bigint;
    if event_severity not in ('info', 'warning', 'error')
      or event_code !~ '^[a-z0-9_.-]{1,80}$'
      or event_message is null or char_length(event_message) > 500
      or not public.vps_text_is_safe(event_message)
      or (event_symbol is not null and event_symbol !~ '^[0-9]{6}\.(SH|SZ)$')
      or (event_action is not null and event_action !~ '^[a-z0-9_.-]{1,64}$') then
      raise exception 'VPS runtime event is invalid or unsafe';
    end if;
    event_key := encode(digest(concat_ws('|', p_device_id, event_timestamp::text, event_severity, event_code, coalesce(event_symbol, ''), coalesce(event_action, ''), coalesce(event_revision_no::text, ''), coalesce(event_generation::text, ''), event_message), 'sha256'), 'hex');
    insert into public.vps_runtime_events (
      occurred_at, severity, event_code, message, symbol, action,
      revision_no, generation, source_event_key
    ) values (
      event_timestamp, event_severity::public.vps_event_severity, event_code,
      event_message, event_symbol, event_action, event_revision_no,
      event_generation, event_key
    ) on conflict (source_event_key) where source_event_key is not null do nothing;
  end loop;

  -- Keep the dashboard diagnostic history bounded. The raw VPS SQLite audit log
  -- remains local and is never deleted by this private-control-plane cleanup.
  delete from public.vps_runtime_events
   where occurred_at < now() - interval '30 days';

  return jsonb_build_object('accepted_ack_keys', accepted_ack_keys);
end;
$$;

-- Existing owner-only wrapper calls continue to work under SECURITY DEFINER.
-- Do not grant legacy direct execution to service_role merely because it has RLS bypass.
do $$
declare crypto_schema text; selected_path text;
begin
  select n.nspname into crypto_schema from pg_extension x join pg_namespace n on n.oid=x.extnamespace where x.extname='pgcrypto';
  selected_path := case when crypto_schema='public' then 'pg_catalog, public' else format('pg_catalog, %I, public',crypto_schema) end;
  execute 'alter function public.vps_sync_ingest_report_v2_base(text,jsonb) set search_path = ' || selected_path;
  execute 'alter function public.vps_sync_ingest_report_legacy(text,jsonb) set search_path = ' || selected_path;
end;
$$;
revoke all on function public.vps_sync_ingest_report_legacy(text,jsonb) from public,anon,authenticated,service_role;
revoke all on function public.vps_sync_ingest_report_v2_base(text,jsonb) from public,anon,authenticated,service_role;

do $$
declare role_name text;
begin
  if (select md5(prosrc) from pg_proc where oid='public.vps_sync_ingest_report_v2_base(text,jsonb)'::regprocedure)<>'99cee6ae98df57f2b7dbd8275c6fd02d' then
    raise exception 'ACK repair body verification failed';
  end if;
  if (select md5(prosrc) from pg_proc where oid='public.vps_sync_ingest_report_legacy(text,jsonb)'::regprocedure)<>'58134fff85fda576d209c0ca400aada1' then
    raise exception 'ACK legacy body verification failed';
  end if;
  foreach role_name in array array['anon','authenticated','service_role'] loop
    if has_function_privilege(role_name,'public.vps_sync_ingest_report_legacy(text,jsonb)','EXECUTE')
      or has_function_privilege(role_name,'public.vps_sync_ingest_report_v2_base(text,jsonb)','EXECUTE') then
      raise exception 'ACK legacy/core ACL closure failed';
    end if;
  end loop;
end;
$$;
commit;

select jsonb_build_object(
  'repair_body_matches',(select md5(prosrc)='99cee6ae98df57f2b7dbd8275c6fd02d' from pg_proc where oid='public.vps_sync_ingest_report_v2_base(text,jsonb)'::regprocedure),
  'legacy_body_matches',(select md5(prosrc)='58134fff85fda576d209c0ca400aada1' from pg_proc where oid='public.vps_sync_ingest_report_legacy(text,jsonb)'::regprocedure),
  'legacy_function_settings',(select to_jsonb(proconfig) from pg_proc where oid='public.vps_sync_ingest_report_legacy(text,jsonb)'::regprocedure),
  'v2_function_settings',(select to_jsonb(proconfig) from pg_proc where oid='public.vps_sync_ingest_report_v2_base(text,jsonb)'::regprocedure),
  'legacy_anon_execute',has_function_privilege('anon','public.vps_sync_ingest_report_legacy(text,jsonb)','EXECUTE'),
  'legacy_authenticated_execute',has_function_privilege('authenticated','public.vps_sync_ingest_report_legacy(text,jsonb)','EXECUTE'),
  'legacy_service_role_execute',has_function_privilege('service_role','public.vps_sync_ingest_report_legacy(text,jsonb)','EXECUTE'),
  'publish_anon_execute',has_function_privilege('anon','public.vps_sync_publish_request(text,text,text,jsonb)','EXECUTE'),
  'publish_authenticated_execute',has_function_privilege('authenticated','public.vps_sync_publish_request(text,text,text,jsonb)','EXECUTE'),
  'publish_service_role_execute',has_function_privilege('service_role','public.vps_sync_publish_request(text,text,text,jsonb)','EXECUTE')
) as ack_repair_postflight;
