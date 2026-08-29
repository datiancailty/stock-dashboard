-- Stock dashboard / VPS private control plane — Stage 2 gateway.
--
-- The VPS remains outbound-only. A signed Supabase Edge Function request is the
-- sole transport. Browser clients never receive the gateway secret, service-role
-- key, VPS address, SSH details, account identifiers, raw provider bodies,
-- prices, costs, market values, P&L, or order/fill identifiers.

begin;

create table if not exists public.vps_sync_devices (
  device_id text primary key check (device_id ~ '^[a-z0-9][a-z0-9._-]{2,79}$'),
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz,
  note text,
  constraint vps_sync_devices_note_safe check (public.vps_text_is_safe(note))
);

-- The HMAC secret itself never enters Postgres. This table supports server-side
-- revocation and replay prevention only.
create table if not exists public.vps_sync_nonces (
  device_id text not null references public.vps_sync_devices(device_id) on delete cascade,
  nonce_sha256 text not null check (nonce_sha256 ~ '^[0-9a-f]{64}$'),
  seen_at timestamptz not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  primary key (device_id, nonce_sha256)
);
create index if not exists vps_sync_nonces_expiry_idx on public.vps_sync_nonces (expires_at);

-- A VPS event/ack may be sent again after a network timeout. Source fingerprints
-- make server-side ingestion idempotent without persisting raw event IDs.
alter table public.vps_runtime_events
  add column if not exists symbol text,
  add column if not exists action text,
  add column if not exists source_event_key text;
alter table public.vps_runtime_events
  drop constraint if exists vps_runtime_events_symbol_format;
alter table public.vps_runtime_events
  add constraint vps_runtime_events_symbol_format check (
    symbol is null or symbol ~ '^[0-9]{6}\.(SH|SZ)$'
  );
alter table public.vps_runtime_events
  drop constraint if exists vps_runtime_events_action_format;
alter table public.vps_runtime_events
  add constraint vps_runtime_events_action_format check (
    action is null or action ~ '^[a-z0-9_.-]{1,64}$'
  );
alter table public.vps_runtime_events
  drop constraint if exists vps_runtime_events_source_event_key_format;
alter table public.vps_runtime_events
  add constraint vps_runtime_events_source_event_key_format check (
    source_event_key is null or source_event_key ~ '^[0-9a-f]{64}$'
  );
create unique index if not exists vps_runtime_events_source_event_key_unique
  on public.vps_runtime_events (source_event_key)
  where source_event_key is not null;

alter table public.vps_sync_acks
  add column if not exists rejection_code text,
  add column if not exists source_ack_key text;
alter table public.vps_sync_acks
  drop constraint if exists vps_sync_acks_rejection_code_format;
alter table public.vps_sync_acks
  add constraint vps_sync_acks_rejection_code_format check (
    rejection_code is null or rejection_code ~ '^[a-z0-9_.-]{1,80}$'
  );
alter table public.vps_sync_acks
  drop constraint if exists vps_sync_acks_source_ack_key_format;
alter table public.vps_sync_acks
  add constraint vps_sync_acks_source_ack_key_format check (
    source_ack_key is null or source_ack_key ~ '^[0-9a-f]{64}$'
  );
create unique index if not exists vps_sync_acks_source_ack_key_unique
  on public.vps_sync_acks (source_ack_key)
  where source_ack_key is not null;

alter table public.vps_sync_devices enable row level security;
alter table public.vps_sync_nonces enable row level security;
revoke all on table public.vps_sync_devices from anon, authenticated;
revoke all on table public.vps_sync_nonces from anon, authenticated;
grant select, insert, update, delete on table public.vps_sync_devices, public.vps_sync_nonces to service_role;
grant select, insert, update, delete on table public.vps_runtime_events, public.vps_sync_acks to service_role;

insert into public.vps_sync_devices (device_id, enabled, note)
values ('stock-sim-v31f-15m', true, 'Outbound-only DRY_RUN gateway device.')
on conflict (device_id) do nothing;

