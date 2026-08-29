-- Stock dashboard private identity/portfolio projection — Stage 3 forward migration.
--
-- STATUS: REVIEWED LOCAL CANDIDATE / MANUAL HOSTED SQL EXECUTION ONLY.
-- Apply only after the user has run the read-only Stage 3 preflight and has
-- personally pasted this exact file into the Supabase SQL Editor. Never run it
-- from the browser, GitHub Actions, VPS, or an unattended agent.
-- It assumes Stage 1, Stage 2, protocol v2, and the v2 ACL closure already exist.
-- This migration does not create Auth users, grant memberships, import positions,
-- enable ARMED mode, call a provider, or modify the VPS.
--
-- The migration creates:
--   * private username mapping with no browser table access;
--   * an explicit private portfolio scope without user membership seeding;
--   * a current-only private position projection with database-computed values;
--   * narrow read/write RPCs and RLS policies;
--   * a concurrency-checked three-argument whitelist submission RPC.

begin;

create table if not exists public.app_usernames (
  user_id uuid primary key references auth.users(id) on delete cascade,
  username_norm text not null unique,
  username_display text not null,
  status text not null default 'active'
    check (status in ('active', 'disabled')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint app_usernames_norm_format check (
    username_norm = lower(username_norm)
    and username_norm ~ '^[a-z0-9]([a-z0-9._-]{1,30})[a-z0-9]$'
  ),
  constraint app_usernames_display_length check (
    char_length(username_display) between 1 and 80
  ),
  constraint app_usernames_display_safe check (
    public.vps_text_is_safe(username_display)
  )
);

alter table public.app_usernames enable row level security;
revoke all on table public.app_usernames from PUBLIC, anon, authenticated, service_role;

create or replace function public.app_resolve_username(p_username text)
returns uuid
language plpgsql
stable
security definer
set search_path = pg_catalog, public
as $$
declare
  candidate text;
  resolved_user_id uuid;
begin
  if p_username is null then
    return null;
  end if;
  candidate := lower(btrim(p_username));
  if candidate !~ '^[a-z0-9]([a-z0-9._-]{1,30})[a-z0-9]$' then
    return null;
  end if;
  select user_id
    into resolved_user_id
    from public.app_usernames
   where username_norm = candidate
     and status = 'active';
  return resolved_user_id;
end;
$$;

revoke all on function public.app_resolve_username(text) from PUBLIC, anon, authenticated, service_role;
grant execute on function public.app_resolve_username(text) to service_role;

create or replace function public.app_get_current_username()
returns text
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select username_display
    from public.app_usernames
   where user_id = auth.uid()
     and status = 'active';
$$;

revoke all on function public.app_get_current_username() from PUBLIC, anon, service_role;
grant execute on function public.app_get_current_username() to authenticated;

drop trigger if exists app_usernames_updated_at on public.app_usernames;
create trigger app_usernames_updated_at
before update on public.app_usernames
for each row execute function public.vps_set_updated_at();

create table if not exists public.vps_private_scopes (
  scope_key text primary key,
  target_device_id text not null references public.vps_sync_devices(device_id) on delete restrict,
  display_label text not null default '模拟盘',
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint vps_private_scopes_key_format check (
    scope_key ~ '^[a-z0-9]([a-z0-9._-]{1,30})[a-z0-9]$'
  ),
  constraint vps_private_scopes_label_length check (
    char_length(display_label) between 1 and 80
  ),
  constraint vps_private_scopes_label_safe check (
    public.vps_text_is_safe(display_label)
  )
);

create table if not exists public.vps_private_scope_members (
  scope_key text not null references public.vps_private_scopes(scope_key) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  access_role text not null default 'viewer'
    check (access_role = 'viewer'),
  active boolean not null default true,
  granted_at timestamptz not null default now(),
  granted_by uuid references auth.users(id) on delete set null,
  note text,
  primary key (scope_key, user_id),
  constraint vps_private_scope_members_note_length check (
    note is null or char_length(note) <= 500
  ),
  constraint vps_private_scope_members_note_safe check (
    public.vps_text_is_safe(note)
  )
);

create index if not exists vps_private_scope_members_user_idx
  on public.vps_private_scope_members (user_id, scope_key)
  where active = true;

alter table public.vps_private_scopes enable row level security;
alter table public.vps_private_scope_members enable row level security;
revoke all on table public.vps_private_scopes from PUBLIC, anon, authenticated, service_role;
revoke all on table public.vps_private_scope_members from PUBLIC, anon, authenticated, service_role;

drop trigger if exists vps_private_scopes_updated_at on public.vps_private_scopes;
create trigger vps_private_scopes_updated_at
before update on public.vps_private_scopes
for each row execute function public.vps_set_updated_at();

create table if not exists public.vps_private_projection_state (
  scope_key text primary key references public.vps_private_scopes(scope_key) on delete cascade,
  schema_version smallint not null default 1 check (schema_version = 1),
  mode text not null default 'DRY_RUN' check (mode = 'DRY_RUN'),
  health_status text not null default 'unknown'
    check (health_status in ('ok', 'degraded', 'failed', 'unknown')),
  projection_sequence bigint not null default 0 check (projection_sequence >= 0),
  projection_digest text check (
    projection_digest is null or projection_digest ~ '^[0-9a-f]{64}$'
  ),
  active_revision_no bigint check (active_revision_no is null or active_revision_no >= 1),
  active_generation bigint check (active_generation is null or active_generation >= 1),
  active_pack_sha256 text check (
    active_pack_sha256 is null or active_pack_sha256 ~ '^[0-9a-f]{64}$'
  ),
  active_control_payload_sha256 text check (
    active_control_payload_sha256 is null
    or active_control_payload_sha256 ~ '^[0-9a-f]{64}$'
  ),
  active_control_raw_contract_sha256 text check (
    active_control_raw_contract_sha256 is null
    or active_control_raw_contract_sha256 ~ '^[0-9a-f]{64}$'
  ),
  active_members_sha256 text check (
    active_members_sha256 is null or active_members_sha256 ~ '^[0-9a-f]{64}$'
  ),
  active_snapshot_id text check (
    active_snapshot_id is null
    or active_snapshot_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
  ),
  active_snapshot_sha256 text check (
    active_snapshot_sha256 is null or active_snapshot_sha256 ~ '^[0-9a-f]{64}$'
  ),
  source_market_snapshot_id text check (
    source_market_snapshot_id is null
    or source_market_snapshot_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
  ),
  source_market_snapshot_sha256 text check (
    source_market_snapshot_sha256 is null
    or source_market_snapshot_sha256 ~ '^[0-9a-f]{64}$'
  ),
  account_as_of timestamptz,
  quote_as_of timestamptz,
  source_generated_at timestamptz,
  position_count smallint not null default 0 check (position_count between 0 and 50),
  sanitized_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint vps_private_projection_snapshot_pair check (
    (source_market_snapshot_id is null and source_market_snapshot_sha256 is null)
    or (source_market_snapshot_id is not null and source_market_snapshot_sha256 is not null)
  ),
  constraint vps_private_projection_active_evidence_pair check (
    (active_revision_no is null and active_generation is null)
    or (active_revision_no is not null and active_generation is not null)
  ),
  constraint vps_private_projection_error_length check (
    sanitized_error is null or char_length(sanitized_error) <= 500
  ),
  constraint vps_private_projection_error_safe check (
    public.vps_text_is_safe(sanitized_error)
  )
);

create table if not exists public.vps_private_sim_positions (
  scope_key text not null references public.vps_private_scopes(scope_key) on delete cascade,
  symbol text not null,
  display_name text,
  held_quantity bigint not null check (held_quantity >= 0),
  available_quantity bigint not null check (
    available_quantity >= 0 and available_quantity <= held_quantity
  ),
  average_cost_per_share numeric(24,6),
  current_unadjusted_price numeric(24,6),
  price_as_of timestamptz,
  data_status text not null
    check (data_status in ('complete', 'stale_price', 'missing_price', 'missing_cost', 'unavailable')),
  quote_source_kind text not null
    check (quote_source_kind in ('hithink_batch_snapshot', 'not_available')),
  position_state text not null default 'held'
    check (position_state ~ '^[a-z0-9_.-]{1,64}$'),
  projection_sequence bigint not null check (projection_sequence >= 1),
  active_revision_no bigint check (active_revision_no is null or active_revision_no >= 1),
  source_generated_at timestamptz not null,
  cost_basis numeric(30,6) generated always as (
    case
      when average_cost_per_share is null then null
      else held_quantity::numeric * average_cost_per_share
    end
  ) stored,
  market_value numeric(30,6) generated always as (
    case
      when current_unadjusted_price is null then null
      else held_quantity::numeric * current_unadjusted_price
    end
  ) stored,
  unrealized_pnl numeric(30,6) generated always as (
    case
      when average_cost_per_share is null or current_unadjusted_price is null then null
      else held_quantity::numeric * (current_unadjusted_price - average_cost_per_share)
    end
  ) stored,
  unrealized_pnl_pct numeric(18,8) generated always as (
    case
      when average_cost_per_share is null
        or average_cost_per_share <= 0
        or current_unadjusted_price is null
        or held_quantity = 0 then null
      else ((current_unadjusted_price - average_cost_per_share)
        / nullif(average_cost_per_share, 0)) * 100
    end
  ) stored,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (scope_key, symbol),
  constraint vps_private_positions_symbol_format check (
    symbol ~ '^[0-9]{6}\.(SH|SZ)$'
  ),
  constraint vps_private_positions_name_length check (
    display_name is null or char_length(display_name) <= 80
  ),
  constraint vps_private_positions_name_safe check (
    public.vps_text_is_safe(display_name)
  ),
  constraint vps_private_positions_cost_range check (
    average_cost_per_share is null
    or average_cost_per_share between -1000000000 and 1000000000
  ),
  constraint vps_private_positions_price_range check (
    current_unadjusted_price is null
    or current_unadjusted_price between 0.000001 and 1000000000
  ),
  constraint vps_private_positions_price_timestamp_pair check (
    (current_unadjusted_price is null and price_as_of is null)
    or (current_unadjusted_price is not null and price_as_of is not null)
  )
);

alter table public.vps_private_projection_state enable row level security;
alter table public.vps_private_sim_positions enable row level security;
revoke all on table public.vps_private_projection_state from PUBLIC, anon, authenticated, service_role;
revoke all on table public.vps_private_sim_positions from PUBLIC, anon, authenticated, service_role;

create index if not exists vps_private_positions_market_value_idx
  on public.vps_private_sim_positions (scope_key, market_value desc nulls last, symbol);
create index if not exists vps_private_positions_updated_idx
  on public.vps_private_sim_positions (scope_key, updated_at desc);

drop trigger if exists vps_private_projection_state_updated_at on public.vps_private_projection_state;
create trigger vps_private_projection_state_updated_at
before update on public.vps_private_projection_state
for each row execute function public.vps_set_updated_at();

drop trigger if exists vps_private_sim_positions_updated_at on public.vps_private_sim_positions;
create trigger vps_private_sim_positions_updated_at
before update on public.vps_private_sim_positions
for each row execute function public.vps_set_updated_at();

create or replace function public.vps_private_can_view_scope(p_scope_key text)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select p_scope_key is not null
    and auth.uid() is not null
    and exists (
      select 1
        from public.vps_private_scope_members as member
        join public.vps_private_scopes as scope
          on scope.scope_key = member.scope_key
       where member.scope_key = p_scope_key
         and member.user_id = auth.uid()
         and member.active = true
         and scope.active = true
    );
$$;

revoke all on function public.vps_private_can_view_scope(text) from PUBLIC, anon, service_role;
grant execute on function public.vps_private_can_view_scope(text) to authenticated;

drop policy if exists vps_private_projection_state_member_select
  on public.vps_private_projection_state;
create policy vps_private_projection_state_member_select
  on public.vps_private_projection_state
  for select to authenticated
  using (public.vps_private_can_view_scope(scope_key));

drop policy if exists vps_private_sim_positions_member_select
  on public.vps_private_sim_positions;
create policy vps_private_sim_positions_member_select
  on public.vps_private_sim_positions
  for select to authenticated
  using (public.vps_private_can_view_scope(scope_key));

-- Narrow browser read. It intentionally omits user_id, target device,
-- projection digest, source hash, account identifiers, order data and raw data.
create or replace function public.vps_private_get_portfolio()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'scope_key', scope.scope_key,
        'display_label', scope.display_label,
        'mode', state.mode,
        'health_status', state.health_status,
        'projection_sequence', state.projection_sequence,
        'active_revision_no', state.active_revision_no,
        'active_generation', state.active_generation,
        'source_market_snapshot_id', state.source_market_snapshot_id,
        'account_as_of', state.account_as_of,
        'quote_as_of', state.quote_as_of,
        'source_generated_at', state.source_generated_at,
        'position_count', state.position_count,
        'sanitized_error', state.sanitized_error,
        'positions', coalesce(
          (
            select jsonb_agg(
              jsonb_build_object(
                'symbol', pos.symbol,
                'display_name', pos.display_name,
                'held_quantity', pos.held_quantity,
                'available_quantity', pos.available_quantity,
                'average_cost_per_share', pos.average_cost_per_share,
                'current_unadjusted_price', pos.current_unadjusted_price,
                'price_as_of', pos.price_as_of,
                'data_status', pos.data_status,
                'quote_source_kind', pos.quote_source_kind,
                'position_state', pos.position_state,
                'projection_sequence', pos.projection_sequence,
                'active_revision_no', pos.active_revision_no,
                'source_generated_at', pos.source_generated_at,
                'cost_basis', pos.cost_basis,
                'market_value', pos.market_value,
                'unrealized_pnl', pos.unrealized_pnl,
                'unrealized_pnl_pct', pos.unrealized_pnl_pct
              )
              order by pos.market_value desc nulls last, pos.symbol asc
            )
              from public.vps_private_sim_positions as pos
             where pos.scope_key = scope.scope_key
          ),
          '[]'::jsonb
        )
      )
      order by scope.scope_key
    ),
    '[]'::jsonb
  )
    from public.vps_private_scopes as scope
    join public.vps_private_projection_state as state
      on state.scope_key = scope.scope_key
   where scope.active = true
     and exists (
       select 1
         from public.vps_private_scope_members as member
        where member.scope_key = scope.scope_key
          and member.user_id = auth.uid()
          and member.active = true
     );
