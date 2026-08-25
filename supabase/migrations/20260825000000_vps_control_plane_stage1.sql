-- Stock dashboard / VPS private control plane — Stage 1
--
-- Scope:
--   * private, RLS-protected dashboard state for the VPS strategy universe;
--   * no VPS endpoint, SSH detail, brokerage identifier, provider response,
--     cost basis, market value, P&L, service-role key, or OAuth secret.
--
-- Bootstrap rule:
--   Do NOT seed public.vps_admins in this migration. After GitHub OAuth has
--   created the intended Supabase Auth user, insert only that verified user_id
--   through the Supabase SQL Editor. This avoids a first-login-becomes-admin
--   vulnerability.

begin;

create extension if not exists pgcrypto;

-- Finite lifecycle values are deliberately separate from strategy-level labels.
-- The VPS implementation will provide its actual SymbolState -> UI mapping in
-- Stage 2; this schema does not fabricate strategy decisions.
do $$
begin
  create type public.vps_whitelist_status as enum (
    'submitted',
    'sync_pending',
    'preparing',
    'active',
    'rejected',
    'superseded'
  );
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.vps_sync_stage as enum (
    'received',
    'preparing',
    'activated',
    'rejected'
  );
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.vps_event_severity as enum (
    'info',
    'warning',
    'error'
  );
exception
  when duplicate_object then null;
end $$;

-- Reject text that is clearly unsafe to publish even into the private control
-- plane. The VPS publisher will apply stronger field-level sanitisation too.
create or replace function public.vps_text_is_safe(value_to_check text)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog
as $$
  select value_to_check is null
    or value_to_check !~* '(authorization|bearer[[:space:]]+[a-z0-9._-]+|api[_-]?key|service[_-]?role|password|secret|access[_-]?token|refresh[_-]?token|private[[:space:]_-]?key|ssh-rsa)';
$$;

create table if not exists public.vps_admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  active boolean not null default true,
  added_at timestamptz not null default now(),
  added_by uuid references auth.users(id) on delete set null,
  note text,
  constraint vps_admins_note_safe check (public.vps_text_is_safe(note))
);

