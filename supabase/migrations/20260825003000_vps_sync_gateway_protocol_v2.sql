-- Stock dashboard / VPS private control plane — Stage 2 protocol-v2 safety closure.
--
-- This migration is intentionally LOCAL SOURCE ONLY at this stage.  It has not
-- been applied to Supabase and must not be applied until the separate deployment
-- gate is approved.  It upgrades the outbound-only gateway contract without
-- putting a secret in Postgres, the dashboard, a market pack, or this file.
--
-- v2 guarantees:
--   * desired whitelist revisions are immutable, 1–50 symbols and DRY_RUN-only;
--   * each revision carries device/adapter/mode/expiry/membership/snapshot/hash evidence;
--   * pull/publish have a durable request-receipt ledger for timeout retries;
--   * ACK ids are stable and conflicting reuse fails closed;
--   * Edge service_role can execute only narrow SECURITY DEFINER RPCs.

begin;

-- Future revisions must be bound to a specific independently verified local
-- market snapshot.  These nullable snapshot fields deliberately make the
-- existing Stage-1 settings safe but non-publishable until a verified operator
-- explicitly supplies snapshot evidence through the audited admin RPC below.
alter table public.vps_control_settings
  add column if not exists gateway_protocol_version smallint not null default 2,
  add column if not exists target_device_id text not null default 'stock-sim-v31f-15m',
  add column if not exists expected_adapter_id text not null default 'v31f-15m-miaoxiang-sim-adapter',
  add column if not exists expected_source_policy_sha256 text not null default '7330a793c79b3c2f2bf0c52b2d085b98cdc9b851fbc88f7c3d399030d8d54c75',
  add column if not exists required_snapshot_id text,
  add column if not exists required_snapshot_sha256 text,
  add column if not exists revision_ttl_seconds integer not null default 604800;

-- Additive nullable fields preserve existing Stage-1 historical records.  Only
-- v2 submissions populate all of them, and only such rows are eligible for
-- hosted pull.
alter table public.vps_whitelist_revisions
  add column if not exists contract_protocol_version smallint,
  add column if not exists target_device_id text,
  add column if not exists contract_adapter_id text,
  add column if not exists contract_mode text,
  add column if not exists source_policy_sha256 text,
  add column if not exists members_sha256 text,
  add column if not exists required_snapshot_id text,
  add column if not exists required_snapshot_sha256 text,
  add column if not exists contract_created_at timestamptz,
  add column if not exists contract_expires_at timestamptz,
  add column if not exists control_payload_sha256 text,
  add column if not exists control_raw_contract text,
  add column if not exists control_raw_contract_sha256 text;

alter table public.vps_runtime_snapshot
  add column if not exists active_control_payload_sha256 text,
  add column if not exists active_control_raw_contract_sha256 text,
  add column if not exists active_members_sha256 text,
  add column if not exists active_snapshot_id text,
  add column if not exists active_snapshot_sha256 text;

alter table public.vps_sync_acks
  add column if not exists vps_ack_id text,
  add column if not exists control_payload_sha256 text,
  add column if not exists control_raw_contract_sha256 text,
  add column if not exists members_sha256 text,
  add column if not exists required_snapshot_id text,
  add column if not exists required_snapshot_sha256 text,
  add column if not exists adapter_id text,
  add column if not exists mode text;

create unique index if not exists vps_sync_acks_vps_ack_id_unique
  on public.vps_sync_acks (vps_ack_id)
  where vps_ack_id is not null;

-- Only a hash of the raw HMAC-authenticated request body is stored.  The body,
-- nonce, HMAC secret and any provider/account value are never retained here.
create table if not exists public.vps_sync_request_receipts (
  device_id text not null references public.vps_sync_devices(device_id) on delete cascade,
  request_id text not null check (request_id ~ '^[0-9a-f]{64}$'),
  operation text not null check (operation in ('pull', 'publish')),
  request_body_sha256 text not null check (request_body_sha256 ~ '^[0-9a-f]{64}$'),
  response_json jsonb not null,
  created_at timestamptz not null default now(),
  primary key (device_id, request_id)
);