$$;

revoke all on function public.vps_private_get_portfolio() from PUBLIC, anon, service_role;
grant execute on function public.vps_private_get_portfolio() to authenticated;

-- Authenticated private-scope read for the compact Part 0 runtime display.
-- It returns only sanitized status/timestamps and the latest four bounded
-- events; it omits hashes, device ids, account ids and raw payloads.
create or replace function public.vps_private_get_runtime_display()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select jsonb_build_object(
    'runtime', jsonb_build_object(
      'schema_version', snapshot.schema_version,
      'adapter_id', snapshot.adapter_id,
      'mode', snapshot.mode,
      'health_status', snapshot.health_status,
      'active_revision_no', snapshot.active_revision_no,
      'active_generation', snapshot.active_generation,
      'generated_at', snapshot.generated_at,
      'last_control_pull_at', snapshot.last_control_pull_at,
      'last_strategy_cycle_at', snapshot.last_strategy_cycle_at,
      'last_quote_snapshot_at', snapshot.last_quote_snapshot_at,
      'last_account_snapshot_at', snapshot.last_account_snapshot_at,
      'last_eod_at', snapshot.last_eod_at,
      'last_backup_at', snapshot.last_backup_at,
      'provider_reads_used', snapshot.provider_reads_used,
      'provider_reads_cap', snapshot.provider_reads_cap,
      'state_summary', snapshot.state_summary,
      'sanitized_error', snapshot.sanitized_error
    ),
    'events', coalesce(
      (
        select jsonb_agg(
          jsonb_build_object(
            'occurred_at', event.occurred_at,
            'severity', event.severity,
            'event_code', event.event_code,
            'message', event.message,
            'revision_no', event.revision_no,
            'generation', event.generation
          )
          order by event.occurred_at desc
        )
          from (
            select occurred_at, severity, event_code, message, revision_no, generation
              from public.vps_runtime_events
             order by occurred_at desc
             limit 4
          ) as event
      ),
      '[]'::jsonb
    )
  )
    from public.vps_runtime_snapshot as snapshot
   where snapshot.id = true
     and public.vps_private_can_view_scope('primary');