-- This singleton freezes the operational boundary in the database so the
-- browser cannot quietly choose a different symbol-count limit or timezone.
create table if not exists public.vps_control_settings (
  id boolean primary key default true,
  schema_version integer not null default 1 check (schema_version >= 1),
  max_active_symbols smallint not null default 50 check (max_active_symbols between 1 and 50),
  strategy_timezone text not null default 'Asia/Shanghai' check (strategy_timezone = 'Asia/Shanghai'),
  default_mode text not null default 'DRY_RUN' check (default_mode = 'DRY_RUN'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint vps_control_settings_singleton check (id = true)
);

create table if not exists public.vps_whitelist_revisions (
  id uuid primary key default gen_random_uuid(),
  revision_no bigint not null unique,
  status public.vps_whitelist_status not null default 'submitted',
  requested_by uuid references auth.users(id) on delete set null,
  request_note text,
  desired_symbol_count smallint not null check (desired_symbol_count between 1 and 50),
  superseded_by uuid references public.vps_whitelist_revisions(id) on delete set null,
  active_generation bigint check (active_generation is null or active_generation >= 0),
  active_pack_sha256 text check (
    active_pack_sha256 is null
    or active_pack_sha256 ~ '^[0-9a-f]{64}$'
  ),
  rejection_code text check (rejection_code is null or char_length(rejection_code) <= 80),
  rejection_message text,
  activated_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint vps_whitelist_revisions_note_safe check (public.vps_text_is_safe(request_note)),
  constraint vps_whitelist_revisions_rejection_safe check (public.vps_text_is_safe(rejection_message)),
  constraint vps_whitelist_revisions_rejection_length check (
    rejection_message is null or char_length(rejection_message) <= 500
  )
);

create table if not exists public.vps_whitelist_revision_items (
  revision_id uuid not null references public.vps_whitelist_revisions(id) on delete cascade,
  symbol text not null,
  sort_order smallint not null check (sort_order between 0 and 49),
  created_at timestamptz not null default now(),
  primary key (revision_id, symbol),
  unique (revision_id, sort_order),
  constraint vps_whitelist_revision_items_symbol_format check (
    symbol ~ '^[0-9]{6}\.(SH|SZ)$'
  )
);

-- One safe, latest-only operational record. The browser receives status and
-- freshness markers, never raw broker/provider payloads or account identity.
create table if not exists public.vps_runtime_snapshot (
  id boolean primary key default true,
  schema_version integer not null default 1 check (schema_version >= 1),
  adapter_id text not null default 'unknown' check (char_length(adapter_id) <= 120),
  mode text not null default 'DRY_RUN' check (mode in ('DRY_RUN', 'HALTED', 'UNKNOWN')),
  health_status text not null default 'unknown' check (health_status in ('ok', 'degraded', 'failed', 'unknown')),
  active_revision_id uuid references public.vps_whitelist_revisions(id) on delete set null,
  active_revision_no bigint check (active_revision_no is null or active_revision_no >= 0),
  active_generation bigint check (active_generation is null or active_generation >= 0),
  active_pack_sha256 text check (
    active_pack_sha256 is null
    or active_pack_sha256 ~ '^[0-9a-f]{64}$'
  ),
  generated_at timestamptz,
  last_control_pull_at timestamptz,
  last_strategy_cycle_at timestamptz,
  last_quote_snapshot_at timestamptz,
  last_account_snapshot_at timestamptz,
  last_eod_at timestamptz,
  last_backup_at timestamptz,
  provider_reads_used smallint check (provider_reads_used is null or provider_reads_used >= 0),
  provider_reads_cap smallint check (provider_reads_cap is null or provider_reads_cap between 1 and 50),
  state_summary text,
  sanitized_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint vps_runtime_snapshot_singleton check (id = true),
  constraint vps_runtime_snapshot_summary_safe check (public.vps_text_is_safe(state_summary)),
  constraint vps_runtime_snapshot_error_safe check (public.vps_text_is_safe(sanitized_error)),
  constraint vps_runtime_snapshot_summary_length check (state_summary is null or char_length(state_summary) <= 500),
  constraint vps_runtime_snapshot_error_length check (sanitized_error is null or char_length(sanitized_error) <= 500)
);

-- Current per-symbol strategy state. `status_key` is intentionally an
-- implementation-supplied key rather than a hard-coded investment conclusion.
create table if not exists public.vps_symbol_states (
  symbol text primary key,
  display_name text,
  status_key text not null default 'unknown' check (char_length(status_key) between 1 and 64),
  status_reason text,
  source_generated_at timestamptz not null,
  data_fresh_at timestamptz,
  active_revision_no bigint check (active_revision_no is null or active_revision_no >= 0),
  is_in_active_whitelist boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint vps_symbol_states_symbol_format check (symbol ~ '^[0-9]{6}\.(SH|SZ)$'),
  constraint vps_symbol_states_name_length check (display_name is null or char_length(display_name) <= 80),
  constraint vps_symbol_states_reason_safe check (public.vps_text_is_safe(status_reason)),
  constraint vps_symbol_states_reason_length check (status_reason is null or char_length(status_reason) <= 500)
);

-- Current simulated position summary only. Cost, value, P&L, broker IDs and
-- order/fill detail are deliberately absent from the Stage-1 public contract.
create table if not exists public.vps_sim_positions (
  symbol text primary key,
  display_name text,
  held_quantity bigint not null check (held_quantity >= 0),
  available_quantity bigint not null check (available_quantity >= 0 and available_quantity <= held_quantity),
  position_state text not null default 'active' check (char_length(position_state) between 1 and 64),
  source_generated_at timestamptz not null,
  active_revision_no bigint check (active_revision_no is null or active_revision_no >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint vps_sim_positions_symbol_format check (symbol ~ '^[0-9]{6}\.(SH|SZ)$'),
  constraint vps_sim_positions_name_length check (display_name is null or char_length(display_name) <= 80)
);

-- Recent, bounded, sanitised runtime events. The raw VPS log remains solely on
-- the VPS; no arbitrary payload column is available here by design.
create table if not exists public.vps_runtime_events (
  id uuid primary key default gen_random_uuid(),
  occurred_at timestamptz not null,
  severity public.vps_event_severity not null default 'info',
  event_code text not null check (event_code ~ '^[a-z0-9_.-]{1,80}$'),
  message text not null,
  revision_no bigint check (revision_no is null or revision_no >= 0),
  generation bigint check (generation is null or generation >= 0),
  created_at timestamptz not null default now(),
  constraint vps_runtime_events_message_safe check (public.vps_text_is_safe(message)),
  constraint vps_runtime_events_message_length check (char_length(message) <= 500)
);

-- Every control-plane acknowledgement is immutable and allows the dashboard to
-- distinguish “submitted” from “actually activated on the VPS”.
create table if not exists public.vps_sync_acks (
  id uuid primary key default gen_random_uuid(),
  revision_id uuid not null references public.vps_whitelist_revisions(id) on delete cascade,
  revision_no bigint not null check (revision_no >= 0),
  sync_stage public.vps_sync_stage not null,
  generation bigint check (generation is null or generation >= 0),
  pack_sha256 text check (pack_sha256 is null or pack_sha256 ~ '^[0-9a-f]{64}$'),
  reported_at timestamptz not null,
  message text,
  created_at timestamptz not null default now(),
  constraint vps_sync_acks_message_safe check (public.vps_text_is_safe(message)),
  constraint vps_sync_acks_message_length check (message is null or char_length(message) <= 500)
);

create index if not exists vps_whitelist_revisions_status_created_idx
  on public.vps_whitelist_revisions (status, created_at desc);
create index if not exists vps_whitelist_revision_items_symbol_idx
  on public.vps_whitelist_revision_items (symbol);
create index if not exists vps_symbol_states_active_idx
  on public.vps_symbol_states (is_in_active_whitelist, symbol);
create index if not exists vps_sim_positions_updated_idx
  on public.vps_sim_positions (updated_at desc);
create index if not exists vps_runtime_events_occurred_idx
  on public.vps_runtime_events (occurred_at desc);
create index if not exists vps_sync_acks_revision_reported_idx
  on public.vps_sync_acks (revision_id, reported_at desc);

create or replace function public.vps_set_updated_at()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists vps_control_settings_updated_at on public.vps_control_settings;
create trigger vps_control_settings_updated_at
  before update on public.vps_control_settings
  for each row execute function public.vps_set_updated_at();

drop trigger if exists vps_whitelist_revisions_updated_at on public.vps_whitelist_revisions;
create trigger vps_whitelist_revisions_updated_at
  before update on public.vps_whitelist_revisions
  for each row execute function public.vps_set_updated_at();

drop trigger if exists vps_runtime_snapshot_updated_at on public.vps_runtime_snapshot;
create trigger vps_runtime_snapshot_updated_at
  before update on public.vps_runtime_snapshot
  for each row execute function public.vps_set_updated_at();

drop trigger if exists vps_symbol_states_updated_at on public.vps_symbol_states;
create trigger vps_symbol_states_updated_at
  before update on public.vps_symbol_states
  for each row execute function public.vps_set_updated_at();

drop trigger if exists vps_sim_positions_updated_at on public.vps_sim_positions;
create trigger vps_sim_positions_updated_at
  before update on public.vps_sim_positions
  for each row execute function public.vps_set_updated_at();

-- Admin membership is not readable through the public API. All dashboard data
-- reads require this helper, and browser writes are only possible via the
-- narrow revision-submission RPC below.
create or replace function public.vps_is_admin()
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select exists (
    select 1
    from public.vps_admins as a
    where a.user_id = auth.uid()
      and a.active = true
  );
$$;

alter table public.vps_admins enable row level security;
alter table public.vps_control_settings enable row level security;
alter table public.vps_whitelist_revisions enable row level security;
alter table public.vps_whitelist_revision_items enable row level security;
alter table public.vps_runtime_snapshot enable row level security;
alter table public.vps_symbol_states enable row level security;
alter table public.vps_sim_positions enable row level security;
alter table public.vps_runtime_events enable row level security;
alter table public.vps_sync_acks enable row level security;

-- Rerunnable, select-only RLS policies for the browser's authenticated admin.
drop policy if exists vps_control_settings_admin_select on public.vps_control_settings;
create policy vps_control_settings_admin_select
  on public.vps_control_settings for select to authenticated
  using (public.vps_is_admin());

drop policy if exists vps_whitelist_revisions_admin_select on public.vps_whitelist_revisions;
create policy vps_whitelist_revisions_admin_select
  on public.vps_whitelist_revisions for select to authenticated
  using (public.vps_is_admin());

drop policy if exists vps_whitelist_revision_items_admin_select on public.vps_whitelist_revision_items;
create policy vps_whitelist_revision_items_admin_select
  on public.vps_whitelist_revision_items for select to authenticated
  using (public.vps_is_admin());

drop policy if exists vps_runtime_snapshot_admin_select on public.vps_runtime_snapshot;
create policy vps_runtime_snapshot_admin_select
  on public.vps_runtime_snapshot for select to authenticated
  using (public.vps_is_admin());

drop policy if exists vps_symbol_states_admin_select on public.vps_symbol_states;
create policy vps_symbol_states_admin_select
  on public.vps_symbol_states for select to authenticated
  using (public.vps_is_admin());

drop policy if exists vps_sim_positions_admin_select on public.vps_sim_positions;
create policy vps_sim_positions_admin_select
  on public.vps_sim_positions for select to authenticated
  using (public.vps_is_admin());

drop policy if exists vps_runtime_events_admin_select on public.vps_runtime_events;
create policy vps_runtime_events_admin_select
  on public.vps_runtime_events for select to authenticated
  using (public.vps_is_admin());

drop policy if exists vps_sync_acks_admin_select on public.vps_sync_acks;
create policy vps_sync_acks_admin_select
  on public.vps_sync_acks for select to authenticated
  using (public.vps_is_admin());

-- Client-side only entry point for immutable desired-whitelist snapshots.
-- It does not activate the whitelist: VPS acknowledgement is required later.
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
  configured_max_symbols integer;
  next_revision_no bigint;
  created_revision_id uuid;
begin
  if not public.vps_is_admin() then
    raise exception 'VPS dashboard administrator access is required'
      using errcode = '42501';
  end if;

  if p_request_note is not null and char_length(p_request_note) > 500 then
    raise exception 'Whitelist request note exceeds 500 characters';
  end if;

  if not public.vps_text_is_safe(p_request_note) then
    raise exception 'Whitelist request note contains a protected term';
  end if;

  if p_symbols is null or cardinality(p_symbols) = 0 then
    raise exception 'At least one canonical A-share symbol is required';
  end if;

  select max_active_symbols
    into configured_max_symbols
    from public.vps_control_settings
    where id = true;

  if configured_max_symbols is null then
    raise exception 'VPS control settings are not initialized';
  end if;

  foreach raw_symbol in array p_symbols loop
    canonical_symbol := upper(btrim(raw_symbol));

    if canonical_symbol is null
      or canonical_symbol !~ '^[0-9]{6}\.(SH|SZ)$' then
      raise exception 'Unsupported canonical A-share symbol: %', coalesce(raw_symbol, '<null>');
    end if;

    if canonical_symbol = any(normalized_symbols) then
      raise exception 'Duplicate canonical A-share symbol: %', canonical_symbol;
    end if;

    normalized_symbols := array_append(normalized_symbols, canonical_symbol);
  end loop;

  if cardinality(normalized_symbols) > configured_max_symbols then
    raise exception 'Whitelist exceeds configured maximum of % symbols', configured_max_symbols;
  end if;

  perform pg_advisory_xact_lock(hashtext('public.vps_whitelist_revisions'));

  select coalesce(max(revision_no), 0) + 1
    into next_revision_no
    from public.vps_whitelist_revisions;

  insert into public.vps_whitelist_revisions (
    revision_no,
    status,
    requested_by,
    request_note,
    desired_symbol_count
  )
  values (
    next_revision_no,
    'submitted',
    auth.uid(),
    nullif(btrim(coalesce(p_request_note, '')), ''),
    cardinality(normalized_symbols)
  )
  returning id into created_revision_id;

  insert into public.vps_whitelist_revision_items (revision_id, symbol, sort_order)
  select created_revision_id, item.symbol, item.ordinality - 1
  from unnest(normalized_symbols) with ordinality as item(symbol, ordinality);

  -- Only not-yet-active desired versions are superseded. The active VPS
  -- revision remains historical evidence until a verified activation arrives.
  update public.vps_whitelist_revisions
     set status = 'superseded',
         superseded_by = created_revision_id
   where id <> created_revision_id
     and status in ('submitted', 'sync_pending', 'preparing');

  return query
  select created_revision_id, next_revision_no, 'submitted'::public.vps_whitelist_status;
end;
$$;

insert into public.vps_control_settings (id)
values (true)
on conflict (id) do nothing;

insert into public.vps_runtime_snapshot (
  id,
  schema_version,
  adapter_id,
  mode,
  health_status,
  state_summary
)
values (
  true,
  1,
  'unknown',
  'DRY_RUN',
  'unknown',
  'Stage 1 control-plane schema initialized; waiting for verified VPS publisher.'
)
on conflict (id) do nothing;

-- The browser gets read-only table access only after RLS admin checks. It gets
-- no direct INSERT/UPDATE/DELETE privileges. The service_role grant is for the
-- future server-side/Edge Function publisher and is never placed in Pages.
revoke all on table public.vps_admins from anon, authenticated;
revoke all on table public.vps_control_settings from anon, authenticated;
revoke all on table public.vps_whitelist_revisions from anon, authenticated;
revoke all on table public.vps_whitelist_revision_items from anon, authenticated;
revoke all on table public.vps_runtime_snapshot from anon, authenticated;
revoke all on table public.vps_symbol_states from anon, authenticated;
revoke all on table public.vps_sim_positions from anon, authenticated;
revoke all on table public.vps_runtime_events from anon, authenticated;
revoke all on table public.vps_sync_acks from anon, authenticated;

grant select on table
  public.vps_control_settings,
  public.vps_whitelist_revisions,
  public.vps_whitelist_revision_items,
  public.vps_runtime_snapshot,
  public.vps_symbol_states,
  public.vps_sim_positions,
  public.vps_runtime_events,
  public.vps_sync_acks
  to authenticated;

grant select, insert, update, delete on table
  public.vps_admins,
  public.vps_control_settings,
  public.vps_whitelist_revisions,
  public.vps_whitelist_revision_items,
  public.vps_runtime_snapshot,
  public.vps_symbol_states,
  public.vps_sim_positions,
  public.vps_runtime_events,
  public.vps_sync_acks
  to service_role;

revoke all on function public.vps_is_admin() from public;
grant execute on function public.vps_is_admin() to authenticated;

revoke all on function public.vps_submit_whitelist_revision(text[], text) from public;
grant execute on function public.vps_submit_whitelist_revision(text[], text) to authenticated;

commit;