-- Stable VPS ACK ids cannot be recycled for different body content.  This table
-- is the immutable local-identifier mapping; legacy source_ack_key remains for
-- backward-compatible event/ACK table deduplication.
create table if not exists public.vps_sync_ack_receipts (
  device_id text not null references public.vps_sync_devices(device_id) on delete cascade,
  ack_id text not null check (ack_id ~ '^[0-9a-f]{64}$'),
  ack_body_sha256 text not null check (ack_body_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  primary key (device_id, ack_id)
);

create table if not exists public.vps_control_contract_audit (
  id uuid primary key default gen_random_uuid(),
  occurred_at timestamptz not null default now(),
  actor_user_id uuid references auth.users(id) on delete set null,
  action text not null check (action in ('snapshot_requirement_set')),
  snapshot_id text not null,
  snapshot_sha256 text not null check (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  ttl_seconds integer not null check (ttl_seconds between 300 and 2592000)
);

alter table public.vps_sync_request_receipts enable row level security;
alter table public.vps_sync_ack_receipts enable row level security;
alter table public.vps_control_contract_audit enable row level security;
revoke all on table public.vps_sync_request_receipts, public.vps_sync_ack_receipts, public.vps_control_contract_audit from anon, authenticated, service_role;

-- Canonical v2 strings avoid any JSON whitespace/order differences across
-- PostgreSQL, Python and Deno.  The formal membership identity is a sorted set,
-- while the browser's display order remains in revision_items.sort_order.
create or replace function public.vps_contract_time_text(p_value timestamptz)
returns text
language sql
immutable
strict
set search_path = pg_catalog
as $$
  select to_char(date_trunc('second', p_value) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
$$;

create or replace function public.vps_members_sha256(p_symbols text[])
returns text
language plpgsql
immutable
strict
set search_path = pg_catalog, public
as $$
declare
  normalized text[];
  item text;
begin
  if cardinality(p_symbols) is null or cardinality(p_symbols) not between 1 and 50 then
    raise exception 'VPS whitelist membership must contain 1 to 50 symbols';
  end if;
  select array_agg(upper(btrim(item.symbol)) order by upper(btrim(item.symbol)))
    into normalized
    from unnest(p_symbols) as item(symbol);
  foreach item in array normalized loop
    if item !~ '^[0-9]{6}\.(SH|SZ)$' then
      raise exception 'VPS whitelist membership contains invalid symbol';
    end if;
  end loop;
  if (select count(*) from unnest(normalized) as item(symbol)) <> (select count(distinct item.symbol) from unnest(normalized) as item(symbol)) then
    raise exception 'VPS whitelist membership contains duplicate symbols';
  end if;
  return encode(digest('vps-members-v2' || E'\n' || array_to_string(normalized, E'\n') || E'\n', 'sha256'), 'hex');
end;
$$;

create or replace function public.vps_revision_payload_text(
  p_revision_no bigint,
  p_target_device_id text,
  p_adapter_id text,
  p_mode text,
  p_created_at text,
  p_expires_at text,
  p_source_policy_sha256 text,
  p_members_sha256 text,
  p_required_snapshot_id text,
  p_required_snapshot_sha256 text,
  p_symbols text[]
)
returns text
language sql
immutable
strict
set search_path = pg_catalog
as $$
  select 'vps-whitelist-payload-v2' || E'\n'
    || 'protocol_version=2' || E'\n'
    || 'revision_no=' || p_revision_no::text || E'\n'
    || 'target_device_id=' || p_target_device_id || E'\n'
    || 'adapter_id=' || p_adapter_id || E'\n'
    || 'mode=' || p_mode || E'\n'
    || 'created_at=' || p_created_at || E'\n'
    || 'expires_at=' || p_expires_at || E'\n'
    || 'source_policy_sha256=' || p_source_policy_sha256 || E'\n'
    || 'members_sha256=' || p_members_sha256 || E'\n'
    || 'required_snapshot_id=' || p_required_snapshot_id || E'\n'
    || 'required_snapshot_sha256=' || p_required_snapshot_sha256 || E'\n'
    || coalesce((
      select string_agg('symbol=' || upper(btrim(item.symbol)), E'\n' order by upper(btrim(item.symbol))) || E'\n'
      from unnest(p_symbols) as item(symbol)
    ), '');
$$;

create or replace function public.vps_revision_raw_contract_text(
  p_payload_text text,
  p_payload_sha256 text
)
returns text
language sql
immutable
strict
set search_path = pg_catalog
as $$
  select 'vps-whitelist-raw-v2' || E'\n' || p_payload_text || 'payload_sha256=' || p_payload_sha256 || E'\n';
$$;

-- The snapshot requirement affects only newly submitted revisions.  Existing
-- active/pending rows are immutable historical evidence and are never rewritten.
create or replace function public.vps_set_control_snapshot_requirement(
  p_snapshot_id text,
  p_snapshot_sha256 text,
  p_ttl_seconds integer default null
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  resolved_ttl integer;
begin
  if not public.vps_is_admin() then
    raise exception 'VPS dashboard administrator access is required' using errcode = '42501';
  end if;
  if p_snapshot_id is null or btrim(p_snapshot_id) !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
    or not public.vps_text_is_safe(p_snapshot_id) then
    raise exception 'Snapshot id is invalid';
  end if;
  if p_snapshot_sha256 is null or lower(btrim(p_snapshot_sha256)) !~ '^[0-9a-f]{64}$' then
    raise exception 'Snapshot SHA-256 is invalid';
  end if;
  select coalesce(p_ttl_seconds, revision_ttl_seconds)
    into resolved_ttl
    from public.vps_control_settings
   where id = true
   for update;
  if resolved_ttl not between 300 and 2592000 then
    raise exception 'Revision TTL is outside the safe range';
  end if;
  update public.vps_control_settings
     set required_snapshot_id = btrim(p_snapshot_id),
         required_snapshot_sha256 = lower(btrim(p_snapshot_sha256)),
         revision_ttl_seconds = resolved_ttl
   where id = true;
  insert into public.vps_control_contract_audit(actor_user_id, action, snapshot_id, snapshot_sha256, ttl_seconds)
  values (auth.uid(), 'snapshot_requirement_set', btrim(p_snapshot_id), lower(btrim(p_snapshot_sha256)), resolved_ttl);
end;
$$;

-- Once a v2 revision exists, its contract evidence and submitted item list are
-- historical proof.  Status/activation metadata may evolve, but no caller may
-- rewrite membership, snapshot, policy or payload evidence in place.
create or replace function public.vps_guard_v2_revision_immutability()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if new.contract_protocol_version = 2 and old.contract_protocol_version is distinct from 2 then
    raise exception 'Existing revision cannot be converted into an immutable v2 revision';
  end if;
  if old.contract_protocol_version = 2 and (
    new.contract_protocol_version is distinct from old.contract_protocol_version
    or new.revision_no is distinct from old.revision_no
    or new.desired_symbol_count is distinct from old.desired_symbol_count
    or new.target_device_id is distinct from old.target_device_id
    or new.contract_adapter_id is distinct from old.contract_adapter_id
    or new.contract_mode is distinct from old.contract_mode
    or new.source_policy_sha256 is distinct from old.source_policy_sha256
    or new.members_sha256 is distinct from old.members_sha256
    or new.required_snapshot_id is distinct from old.required_snapshot_id
    or new.required_snapshot_sha256 is distinct from old.required_snapshot_sha256
    or new.contract_created_at is distinct from old.contract_created_at
    or new.contract_expires_at is distinct from old.contract_expires_at
    or new.control_payload_sha256 is distinct from old.control_payload_sha256
    or new.control_raw_contract is distinct from old.control_raw_contract
    or new.control_raw_contract_sha256 is distinct from old.control_raw_contract_sha256
  ) then
    raise exception 'Immutable v2 whitelist revision evidence cannot be changed';
  end if;
  return new;
end;
$$;

drop trigger if exists vps_v2_revision_immutability on public.vps_whitelist_revisions;
create trigger vps_v2_revision_immutability
before update on public.vps_whitelist_revisions
for each row execute function public.vps_guard_v2_revision_immutability();

create or replace function public.vps_guard_v2_revision_item_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  parent_protocol smallint;
  parent_revision_id uuid;
begin
  if tg_op = 'DELETE' then
    parent_revision_id := old.revision_id;
  else
    parent_revision_id := new.revision_id;
  end if;
  select contract_protocol_version into parent_protocol
    from public.vps_whitelist_revisions
   where id = parent_revision_id;
  if parent_protocol = 2 then
    raise exception 'Immutable v2 whitelist revision items cannot be changed';
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end;
$$;

drop trigger if exists vps_v2_revision_item_mutation on public.vps_whitelist_revision_items;
create trigger vps_v2_revision_item_mutation
before update or delete on public.vps_whitelist_revision_items
for each row execute function public.vps_guard_v2_revision_item_mutation();

create or replace function public.vps_assert_v2_revision_item_consistency()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  revision_id_to_check uuid;
  chosen public.vps_whitelist_revisions%rowtype;
  selected_symbols text[];
  selected_count integer;
  distinct_count integer;
  min_sort smallint;
  max_sort smallint;
  members_sha text;
  payload_text text;
  payload_sha text;
  raw_contract text;
begin
  if tg_op = 'DELETE' then
    revision_id_to_check := old.revision_id;
  else
    revision_id_to_check := new.revision_id;
  end if;
  select * into chosen
    from public.vps_whitelist_revisions
   where id = revision_id_to_check;
  if not found or chosen.contract_protocol_version is distinct from 2 then
    return null;
  end if;

  select
    array_agg(upper(btrim(item.symbol)) order by item.sort_order),
    count(*),
    count(distinct upper(btrim(item.symbol))),
    min(item.sort_order),
    max(item.sort_order)
    into selected_symbols, selected_count, distinct_count, min_sort, max_sort
    from public.vps_whitelist_revision_items as item
   where item.revision_id = chosen.id;

  if selected_count not between 1 and 50
    or selected_count <> chosen.desired_symbol_count
    or distinct_count <> selected_count
    or min_sort <> 0
    or max_sort <> selected_count - 1 then
    raise exception 'V2 whitelist revision items violate the frozen 1-50 contract';
  end if;

  members_sha := public.vps_members_sha256(selected_symbols);
  if members_sha <> chosen.members_sha256 then
    raise exception 'V2 whitelist revision membership hash does not match items';
  end if;
  payload_text := public.vps_revision_payload_text(
    chosen.revision_no, chosen.target_device_id, chosen.contract_adapter_id, chosen.contract_mode,
    public.vps_contract_time_text(chosen.contract_created_at), public.vps_contract_time_text(chosen.contract_expires_at),
    chosen.source_policy_sha256, chosen.members_sha256, chosen.required_snapshot_id,
    chosen.required_snapshot_sha256, selected_symbols
  );
  payload_sha := encode(digest(payload_text, 'sha256'), 'hex');
  raw_contract := public.vps_revision_raw_contract_text(payload_text, payload_sha);
  if payload_sha <> chosen.control_payload_sha256
    or raw_contract <> chosen.control_raw_contract
    or encode(digest(raw_contract, 'sha256'), 'hex') <> chosen.control_raw_contract_sha256 then
    raise exception 'V2 whitelist revision immutable contract hash does not match items';
  end if;
  return null;
end;
$$;

drop trigger if exists vps_v2_revision_item_consistency on public.vps_whitelist_revision_items;
create constraint trigger vps_v2_revision_item_consistency
after insert or update or delete on public.vps_whitelist_revision_items
deferrable initially deferred
for each row execute function public.vps_assert_v2_revision_item_consistency();

-- Replace the Stage-1 submission RPC.  It stays browser-callable only for an
-- authenticated explicit VPS admin, never activates a VPS, and rejects empty
-- lists rather than silently treating an empty list as suspend/default/universe.
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
  select coalesce(max(revision_no), 0) + 1 into next_revision_no from public.vps_whitelist_revisions;
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

-- Returns only a narrow immutable v2 revision.  It can neither carry historical
-- market rows nor use an empty/suspended/default universe.
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
  selected_symbol_array text[];
  selected_count integer;
  distinct_count integer;
  min_sort smallint;
  max_sort smallint;
  recomputed_members_sha text;
begin
  if not exists (select 1 from public.vps_sync_devices where device_id = p_device_id and enabled = true) then
    raise exception 'VPS gateway device is disabled';
  end if;

  update public.vps_whitelist_revisions
     set status = 'rejected',
         rejection_code = 'control_revision_expired',
         rejection_message = 'The requested whitelist revision expired before VPS activation.'
   where status in ('submitted', 'sync_pending', 'preparing')
     and contract_protocol_version = 2
     and contract_expires_at <= now();

  select * into chosen
    from public.vps_whitelist_revisions
   where status in ('submitted', 'sync_pending', 'preparing')
     and contract_protocol_version = 2
     and target_device_id = p_device_id
     and contract_adapter_id = 'v31f-15m-miaoxiang-sim-adapter'
     and contract_mode = 'DRY_RUN'
     and desired_symbol_count between 1 and 50
     and contract_expires_at > now()
     and source_policy_sha256 ~ '^[0-9a-f]{64}$'
     and members_sha256 ~ '^[0-9a-f]{64}$'
     and required_snapshot_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
     and required_snapshot_sha256 ~ '^[0-9a-f]{64}$'
     and control_payload_sha256 ~ '^[0-9a-f]{64}$'
     and control_raw_contract_sha256 ~ '^[0-9a-f]{64}$'
   order by revision_no desc
   limit 1
   for update;
  if not found then
    return null;
  end if;

  if chosen.status = 'submitted' then
    update public.vps_whitelist_revisions set status = 'sync_pending' where id = chosen.id;
    selected_status := 'sync_pending';
  else
    selected_status := chosen.status;
  end if;

  select
    array_agg(upper(btrim(item.symbol)) order by item.sort_order),
    count(*),
    count(distinct upper(btrim(item.symbol))),
    min(item.sort_order),
    max(item.sort_order)
    into selected_symbol_array, selected_count, distinct_count, min_sort, max_sort
    from public.vps_whitelist_revision_items as item
   where item.revision_id = chosen.id;
  if selected_count not between 1 and 50
    or selected_count <> chosen.desired_symbol_count
    or distinct_count <> selected_count
    or min_sort <> 0
    or max_sort <> selected_count - 1 then
    raise exception 'VPS gateway revision violates frozen 1-50 whitelist item contract';
  end if;
  recomputed_members_sha := public.vps_members_sha256(selected_symbol_array);
  if recomputed_members_sha <> chosen.members_sha256 then
    raise exception 'VPS gateway revision membership hash does not match frozen items';
  end if;
  select jsonb_agg(item.symbol order by item.sort_order)
    into selected_symbols
    from public.vps_whitelist_revision_items as item
   where item.revision_id = chosen.id;

  return jsonb_build_object(
    'protocol_version', 2,
    'revision_no', chosen.revision_no,
    'status', selected_status,
    'target_device_id', chosen.target_device_id,
    'adapter_id', chosen.contract_adapter_id,
    'mode', chosen.contract_mode,
    'created_at', public.vps_contract_time_text(chosen.contract_created_at),
    'expires_at', public.vps_contract_time_text(chosen.contract_expires_at),
    'source_policy_sha256', chosen.source_policy_sha256,
    'members_sha256', chosen.members_sha256,
    'required_snapshot_id', chosen.required_snapshot_id,
    'required_snapshot_sha256', chosen.required_snapshot_sha256,
    'payload_sha256', chosen.control_payload_sha256,
    'raw_contract_sha256', chosen.control_raw_contract_sha256,
    'raw_contract', chosen.control_raw_contract,
    'symbols', selected_symbols
  );
end;
$$;

-- Preserve the already-applied v1 hardening implementation as an internal
-- compatibility primitive.  The new v2 function below validates immutable
-- evidence and transforms only the schema marker for that bounded primitive.
alter function public.vps_sync_ingest_report(text, jsonb)
  rename to vps_sync_ingest_report_v1;

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
    on conflict (device_id, ack_id) do nothing;
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

-- Durable request receipt wrappers: an identical request_id/body hash returns
-- the exact first stored response; a reused id with different body/operation
-- fails closed.  A new nonce still protects each transport attempt.
create or replace function public.vps_sync_pull_request(
  p_device_id text,
  p_request_id text,
  p_request_body_sha256 text
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  stored public.vps_sync_request_receipts%rowtype;
  revision jsonb;
  result jsonb;
begin
  if p_request_id !~ '^[0-9a-f]{64}$' or p_request_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'VPS request identity is invalid';
  end if;
  if not exists (select 1 from public.vps_sync_devices where device_id = p_device_id and enabled = true) then
    raise exception 'VPS gateway device is disabled';
  end if;
  perform pg_advisory_xact_lock(hashtext(p_device_id || ':' || p_request_id));
  select * into stored from public.vps_sync_request_receipts
   where device_id = p_device_id and request_id = p_request_id;
  if found then
    if stored.operation <> 'pull' or stored.request_body_sha256 <> p_request_body_sha256 then
      raise exception 'VPS request id conflicts with previous content';
    end if;
    return stored.response_json;
  end if;
  revision := public.vps_sync_get_pending_revision(p_device_id);
  result := jsonb_build_object('revision', revision);
  insert into public.vps_sync_request_receipts(device_id, request_id, operation, request_body_sha256, response_json)
  values (p_device_id, p_request_id, 'pull', p_request_body_sha256, result);
  return result;
end;
$$;

create or replace function public.vps_sync_publish_request(
  p_device_id text,
  p_request_id text,
  p_request_body_sha256 text,
  p_report jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  stored public.vps_sync_request_receipts%rowtype;
  result jsonb;
begin
  if p_request_id !~ '^[0-9a-f]{64}$' or p_request_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'VPS request identity is invalid';
  end if;
  if not exists (select 1 from public.vps_sync_devices where device_id = p_device_id and enabled = true) then
    raise exception 'VPS gateway device is disabled';
  end if;
  perform pg_advisory_xact_lock(hashtext(p_device_id || ':' || p_request_id));
  select * into stored from public.vps_sync_request_receipts
   where device_id = p_device_id and request_id = p_request_id;
  if found then
    if stored.operation <> 'publish' or stored.request_body_sha256 <> p_request_body_sha256 then
      raise exception 'VPS request id conflicts with previous content';
    end if;
    return stored.response_json;
  end if;
  result := public.vps_sync_ingest_report(p_device_id, p_report);
  insert into public.vps_sync_request_receipts(device_id, request_id, operation, request_body_sha256, response_json)
  values (p_device_id, p_request_id, 'publish', p_request_body_sha256, result);
  return result;
end;
$$;

-- Browser reads only the audit row through RLS; no direct write permission is
-- added.  GitHub OAuth + vps_is_admin is still the only browser authority.
drop policy if exists vps_control_contract_audit_admin_select on public.vps_control_contract_audit;
create policy vps_control_contract_audit_admin_select
  on public.vps_control_contract_audit for select to authenticated
  using (public.vps_is_admin());

grant select on table public.vps_control_contract_audit to authenticated;

-- Edge service_role now has EXECUTE only on the gateway primitives.  It has no
-- table DML, including on the nonce/receipt tables, and cannot invoke legacy
-- ingestion or raw revision functions directly.
revoke all on function public.vps_sync_consume_nonce(text, text, timestamptz) from public, service_role;
revoke all on function public.vps_sync_get_pending_revision(text) from public, service_role;
revoke all on function public.vps_sync_ingest_report_v1(text, jsonb) from public, service_role;
revoke all on function public.vps_sync_ingest_report(text, jsonb) from public, service_role;
revoke all on function public.vps_sync_pull_request(text, text, text) from public;
revoke all on function public.vps_sync_publish_request(text, text, text, jsonb) from public;
revoke all on function public.vps_set_control_snapshot_requirement(text, text, integer) from public;
grant execute on function public.vps_sync_consume_nonce(text, text, timestamptz) to service_role;
grant execute on function public.vps_sync_pull_request(text, text, text) to service_role;
grant execute on function public.vps_sync_publish_request(text, text, text, jsonb) to service_role;
grant execute on function public.vps_set_control_snapshot_requirement(text, text, integer) to authenticated;

commit;