$$;

revoke all on function public.vps_private_get_runtime_display() from PUBLIC, anon, service_role;
grant execute on function public.vps_private_get_runtime_display() to authenticated;

-- Internal VPS writer. The existing Edge HMAC/request-receipt wrapper must call
-- this in the same transaction as vps_sync_ingest_report. The function computes
-- derived amounts in PostgreSQL and never accepts derived amounts from payload.
create or replace function public.vps_sync_replace_private_projection(
  p_device_id text,
  p_scope_key text,
  p_projection jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  scope_row public.vps_private_scopes%rowtype;
  existing_state public.vps_private_projection_state%rowtype;
  active_revision public.vps_whitelist_revisions%rowtype;
  item jsonb;

  symbol_value text;
  display_name_value text;
  position_state_value text;
  data_status_value text;
  quote_source_value text;
  seen_symbols text[] := array[]::text[];
  projection_digest_value text;
  projection_sequence_value bigint;
  active_revision_no_value bigint;
  active_generation_value bigint;
  held_quantity_value bigint;
  available_quantity_value bigint;
  average_cost_value numeric;
  current_price_value numeric;
  price_as_of_value timestamptz;
  projection_generated_at timestamptz;
  account_as_of_value timestamptz;
  quote_as_of_value timestamptz;
  health_status_value text;
  source_snapshot_id_value text;
  source_snapshot_sha_value text;
  active_pack_sha_value text;
  active_payload_sha_value text;
  active_raw_sha_value text;
  active_members_sha_value text;
  active_snapshot_id_value text;
  active_snapshot_sha_value text;
  sanitized_error_value text;
  position_count_value integer;
  item_position_count integer;
begin
  if p_device_id is null or p_device_id !~ '^[a-z0-9][a-z0-9._-]{2,79}$'
    or p_scope_key is null or p_scope_key !~ '^[a-z0-9]([a-z0-9._-]{1,30})[a-z0-9]$'
    or jsonb_typeof(p_projection) <> 'object' then
    raise exception 'Private projection request is invalid';
  end if;
  if not exists (
    select 1 from public.vps_sync_devices
     where device_id = p_device_id and enabled = true
  ) then
    raise exception 'VPS gateway device is disabled';
  end if;

  select * into scope_row
    from public.vps_private_scopes
   where scope_key = p_scope_key
     and active = true
   for update;
  if not found or scope_row.target_device_id <> p_device_id then
    raise exception 'Private projection scope is not bound to this device';
  end if;
  if p_projection ->> 'scope_key' <> p_scope_key then
    raise exception 'Private projection scope identity is invalid';
  end if;

  if exists (
    select 1
      from jsonb_object_keys(p_projection) as key(name)
     where key.name not in (
       'schema_version', 'scope_key', 'mode', 'health_status',
       'projection_sequence', 'generated_at', 'account_as_of', 'quote_as_of',
       'source_market_snapshot_id', 'source_market_snapshot_sha256',
       'active_revision_no', 'active_generation', 'active_pack_sha256',
       'active_control_payload_sha256', 'active_control_raw_contract_sha256',
       'active_members_sha256', 'active_snapshot_id', 'active_snapshot_sha256',
       'sanitized_error', 'positions'
     )
  ) then
    raise exception 'Private projection contains an unknown field';
  end if;
  if p_projection ->> 'schema_version' <> '1'
    or p_projection ->> 'mode' <> 'DRY_RUN'
    or jsonb_typeof(coalesce(p_projection -> 'positions', '[]'::jsonb)) <> 'array' then
    raise exception 'Private projection schema or mode is invalid';
  end if;

  begin
    projection_sequence_value := (p_projection ->> 'projection_sequence')::bigint;
    projection_generated_at := (p_projection ->> 'generated_at')::timestamptz;
    account_as_of_value := nullif(p_projection ->> 'account_as_of', '')::timestamptz;
    quote_as_of_value := nullif(p_projection ->> 'quote_as_of', '')::timestamptz;
    active_revision_no_value := nullif(p_projection ->> 'active_revision_no', '')::bigint;
    active_generation_value := nullif(p_projection ->> 'active_generation', '')::bigint;
  exception when others then
    raise exception 'Private projection numeric or timestamp field is invalid';
  end;
  if projection_sequence_value is null or projection_sequence_value < 1
    or projection_generated_at is null
    or (p_projection ->> 'generated_at') !~ '[zZ]|[+-][0-9]{2}:[0-9]{2}$' then
    raise exception 'Private projection sequence or generated time is invalid';
  end if;
  if account_as_of_value is not null and (p_projection ->> 'account_as_of') !~ '[zZ]|[+-][0-9]{2}:[0-9]{2}$' then
    raise exception 'Private account timestamp is invalid';
  end if;
  if quote_as_of_value is not null and (p_projection ->> 'quote_as_of') !~ '[zZ]|[+-][0-9]{2}:[0-9]{2}$' then
    raise exception 'Private quote timestamp is invalid';
  end if;

  health_status_value := p_projection ->> 'health_status';
  if health_status_value not in ('ok', 'degraded', 'failed', 'unknown') then
    raise exception 'Private projection health status is invalid';
  end if;
  sanitized_error_value := nullif(p_projection ->> 'sanitized_error', '');
  if sanitized_error_value is not null
    and (char_length(sanitized_error_value) > 500
      or not public.vps_text_is_safe(sanitized_error_value)) then
    raise exception 'Private projection error is invalid';
  end if;

  source_snapshot_id_value := nullif(p_projection ->> 'source_market_snapshot_id', '');
  source_snapshot_sha_value := nullif(p_projection ->> 'source_market_snapshot_sha256', '');
  if (source_snapshot_id_value is null) <> (source_snapshot_sha_value is null)
    or (source_snapshot_id_value is not null
      and (source_snapshot_id_value !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
        or source_snapshot_sha_value !~ '^[0-9a-f]{64}$')) then
    raise exception 'Private market snapshot evidence is invalid';
  end if;

  active_pack_sha_value := nullif(p_projection ->> 'active_pack_sha256', '');
  active_payload_sha_value := nullif(p_projection ->> 'active_control_payload_sha256', '');
  active_raw_sha_value := nullif(p_projection ->> 'active_control_raw_contract_sha256', '');
  active_members_sha_value := nullif(p_projection ->> 'active_members_sha256', '');
  active_snapshot_id_value := nullif(p_projection ->> 'active_snapshot_id', '');
  active_snapshot_sha_value := nullif(p_projection ->> 'active_snapshot_sha256', '');
  if active_revision_no_value is null then
    if active_generation_value is not null
      or active_pack_sha_value is not null
      or active_payload_sha_value is not null
      or active_raw_sha_value is not null
      or active_members_sha_value is not null
      or active_snapshot_id_value is not null
      or active_snapshot_sha_value is not null then
      raise exception 'Private projection has orphan active evidence';
    end if;
  else
    if active_generation_value is null or active_generation_value < 1
      or active_pack_sha_value !~ '^[0-9a-f]{64}$'
      or active_payload_sha_value !~ '^[0-9a-f]{64}$'
      or active_raw_sha_value !~ '^[0-9a-f]{64}$'
      or active_members_sha_value !~ '^[0-9a-f]{64}$'
      or active_snapshot_id_value !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
      or active_snapshot_sha_value !~ '^[0-9a-f]{64}$' then
      raise exception 'Private projection active evidence is incomplete';
    end if;
    select * into active_revision
      from public.vps_whitelist_revisions
     where revision_no = active_revision_no_value
       and target_device_id = p_device_id
     for update;
    if not found or active_revision.status <> 'active'
      or active_revision.contract_protocol_version <> 2
      or active_revision.contract_adapter_id <> 'v31f-15m-miaoxiang-sim-adapter'
      or active_revision.contract_mode <> 'DRY_RUN'
      or active_revision.active_generation is distinct from active_generation_value
      or active_revision.active_pack_sha256 is distinct from active_pack_sha_value
      or active_revision.control_payload_sha256 is distinct from active_payload_sha_value
      or active_revision.control_raw_contract_sha256 is distinct from active_raw_sha_value
      or active_revision.members_sha256 is distinct from active_members_sha_value
      or active_revision.required_snapshot_id is distinct from active_snapshot_id_value
      or active_revision.required_snapshot_sha256 is distinct from active_snapshot_sha_value
      or source_snapshot_id_value is distinct from active_revision.required_snapshot_id
      or source_snapshot_sha_value is distinct from active_revision.required_snapshot_sha256 then
      raise exception 'Private projection active evidence does not match control plane';
    end if;
  end if;

  item_position_count := jsonb_array_length(p_projection -> 'positions');
  if item_position_count > 50 or (item_position_count > 0 and account_as_of_value is null) then
    raise exception 'Private projection position count or account time is invalid';
  end if;

  projection_digest_value := encode(extensions.digest(p_projection::text, 'sha256'), 'hex');
  select * into existing_state
    from public.vps_private_projection_state
   where scope_key = p_scope_key
   for update;
  if found then
    if projection_sequence_value < existing_state.projection_sequence then
      raise exception 'Private projection sequence regressed';
    end if;
    if projection_sequence_value = existing_state.projection_sequence then
      if existing_state.projection_digest = projection_digest_value then
        return jsonb_build_object(
          'accepted', true,
          'replayed', true,
          'projection_sequence', projection_sequence_value,
          'position_count', existing_state.position_count
        );
      end if;
      raise exception 'Private projection sequence conflicts with previous content';
    end if;
  end if;

  delete from public.vps_private_sim_positions
   where scope_key = p_scope_key;

  for item in select value from jsonb_array_elements(p_projection -> 'positions') loop
    if jsonb_typeof(item) <> 'object' then
      raise exception 'Private position row is invalid';
    end if;
    if exists (
      select 1
        from jsonb_object_keys(item) as key(name)
       where key.name not in (
         'symbol', 'display_name', 'held_quantity', 'available_quantity',
         'average_cost_per_share', 'current_unadjusted_price', 'price_as_of',
         'data_status', 'quote_source_kind', 'position_state'
       )
    ) then
      raise exception 'Private position contains an unknown field';
    end if;
    symbol_value := upper(btrim(item ->> 'symbol'));
    if symbol_value is null or symbol_value !~ '^[0-9]{6}\.(SH|SZ)$'
      or symbol_value = any(seen_symbols) then
      raise exception 'Private position symbol is invalid or duplicated';
    end if;
    seen_symbols := array_append(seen_symbols, symbol_value);
    begin
      held_quantity_value := (item ->> 'held_quantity')::bigint;
      available_quantity_value := (item ->> 'available_quantity')::bigint;
      average_cost_value := nullif(item ->> 'average_cost_per_share', '')::numeric;
      current_price_value := nullif(item ->> 'current_unadjusted_price', '')::numeric;
      price_as_of_value := nullif(item ->> 'price_as_of', '')::timestamptz;
    exception when others then
      raise exception 'Private position numeric or timestamp field is invalid';
    end;
    if held_quantity_value is null or held_quantity_value < 0
      or available_quantity_value is null
      or available_quantity_value < 0
      or available_quantity_value > held_quantity_value
      or (average_cost_value is not null
        and average_cost_value not between -1000000000 and 1000000000)
      or (current_price_value is not null
        and current_price_value not between 0.000001 and 1000000000) then
      raise exception 'Private position quantity or price is invalid';
    end if;
    if current_price_value is null then
      if price_as_of_value is not null
        or item ->> 'data_status' not in ('missing_price', 'unavailable')
        or coalesce(item ->> 'quote_source_kind', 'not_available') <> 'not_available' then
        raise exception 'Private position missing-price status is invalid';
      end if;
    else
      if price_as_of_value is null
        or (item ->> 'price_as_of') !~ '[zZ]|[+-][0-9]{2}:[0-9]{2}$'
        or item ->> 'data_status' in ('missing_price', 'unavailable')
        or coalesce(item ->> 'quote_source_kind', '') <> 'hithink_batch_snapshot' then
        raise exception 'Private position price status is invalid';
      end if;
    end if;
    if held_quantity_value > 0 and average_cost_value is null
      and item ->> 'data_status' not in ('missing_cost', 'unavailable') then
      raise exception 'Private position missing-cost status is invalid';
    end if;
    if average_cost_value is not null and current_price_value is not null
      and item ->> 'data_status' not in ('complete', 'stale_price') then
      raise exception 'Private position complete status is invalid';
    end if;
    data_status_value := item ->> 'data_status';
    if data_status_value not in ('complete', 'stale_price', 'missing_price', 'missing_cost', 'unavailable') then
      raise exception 'Private position data status is invalid';
    end if;
    quote_source_value := coalesce(item ->> 'quote_source_kind', 'not_available');
    if quote_source_value not in ('hithink_batch_snapshot', 'not_available') then
      raise exception 'Private position quote source is invalid';
    end if;
    position_state_value := coalesce(nullif(item ->> 'position_state', ''), 'held');
    if position_state_value !~ '^[a-z0-9_.-]{1,64}$' then
      raise exception 'Private position state is invalid';
    end if;
    display_name_value := nullif(item ->> 'display_name', '');
    if display_name_value is not null
      and (char_length(display_name_value) > 80
        or not public.vps_text_is_safe(display_name_value)) then
      raise exception 'Private position display name is invalid';
    end if;

    insert into public.vps_private_sim_positions (
      scope_key, symbol, display_name, held_quantity, available_quantity,
      average_cost_per_share, current_unadjusted_price, price_as_of,
      data_status, quote_source_kind, position_state, projection_sequence,
      active_revision_no, source_generated_at
    ) values (
      p_scope_key, symbol_value, display_name_value, held_quantity_value,
      available_quantity_value, average_cost_value, current_price_value,
      price_as_of_value, data_status_value, quote_source_value,
      position_state_value, projection_sequence_value, active_revision_no_value,
      projection_generated_at
    );
  end loop;

  position_count_value := cardinality(seen_symbols);
  insert into public.vps_private_projection_state (
    scope_key, schema_version, mode, health_status, projection_sequence,
    projection_digest, active_revision_no, active_generation, active_pack_sha256,
    active_control_payload_sha256, active_control_raw_contract_sha256,
    active_members_sha256, active_snapshot_id, active_snapshot_sha256,
    source_market_snapshot_id, source_market_snapshot_sha256, account_as_of,
    quote_as_of, source_generated_at, position_count, sanitized_error
  ) values (
    p_scope_key, 1, 'DRY_RUN', health_status_value, projection_sequence_value,
    projection_digest_value, active_revision_no_value, active_generation_value,
    active_pack_sha_value, active_payload_sha_value, active_raw_sha_value,
    active_members_sha_value, active_snapshot_id_value, active_snapshot_sha_value,
    source_snapshot_id_value, source_snapshot_sha_value, account_as_of_value,
    quote_as_of_value, projection_generated_at, position_count_value,
    sanitized_error_value
  )
  on conflict (scope_key) do update set
    schema_version = excluded.schema_version,
    mode = excluded.mode,
    health_status = excluded.health_status,
    projection_sequence = excluded.projection_sequence,
    projection_digest = excluded.projection_digest,
    active_revision_no = excluded.active_revision_no,
    active_generation = excluded.active_generation,
    active_pack_sha256 = excluded.active_pack_sha256,
    active_control_payload_sha256 = excluded.active_control_payload_sha256,
    active_control_raw_contract_sha256 = excluded.active_control_raw_contract_sha256,
    active_members_sha256 = excluded.active_members_sha256,
    active_snapshot_id = excluded.active_snapshot_id,
    active_snapshot_sha256 = excluded.active_snapshot_sha256,
    source_market_snapshot_id = excluded.source_market_snapshot_id,
    source_market_snapshot_sha256 = excluded.source_market_snapshot_sha256,
    account_as_of = excluded.account_as_of,
    quote_as_of = excluded.quote_as_of,
    source_generated_at = excluded.source_generated_at,
    position_count = excluded.position_count,
    sanitized_error = excluded.sanitized_error;

  return jsonb_build_object(
    'accepted', true,
    'replayed', false,
    'projection_sequence', projection_sequence_value,
    'position_count', position_count_value
  );
end;
$$;

revoke all on function public.vps_sync_replace_private_projection(text, text, jsonb)
  from PUBLIC, anon, authenticated, service_role;
grant execute on function public.vps_sync_replace_private_projection(text, text, jsonb)
  to service_role;

-- Narrow admin read for the whitelist editor. The returned edit base is the
-- latest pending desired revision, or the active revision when no pending target
-- exists. It never returns control secrets or raw VPS data.
create or replace function public.vps_get_whitelist_control_state()
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public
as $$
declare
  settings public.vps_control_settings%rowtype;
  desired public.vps_whitelist_revisions%rowtype;
  active public.vps_whitelist_revisions%rowtype;
  desired_found boolean;
  active_found boolean;
  desired_symbols jsonb;
  active_symbols jsonb;
begin
  if not public.vps_is_admin() then
    raise exception 'VPS dashboard administrator access is required' using errcode = '42501';
  end if;
  select * into settings
    from public.vps_control_settings
   where id = true;
  if not found then
    raise exception 'VPS control settings are not initialized';
  end if;
  select * into desired
    from public.vps_whitelist_revisions
   where target_device_id = settings.target_device_id
     and status in ('submitted', 'sync_pending', 'preparing')
   order by revision_no desc
   limit 1;
  desired_found := found;
  select * into active
    from public.vps_whitelist_revisions
   where target_device_id = settings.target_device_id
     and status = 'active'
   order by revision_no desc
   limit 1;
  active_found := found;
  select coalesce(jsonb_agg(item.symbol order by item.sort_order), '[]'::jsonb)
    into desired_symbols
    from public.vps_whitelist_revision_items as item
   where item.revision_id = desired.id;
  select coalesce(jsonb_agg(item.symbol order by item.sort_order), '[]'::jsonb)
    into active_symbols
    from public.vps_whitelist_revision_items as item
   where item.revision_id = active.id;
  return jsonb_build_object(
    'desired_revision_no', case when desired_found then desired.revision_no else null end,
    'desired_status', case when desired_found then desired.status else null end,
    'desired_symbols', coalesce(desired_symbols, '[]'::jsonb),
    'active_revision_no', case when active_found then active.revision_no else null end,
    'active_symbols', coalesce(active_symbols, '[]'::jsonb),
    'edit_base_revision_no', case
      when desired_found then desired.revision_no
      when active_found then active.revision_no
      else null
    end,
    'max_active_symbols', settings.max_active_symbols,
    'mode', settings.default_mode,
    'strategy_timezone', settings.strategy_timezone
  );
end;
$$;

revoke all on function public.vps_get_whitelist_control_state() from PUBLIC, anon, service_role;
grant execute on function public.vps_get_whitelist_control_state() to authenticated;

-- Concurrency-safe replacement for the old two-argument browser RPC. The old
-- overload remains as a historical object but loses EXECUTE below.
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
    from public.vps_whitelist_revisions
   where target_device_id = settings.target_device_id
     and status in ('submitted', 'sync_pending', 'preparing')
   order by revision_no desc
   limit 1;
  if found then
    current_base_revision_no := current_desired.revision_no;
  else
    select * into current_active
      from public.vps_whitelist_revisions
     where target_device_id = settings.target_device_id
       and status = 'active'
     order by revision_no desc
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

  select coalesce(max(revision_no), 0) + 1
    into next_revision_no
    from public.vps_whitelist_revisions;
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

  update public.vps_whitelist_revisions
     set status = 'superseded', superseded_by = created_revision_id
   where id <> created_revision_id
     and target_device_id = settings.target_device_id
     and status in ('submitted', 'sync_pending', 'preparing');

  return query
  select created_revision_id, next_revision_no,
         'submitted'::public.vps_whitelist_status,
         current_base_revision_no;
end;
$$;

revoke all on function public.vps_submit_whitelist_revision(text[], text)
  from PUBLIC, anon, authenticated, service_role;
revoke all on function public.vps_submit_whitelist_revision(text[], text, bigint)
  from PUBLIC, anon, authenticated, service_role;
grant execute on function public.vps_submit_whitelist_revision(text[], text, bigint)
  to authenticated;

-- The private scope is a non-secret routing label, not an account identifier.
-- No user membership is seeded here. The project administrator must insert the
-- verified Auth user and the desired scope membership in a separate controlled
-- SQL Editor step after reviewing the postflight output.
insert into public.vps_private_scopes (
  scope_key, target_device_id, display_label, active
)
values (
  'primary', 'stock-sim-v31f-15m', '模拟盘', true
)
on conflict (scope_key) do nothing;

insert into public.vps_private_projection_state (
  scope_key, schema_version, mode, health_status, projection_sequence,
  position_count
)
values (
  'primary', 1, 'DRY_RUN', 'unknown', 0, 0
)
on conflict (scope_key) do nothing;

commit;