-- Records a nonce hash only after Edge-Function HMAC verification. A duplicate
-- nonce fails closed. The raw nonce and shared secret are never stored.
create or replace function public.vps_sync_consume_nonce(
  p_device_id text,
  p_nonce_sha256 text,
  p_seen_at timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  inserted_count integer := 0;
begin
  if p_device_id !~ '^[a-z0-9][a-z0-9._-]{2,79}$' then
    raise exception 'Invalid VPS gateway device';
  end if;
  if p_nonce_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'Invalid VPS gateway nonce hash';
  end if;
  if p_seen_at is null or abs(extract(epoch from (now() - p_seen_at))) > 600 then
    raise exception 'VPS gateway request timestamp is outside the allowed window';
  end if;
  if not exists (
    select 1 from public.vps_sync_devices
    where device_id = p_device_id and enabled = true
  ) then
    return false;
  end if;

  delete from public.vps_sync_nonces
  where expires_at < now();

  insert into public.vps_sync_nonces (device_id, nonce_sha256, seen_at, expires_at)
  values (p_device_id, p_nonce_sha256, p_seen_at, now() + interval '15 minutes')
  on conflict do nothing;
  get diagnostics inserted_count = row_count;

  if inserted_count = 1 then
    update public.vps_sync_devices
      set last_seen_at = now()
      where device_id = p_device_id;
    return true;
  end if;
  return false;
end;
$$;

-- Returns only the desired canonical symbol revision. It deliberately carries
-- no historical market data; the VPS must use an already verified local market
-- pack and fail closed to PREPARING if the requested symbols are not ready.
create or replace function public.vps_sync_get_pending_revision(p_device_id text)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  chosen public.vps_whitelist_revisions%rowtype;
  selected_status public.vps_whitelist_status;
  selected_symbols jsonb;
begin
  if not exists (
    select 1 from public.vps_sync_devices
    where device_id = p_device_id and enabled = true
  ) then
    raise exception 'VPS gateway device is disabled';
  end if;

  select * into chosen
    from public.vps_whitelist_revisions
   where status in ('submitted', 'sync_pending', 'preparing')
   order by revision_no desc
   limit 1
   for update;

  if not found then
    return null;
  end if;

  if chosen.status = 'submitted' then
    update public.vps_whitelist_revisions
       set status = 'sync_pending'
     where id = chosen.id;
    selected_status := 'sync_pending';
  else
    selected_status := chosen.status;
  end if;

  select coalesce(jsonb_agg(item.symbol order by item.sort_order), '[]'::jsonb)
    into selected_symbols
    from public.vps_whitelist_revision_items as item
   where item.revision_id = chosen.id;

  return jsonb_build_object(
    'revision_no', chosen.revision_no,
    'status', selected_status,
    'symbols', selected_symbols,
    'requested_at', chosen.created_at
  );
end;
$$;

-- Applies a fully validated, HMAC-authenticated and bounded runtime report in
-- one database transaction. The Edge Function validates the precise JSON shape;
-- this function repeats critical database-side checks so a future caller cannot
-- silently bypass control-plane rules.
create or replace function public.vps_sync_ingest_report(
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
    on conflict (source_ack_key) do nothing;

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
    ) on conflict (source_event_key) do nothing;
  end loop;

  -- Keep the dashboard diagnostic history bounded. The raw VPS SQLite audit log
  -- remains local and is never deleted by this private-control-plane cleanup.
  delete from public.vps_runtime_events
   where occurred_at < now() - interval '30 days';

  return jsonb_build_object('accepted_ack_keys', accepted_ack_keys);
end;
$$;

revoke all on function public.vps_sync_consume_nonce(text, text, timestamptz) from public;
revoke all on function public.vps_sync_get_pending_revision(text) from public;
revoke all on function public.vps_sync_ingest_report(text, jsonb) from public;
grant execute on function public.vps_sync_consume_nonce(text, text, timestamptz) to service_role;
grant execute on function public.vps_sync_get_pending_revision(text) to service_role;
grant execute on function public.vps_sync_ingest_report(text, jsonb) to service_role;

commit;
