-- Part 4 official dividend-announcement ledger — forward-only private upgrade.
-- MANUAL HOSTED SQL EXECUTION ONLY.
--
-- Scope:
--   * Adds a separate owner-scoped ledger for official dividend-related notices.
--   * Keeps the existing private market document and its implementation-date
--     events intact; it never changes formal annual/interim dividend fields.
--   * Exposes the merged calendar only through personal_get_part4().
--   * Gives authenticated users no direct table privileges; writes go through
--     the narrow validated personal_sync_part4_dividend_notices(...) RPC.
--   * Does not touch vps_*, trading, accounts, orders, or public GitHub data.

begin;

create table if not exists public.personal_part4_dividend_notices (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  source_id text not null check (source_id ~ '^eastmoney:AN[0-9]{12,32}$'),
  stock_code text not null check (stock_code ~ '^[0-9]{6}$'),
  event_date date not null,
  event_type text not null check (event_type in ('分红方案公告', '权益分派公告', '分红相关决议', '中期分红预披露')),
  stage text not null check (stage in ('proposal', 'implementation', 'pre_disclosure')),
  title text not null check (char_length(title) between 1 and 300),
  source_label text not null check (source_label in ('东方财富公司公告', '东方财富公司公告 + 结构化分红核对')),
  source_url text not null check (
    source_url ~ '^https://data\.eastmoney\.com/notices/detail/[0-9]{6}/AN[0-9]{12,32}\.html$'
  ),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  archived_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, source_id)
);

create index if not exists personal_part4_dividend_notices_owner_date_idx
  on public.personal_part4_dividend_notices (owner_user_id, event_date desc, stock_code, source_id);

create table if not exists public.personal_part4_dividend_notice_sync_runs (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  run_id text not null check (run_id ~ '^[A-Za-z0-9._:-]{1,160}$'),
  window_start date not null,
  window_end date not null,
  expected_watchlist_count integer not null check (expected_watchlist_count between 1 and 50),
  scanned_watchlist_count integer not null check (scanned_watchlist_count between 1 and 50),
  official_notice_count integer not null check (official_notice_count >= 0),
  selected_notice_count integer not null check (selected_notice_count between 0 and 500),
  worker_declared_input_sha256 text not null check (worker_declared_input_sha256 ~ '^[0-9a-f]{64}$'),
  source_label text not null check (source_label = 'eastmoney_official_announcement_api'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, run_id),
  check (window_start <= window_end),
  check (scanned_watchlist_count = expected_watchlist_count),
  check (official_notice_count >= selected_notice_count)
);

create index if not exists personal_part4_notice_sync_runs_owner_created_idx
  on public.personal_part4_dividend_notice_sync_runs (owner_user_id, created_at desc, run_id);

create table if not exists public.personal_part4_dividend_notice_run_items (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  run_id text not null,
  source_id text not null check (source_id ~ '^eastmoney:AN[0-9]{12,32}$'),
  primary key (owner_user_id, run_id, source_id),
  foreign key (owner_user_id, run_id) references public.personal_part4_dividend_notice_sync_runs(owner_user_id, run_id) on delete cascade
);

-- A browser session cannot use this capability: only the local worker's
-- Keychain-held random secret is accepted, and only its digest is stored.
-- The row is seeded manually in Hosted SQL from the non-secret digest emitted
-- by `init-writer`; raw capability material is never written to SQL/GitHub.
create table if not exists public.personal_part4_sync_writer_credentials (
  owner_user_id uuid primary key references auth.users(id) on delete cascade,
  secret_sha256 text not null check (secret_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  rotated_at timestamptz not null default now()
);

create table if not exists public.personal_market_quote_snapshots (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  stock_code text not null check (stock_code ~ '^[0-9]{6}$'),
  price numeric(18,6) not null check (price > 0 and price < 1000000),
  as_of timestamptz not null,
  source_label text not null check (source_label = '东方财富公开行情'),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, stock_code)
);
alter table public.personal_market_quote_snapshots enable row level security;
revoke all on table public.personal_market_quote_snapshots from PUBLIC, anon, authenticated, service_role;

create table if not exists public.personal_future_dividend_grid_snapshots (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  stock_code text not null check (stock_code ~ '^[0-9]{6}$'),
  future_dividend numeric(18,6) not null check (future_dividend >= 0 and future_dividend < 1000000),
  status text check (status is null or status in ('已公告待实施', '已公告预披露')),
  as_of timestamptz not null,
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, stock_code),
  check ((future_dividend = 0 and status is null) or (future_dividend > 0 and status in ('已公告待实施', '已公告预披露')))
);
alter table public.personal_future_dividend_grid_snapshots enable row level security;
revoke all on table public.personal_future_dividend_grid_snapshots from PUBLIC, anon, authenticated, service_role;

create table if not exists public.personal_part4_market_document_baselines (
  owner_user_id uuid primary key references auth.users(id) on delete cascade,
  market_sha256 text not null check (market_sha256 ~ '^[0-9a-f]{64}$'),
  captured_at timestamptz not null default now()
);
alter table public.personal_part4_market_document_baselines enable row level security;
revoke all on table public.personal_part4_market_document_baselines from PUBLIC, anon, authenticated, service_role;

create index if not exists personal_part4_notice_run_items_owner_run_idx
  on public.personal_part4_dividend_notice_run_items (owner_user_id, run_id, source_id);

alter table public.personal_part4_dividend_notice_run_items enable row level security;
alter table public.personal_part4_sync_writer_credentials enable row level security;
revoke all on table public.personal_part4_dividend_notice_run_items from PUBLIC, anon, authenticated, service_role;
revoke all on table public.personal_part4_sync_writer_credentials from PUBLIC, anon, authenticated, service_role;

alter table public.personal_part4_dividend_notices enable row level security;
alter table public.personal_part4_dividend_notice_sync_runs enable row level security;

revoke all on table public.personal_part4_dividend_notices from PUBLIC, anon, authenticated, service_role;
revoke all on table public.personal_part4_dividend_notice_sync_runs from PUBLIC, anon, authenticated, service_role;

drop policy if exists personal_part4_dividend_notices_owner_select on public.personal_part4_dividend_notices;
create policy personal_part4_dividend_notices_owner_select
  on public.personal_part4_dividend_notices
  for select to authenticated
  using (owner_user_id = auth.uid());

drop policy if exists personal_part4_dividend_notice_sync_runs_owner_select on public.personal_part4_dividend_notice_sync_runs;
create policy personal_part4_dividend_notice_sync_runs_owner_select
  on public.personal_part4_dividend_notice_sync_runs
  for select to authenticated
  using (owner_user_id = auth.uid());

-- Keep announcement history but make Part 1 add/remove immediately visible in
-- Part 4, even before the next full announcement sync.
create or replace function public.personal_part4_watchlist_archive_trigger()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  update public.personal_part4_dividend_notices
     set archived_at = coalesce(archived_at, now()), updated_at = now()
   where owner_user_id = old.owner_user_id and stock_code = old.stock_code;
  return old;
end;
$$;
revoke all on function public.personal_part4_watchlist_archive_trigger() from PUBLIC, anon, authenticated, service_role;
create or replace function public.personal_part4_watchlist_restore_trigger()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  update public.personal_part4_dividend_notices
     set archived_at = null, updated_at = now()
   where owner_user_id = new.owner_user_id and stock_code = new.stock_code;
  return new;
end;
$$;
revoke all on function public.personal_part4_watchlist_restore_trigger() from PUBLIC, anon, authenticated, service_role;
drop trigger if exists personal_part4_watchlist_archive_after_delete on public.personal_watchlist_items;
create trigger personal_part4_watchlist_archive_after_delete
  after delete on public.personal_watchlist_items
  for each row execute function public.personal_part4_watchlist_archive_trigger();
drop trigger if exists personal_part4_watchlist_restore_after_insert on public.personal_watchlist_items;
create trigger personal_part4_watchlist_restore_after_insert
  after insert on public.personal_watchlist_items
  for each row execute function public.personal_part4_watchlist_restore_trigger();

-- Serialize whole-list replacement with snapshots for the same owner.  This
-- replaces the existing Stage-2 function forward-only; it never touches VPS.
create or replace function public.personal_replace_watchlist(p_items jsonb)
returns jsonb
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
declare
  v_item jsonb;
  v_code text;
  v_display_name text;
  v_seen_codes text[] := array[]::text[];
  v_item_count integer;
  v_replaced_count integer;
begin
  if not public.personal_current_user_is_active() then
    raise exception 'personal_auth_required';
  end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' then
    raise exception 'watchlist_must_be_array';
  end if;
  v_item_count := jsonb_array_length(p_items);
  if v_item_count > 50 then
    raise exception 'watchlist_too_large';
  end if;
  for v_item in select value from jsonb_array_elements(p_items) loop
    if jsonb_typeof(v_item) <> 'object' then
      raise exception 'watchlist_item_must_be_object';
    end if;
    v_code := btrim(v_item->>'code');
    v_display_name := btrim(v_item->>'name');
    if v_code !~ '^[0-9]{6}$' then
      raise exception 'watchlist_code_invalid';
    end if;
    if v_display_name is null or char_length(v_display_name) not between 1 and 80 then
      raise exception 'watchlist_name_invalid';
    end if;
    if v_code = any(v_seen_codes) then
      raise exception 'watchlist_duplicate_code';
    end if;
    v_seen_codes := array_append(v_seen_codes, v_code);
  end loop;
  perform pg_advisory_xact_lock(hashtext('personal_snapshot:' || auth.uid()::text));
  delete from public.personal_watchlist_items where owner_user_id = auth.uid();
  for v_item in select value from jsonb_array_elements(p_items) loop
    v_code := btrim(v_item->>'code');
    v_display_name := btrim(v_item->>'name');
    insert into public.personal_watchlist_items (
      owner_user_id, source_id, stock_code, display_name, payload, source_sha256
    ) values (
      auth.uid(), v_code, v_code, v_display_name, v_item,
      encode(extensions.digest(v_item::text, 'sha256'), 'hex')
    );
  end loop;
  select count(*) into v_replaced_count
    from public.personal_watchlist_items where owner_user_id = auth.uid();
  return jsonb_build_object('count', v_replaced_count);
end;
$$;
revoke all on function public.personal_replace_watchlist(jsonb) from PUBLIC, anon, service_role;
grant execute on function public.personal_replace_watchlist(jsonb) to authenticated;

-- Immutable comparison point: this migration captures the existing market
-- document before the ledger is introduced.  Later verification compares this
-- content fingerprint, never a fixed stock/event count.
insert into public.personal_part4_market_document_baselines (owner_user_id, market_sha256)
select d.owner_user_id, encode(extensions.digest(d.payload::text, 'sha256'), 'hex')
  from public.personal_documents d
 where d.document_key = 'market'
on conflict (owner_user_id) do nothing;

create or replace function public.personal_sync_part4_dividend_notices(
  p_run_id text,
  p_window_start text,
  p_window_end text,
  p_expected_watchlist_count integer,
  p_scanned_watchlist_count integer,
  p_official_notice_count integer,
  p_events jsonb,
  p_worker_declared_input_sha256 text,
  p_writer_secret text,
  p_watchlist_codes jsonb
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
declare
  v_window_start date;
  v_window_end date;
  v_current_watchlist_count integer;
  v_event_count integer;
  v_item jsonb;
  v_source_id text;
  v_date_text text;
  v_event_date date;
  v_code text;
  v_expected_name text;
  v_name text;
  v_event_type text;
  v_stage text;
  v_title text;
  v_description text;
  v_source_label text;
  v_source_url text;
  v_source_hash text;
  v_seen_ids text[] := array[]::text[];
  v_submitted_watchlist_codes text[];
  v_current_watchlist_codes text[];
  v_calendar_notice_count integer;
begin
  if not public.personal_current_user_is_active() then
    raise exception 'personal_auth_required';
  end if;
  if p_writer_secret is null
     or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
     or not exists (
       select 1
         from public.personal_part4_sync_writer_credentials c
        where c.owner_user_id = auth.uid()
          and c.secret_sha256 = encode(extensions.digest(p_writer_secret, 'sha256'), 'hex')
     ) then
    raise exception 'part4_sync_trusted_writer_required';
  end if;
  if p_run_id is null or p_run_id !~ '^[A-Za-z0-9._:-]{1,160}$' then
    raise exception 'part4_sync_run_id_invalid';
  end if;
  if p_window_start is null or p_window_start !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
     or p_window_end is null or p_window_end !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
    raise exception 'part4_sync_window_invalid';
  end if;
  begin
    v_window_start := p_window_start::date;
    v_window_end := p_window_end::date;
  exception when others then
    raise exception 'part4_sync_window_invalid';
  end;
  if to_char(v_window_start, 'YYYY-MM-DD') <> p_window_start
     or to_char(v_window_end, 'YYYY-MM-DD') <> p_window_end
     or v_window_start > v_window_end
     or v_window_end - v_window_start > 366 then
    raise exception 'part4_sync_window_invalid';
  end if;
  if p_events is null or jsonb_typeof(p_events) <> 'array' then
    raise exception 'part4_sync_events_must_be_array';
  end if;
  v_event_count := jsonb_array_length(p_events);
  if v_event_count > 500 then
    raise exception 'part4_sync_events_too_large';
  end if;
  if p_worker_declared_input_sha256 is null or p_worker_declared_input_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'part4_sync_worker_declared_hash_invalid';
  end if;
  if p_official_notice_count is null or p_official_notice_count < v_event_count then
    raise exception 'part4_sync_official_notice_count_invalid';
  end if;

  if p_watchlist_codes is null or jsonb_typeof(p_watchlist_codes) <> 'array' then
    raise exception 'part4_sync_watchlist_set_invalid';
  end if;
  select array_agg(code order by code) into v_submitted_watchlist_codes
    from (
      select btrim(value #>> '{}') as code
        from jsonb_array_elements(p_watchlist_codes)
    ) submitted;
  if coalesce(array_length(v_submitted_watchlist_codes, 1), 0) <> jsonb_array_length(p_watchlist_codes)
     or exists (select 1 from unnest(coalesce(v_submitted_watchlist_codes, array[]::text[])) code where code !~ '^[0-9]{6}$')
     or (select count(distinct code) from unnest(coalesce(v_submitted_watchlist_codes, array[]::text[])) code) <> jsonb_array_length(p_watchlist_codes) then
    raise exception 'part4_sync_watchlist_set_invalid';
  end if;
  select array_agg(stock_code order by stock_code) into v_current_watchlist_codes
    from public.personal_watchlist_items where owner_user_id = auth.uid();
  if v_submitted_watchlist_codes is distinct from v_current_watchlist_codes then
    raise exception 'part4_sync_watchlist_set_changed';
  end if;
  select count(*) into v_current_watchlist_count
    from public.personal_watchlist_items
   where owner_user_id = auth.uid();
  if v_current_watchlist_count not between 1 and 50
     or p_expected_watchlist_count <> v_current_watchlist_count
     or p_scanned_watchlist_count <> v_current_watchlist_count then
    raise exception 'part4_sync_watchlist_coverage_incomplete';
  end if;
  if not exists (
    select 1 from public.personal_documents d
     where d.owner_user_id = auth.uid()
       and d.document_key = 'market'
       and jsonb_typeof(d.payload->'events') = 'array'
  ) then
    raise exception 'part4_sync_market_document_missing';
  end if;

  for v_item in select value from jsonb_array_elements(p_events) loop
    if jsonb_typeof(v_item) <> 'object'
       or not (v_item ?& array['id', 'date', 'code', 'name', 'type', 'stage', 'title', 'description', 'source', 'sourceUrl', 'sourceHash'])
       or (select count(*) from jsonb_object_keys(v_item)) <> 11 then
      raise exception 'part4_sync_event_shape_invalid';
    end if;
    v_source_id := btrim(v_item->>'id');
    v_date_text := btrim(v_item->>'date');
    v_code := btrim(v_item->>'code');
    v_name := btrim(v_item->>'name');
    v_event_type := btrim(v_item->>'type');
    v_stage := btrim(v_item->>'stage');
    v_title := btrim(v_item->>'title');
    v_description := btrim(v_item->>'description');
    v_source_label := btrim(v_item->>'source');
    v_source_url := btrim(v_item->>'sourceUrl');
    v_source_hash := btrim(v_item->>'sourceHash');

    if v_source_id !~ '^eastmoney:AN[0-9]{12,32}$' or v_source_id = any(v_seen_ids) then
      raise exception 'part4_sync_event_id_invalid';
    end if;
    v_seen_ids := array_append(v_seen_ids, v_source_id);
    if v_code !~ '^[0-9]{6}$'
       or v_date_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
       or v_name is null or char_length(v_name) not between 1 and 80
       or v_title is null or char_length(v_title) not between 1 and 300
       or v_description is null or char_length(v_description) not between 1 and 450 then
      raise exception 'part4_sync_event_value_invalid';
    end if;
    begin
      v_event_date := v_date_text::date;
    exception when others then
      raise exception 'part4_sync_event_date_invalid';
    end;
    if to_char(v_event_date, 'YYYY-MM-DD') <> v_date_text
       or v_event_date < v_window_start or v_event_date > v_window_end then
      raise exception 'part4_sync_event_date_invalid';
    end if;
    if v_event_type not in ('分红方案公告', '权益分派公告', '分红相关决议', '中期分红预披露')
       or v_stage not in ('proposal', 'implementation', 'pre_disclosure')
       or (v_event_type = '中期分红预披露' and v_stage <> 'pre_disclosure')
       or (v_event_type <> '中期分红预披露' and v_stage = 'pre_disclosure') then
      raise exception 'part4_sync_event_classification_invalid';
    end if;
    if v_source_label not in ('东方财富公司公告', '东方财富公司公告 + 结构化分红核对')
       or (v_stage = 'pre_disclosure' and v_source_label <> '东方财富公司公告 + 结构化分红核对')
       or (v_stage <> 'pre_disclosure' and v_source_label <> '东方财富公司公告')
       or v_source_hash !~ '^[0-9a-f]{64}$' then
      raise exception 'part4_sync_event_source_invalid';
    end if;
    if v_source_url !~ '^https://data\.eastmoney\.com/notices/detail/[0-9]{6}/AN[0-9]{12,32}\.html$'
       or position(v_code || '/' || replace(v_source_id, 'eastmoney:', '') || '.html' in v_source_url) = 0 then
      raise exception 'part4_sync_event_source_url_invalid';
    end if;
    select display_name into v_expected_name
      from public.personal_watchlist_items
     where owner_user_id = auth.uid() and stock_code = v_code;
    if v_expected_name is null or v_expected_name <> v_name then
      raise exception 'part4_sync_event_watchlist_mismatch';
    end if;
  end loop;

  insert into public.personal_part4_dividend_notices (
    owner_user_id, source_id, stock_code, event_date, event_type, stage,
    title, source_label, source_url, payload, source_sha256
  )
  select auth.uid(),
         btrim(item.event_value->>'id'),
         btrim(item.event_value->>'code'),
         (item.event_value->>'date')::date,
         btrim(item.event_value->>'type'),
         btrim(item.event_value->>'stage'),
         btrim(item.event_value->>'title'),
         btrim(item.event_value->>'source'),
         btrim(item.event_value->>'sourceUrl'),
         item.event_value,
         btrim(item.event_value->>'sourceHash')
    from jsonb_array_elements(p_events) as item(event_value)
  on conflict (owner_user_id, source_id) do update set
    stock_code = excluded.stock_code,
    event_date = excluded.event_date,
    event_type = excluded.event_type,
    stage = excluded.stage,
    title = excluded.title,
    source_label = excluded.source_label,
    source_url = excluded.source_url,
    payload = excluded.payload,
    source_sha256 = excluded.source_sha256,
    archived_at = null,
    updated_at = now();

  -- Preserve history, but exclude symbols removed from the current Part 1
  -- private watchlist from normal Part 4 retrieval.
  update public.personal_part4_dividend_notices n
     set archived_at = coalesce(n.archived_at, now()), updated_at = now()
   where n.owner_user_id = auth.uid()
     and not exists (
       select 1 from public.personal_watchlist_items w
        where w.owner_user_id = n.owner_user_id and w.stock_code = n.stock_code
     );

  insert into public.personal_part4_dividend_notice_sync_runs (
    owner_user_id, run_id, window_start, window_end, expected_watchlist_count,
    scanned_watchlist_count, official_notice_count, selected_notice_count,
    worker_declared_input_sha256, source_label
  ) values (
    auth.uid(), p_run_id, v_window_start, v_window_end, p_expected_watchlist_count,
    p_scanned_watchlist_count, p_official_notice_count, v_event_count,
    p_worker_declared_input_sha256, 'eastmoney_official_announcement_api'
  ) on conflict (owner_user_id, run_id) do update set
    window_start = excluded.window_start,
    window_end = excluded.window_end,
    expected_watchlist_count = excluded.expected_watchlist_count,
    scanned_watchlist_count = excluded.scanned_watchlist_count,
    official_notice_count = excluded.official_notice_count,
    selected_notice_count = excluded.selected_notice_count,
    worker_declared_input_sha256 = excluded.worker_declared_input_sha256,
    source_label = excluded.source_label,
    updated_at = now();

  delete from public.personal_part4_dividend_notice_run_items
   where owner_user_id = auth.uid() and run_id = p_run_id;
  insert into public.personal_part4_dividend_notice_run_items (owner_user_id, run_id, source_id)
  select auth.uid(), p_run_id, btrim(item.event_value->>'id')
    from jsonb_array_elements(p_events) as item(event_value);

  select count(*) into v_calendar_notice_count
    from public.personal_part4_dividend_notices n
   where n.owner_user_id = auth.uid();
  return jsonb_build_object(
    'requested', v_event_count,
    'stored', v_event_count,
    'calendar_notice_count', v_calendar_notice_count,
    'watchlist_count', v_current_watchlist_count
  );
end;
$$;

revoke all on function public.personal_sync_part4_dividend_notices(text, text, text, integer, integer, integer, jsonb, text, text, jsonb)
  from PUBLIC, anon, service_role;
grant execute on function public.personal_sync_part4_dividend_notices(text, text, text, integer, integer, integer, jsonb, text, text, jsonb)
  to authenticated;

create or replace function public.personal_sync_market_snapshot(
  p_as_of text,
  p_quotes jsonb,
  p_writer_secret text
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
declare
  v_as_of timestamptz;
  v_expected_count integer;
  v_quote_count integer;
  v_item jsonb;
  v_code text;
  v_price numeric;
  v_seen_codes text[] := array[]::text[];
begin
  if not public.personal_current_user_is_active() then
    raise exception 'personal_auth_required';
  end if;
  if p_writer_secret is null
     or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
     or not exists (
       select 1 from public.personal_part4_sync_writer_credentials c
        where c.owner_user_id = auth.uid()
          and c.secret_sha256 = encode(extensions.digest(p_writer_secret, 'sha256'), 'hex')
     ) then
    raise exception 'market_snapshot_trusted_writer_required';
  end if;
  if p_as_of is null or p_quotes is null or jsonb_typeof(p_quotes) <> 'array' then
    raise exception 'market_snapshot_input_invalid';
  end if;
  begin
    v_as_of := p_as_of::timestamptz;
  exception when others then
    raise exception 'market_snapshot_as_of_invalid';
  end;
  if v_as_of < now() - interval '3 days' or v_as_of > now() + interval '5 minutes' then
    raise exception 'market_snapshot_as_of_out_of_range';
  end if;
  perform pg_advisory_xact_lock(hashtext('personal_snapshot:' || auth.uid()::text));
  select count(*) into v_expected_count from public.personal_watchlist_items where owner_user_id = auth.uid();
  v_quote_count := jsonb_array_length(p_quotes);
  if v_expected_count not between 1 and 50 or v_quote_count <> v_expected_count then
    raise exception 'market_snapshot_coverage_incomplete';
  end if;
  for v_item in select value from jsonb_array_elements(p_quotes) loop
    if jsonb_typeof(v_item) <> 'object'
       or not (v_item ?& array['code', 'price'])
       or (select count(*) from jsonb_object_keys(v_item)) <> 2
       or jsonb_typeof(v_item->'code') <> 'string'
       or jsonb_typeof(v_item->'price') <> 'number' then
      raise exception 'market_snapshot_quote_shape_invalid';
    end if;
    v_code := btrim(v_item->>'code');
    begin
      v_price := (v_item->>'price')::numeric;
    exception when others then
      raise exception 'market_snapshot_quote_price_invalid';
    end;
    if v_code !~ '^[0-9]{6}$' or v_code = any(v_seen_codes)
       or v_price <= 0 or v_price >= 1000000
       or not exists (select 1 from public.personal_watchlist_items w where w.owner_user_id = auth.uid() and w.stock_code = v_code) then
      raise exception 'market_snapshot_quote_invalid';
    end if;
    v_seen_codes := array_append(v_seen_codes, v_code);
  end loop;
  if exists (
    select 1 from public.personal_market_quote_snapshots q
     where q.owner_user_id = auth.uid()
       and q.as_of > v_as_of
       and exists (select 1 from public.personal_watchlist_items w where w.owner_user_id = q.owner_user_id and w.stock_code = q.stock_code)
  ) then
    raise exception 'market_snapshot_time_regression';
  end if;
  insert into public.personal_market_quote_snapshots (owner_user_id, stock_code, price, as_of, source_label)
  select auth.uid(), btrim(item.value->>'code'), (item.value->>'price')::numeric, v_as_of, '东方财富公开行情'
    from jsonb_array_elements(p_quotes) item(value)
  on conflict (owner_user_id, stock_code) do update set price = excluded.price, as_of = excluded.as_of, source_label = excluded.source_label, updated_at = now();
  update public.personal_market_quote_snapshots q
     set updated_at = now()
   where q.owner_user_id = auth.uid()
     and not exists (select 1 from public.personal_watchlist_items w where w.owner_user_id = q.owner_user_id and w.stock_code = q.stock_code);
  return jsonb_build_object('stored', v_quote_count, 'as_of', v_as_of::text);
end;
$$;
revoke all on function public.personal_sync_market_snapshot(text, jsonb, text) from PUBLIC, anon, service_role;
grant execute on function public.personal_sync_market_snapshot(text, jsonb, text) to authenticated;

create or replace function public.personal_sync_future_dividend_grid(
  p_as_of text,
  p_records jsonb,
  p_writer_secret text
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
declare
  v_as_of timestamptz;
  v_expected_count integer;
  v_record_count integer;
  v_item jsonb;
  v_code text;
  v_value numeric;
  v_status text;
  v_seen_codes text[] := array[]::text[];
begin
  if not public.personal_current_user_is_active() then
    raise exception 'personal_auth_required';
  end if;
  if p_writer_secret is null
     or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
     or not exists (
       select 1 from public.personal_part4_sync_writer_credentials c
        where c.owner_user_id = auth.uid()
          and c.secret_sha256 = encode(extensions.digest(p_writer_secret, 'sha256'), 'hex')
     ) then
    raise exception 'future_dividend_trusted_writer_required';
  end if;
  if p_as_of is null or p_records is null or jsonb_typeof(p_records) <> 'array' then
    raise exception 'future_dividend_input_invalid';
  end if;
  begin
    v_as_of := p_as_of::timestamptz;
  exception when others then
    raise exception 'future_dividend_as_of_invalid';
  end;
  if v_as_of < now() - interval '3 days' or v_as_of > now() + interval '5 minutes' then
    raise exception 'future_dividend_as_of_out_of_range';
  end if;
  perform pg_advisory_xact_lock(hashtext('personal_snapshot:' || auth.uid()::text));
  select count(*) into v_expected_count
    from public.personal_watchlist_items where owner_user_id = auth.uid();
  v_record_count := jsonb_array_length(p_records);
  if v_expected_count not between 1 and 50 or v_record_count <> v_expected_count then
    raise exception 'future_dividend_coverage_incomplete';
  end if;
  for v_item in select value from jsonb_array_elements(p_records) loop
    if jsonb_typeof(v_item) <> 'object'
       or not (v_item ?& array['code', 'futureDividend', 'status'])
       or (select count(*) from jsonb_object_keys(v_item)) <> 3
       or jsonb_typeof(v_item->'code') <> 'string'
       or jsonb_typeof(v_item->'futureDividend') <> 'number'
       or (v_item->'status' <> 'null'::jsonb and jsonb_typeof(v_item->'status') <> 'string') then
      raise exception 'future_dividend_record_shape_invalid';
    end if;
    v_code := btrim(v_item->>'code');
    v_status := nullif(btrim(v_item->>'status'), '');
    begin
      v_value := (v_item->>'futureDividend')::numeric;
    exception when others then
      raise exception 'future_dividend_value_invalid';
    end;
    if v_code !~ '^[0-9]{6}$'
       or v_code = any(v_seen_codes)
       or v_value < 0 or v_value >= 1000000
       or (v_value = 0 and v_status is not null)
       or (v_value > 0 and v_status not in ('已公告待实施', '已公告预披露'))
       or not exists (
         select 1 from public.personal_watchlist_items w
          where w.owner_user_id = auth.uid() and w.stock_code = v_code
       ) then
      raise exception 'future_dividend_record_invalid';
    end if;
    v_seen_codes := array_append(v_seen_codes, v_code);
  end loop;
  if exists (
    select 1 from public.personal_future_dividend_grid_snapshots f
     where f.owner_user_id = auth.uid() and f.as_of > v_as_of
       and exists (
         select 1 from public.personal_watchlist_items w
          where w.owner_user_id = f.owner_user_id and w.stock_code = f.stock_code
       )
  ) then
    raise exception 'future_dividend_time_regression';
  end if;
  insert into public.personal_future_dividend_grid_snapshots (
    owner_user_id, stock_code, future_dividend, status, as_of
  )
  select auth.uid(), btrim(item.value->>'code'), (item.value->>'futureDividend')::numeric,
         nullif(btrim(item.value->>'status'), ''), v_as_of
    from jsonb_array_elements(p_records) item(value)
  on conflict (owner_user_id, stock_code) do update set
    future_dividend = excluded.future_dividend,
    status = excluded.status,
    as_of = excluded.as_of,
    updated_at = now();
  return jsonb_build_object('stored', v_record_count, 'as_of', v_as_of::text);
end;
$$;
revoke all on function public.personal_sync_future_dividend_grid(text, jsonb, text) from PUBLIC, anon, service_role;
grant execute on function public.personal_sync_future_dividend_grid(text, jsonb, text) to authenticated;

-- Return the pre-existing market document plus the separately stored official
-- notice ledger.  This is a read-only composition: it does not rewrite the
-- base migration payload or its original source/provenance fields.
create or replace function public.personal_get_part4()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  with base as (
    select jsonb_set(
      jsonb_set(
        jsonb_set(
        d.payload,
        '{stocks}',
        coalesce((
          select jsonb_agg(
            coalesce(b.stock, jsonb_build_object('code', w.stock_code, 'name', w.display_name))
              || jsonb_strip_nulls(jsonb_build_object(
                   'price', q.price,
                   'quoteAsOf', q.as_of::text,
                   'quoteSource', q.source_label,
                   'futureDividend', f.future_dividend,
                   'futureDividendStatus', f.status,
                   'futureDividendAsOf', f.as_of::text
                 ))
            order by w.stock_code
          )
          from public.personal_watchlist_items w
          left join lateral (
            select s.stock
              from jsonb_array_elements(coalesce(d.payload->'stocks', '[]'::jsonb)) as s(stock)
             where btrim(s.stock->>'code') = w.stock_code
             limit 1
          ) b on true
          left join public.personal_market_quote_snapshots q
            on q.owner_user_id = w.owner_user_id and q.stock_code = w.stock_code
          left join public.personal_future_dividend_grid_snapshots f
            on f.owner_user_id = w.owner_user_id and f.stock_code = w.stock_code
         where w.owner_user_id = d.owner_user_id
        ), '[]'::jsonb),
        true
      ),
      '{events}',
      coalesce((
        select jsonb_agg(e.event order by btrim(e.event->>'date'), btrim(e.event->>'code'), btrim(e.event->>'id'))
          from jsonb_array_elements(coalesce(d.payload->'events', '[]'::jsonb)) as e(event)
         where exists (
           select 1 from public.personal_watchlist_items w
            where w.owner_user_id = d.owner_user_id
              and w.stock_code = btrim(e.event->>'code')
         )
      ), '[]'::jsonb),
      true
    ),
      '{updatedAt}',
      to_jsonb(coalesce(
        (select max(q.as_of)::text
           from public.personal_market_quote_snapshots q
          where q.owner_user_id = d.owner_user_id
            and exists (
              select 1 from public.personal_watchlist_items w
               where w.owner_user_id = q.owner_user_id and w.stock_code = q.stock_code
            )),
        d.payload->>'updatedAt'
      )),
      true
    ) as payload
      from public.personal_documents d
     where d.owner_user_id = auth.uid()
       and d.document_key = 'market'
  ), notices as (
    select n.payload, n.event_date, n.stock_code, n.source_id, n.updated_at
      from public.personal_part4_dividend_notices n
     where n.owner_user_id = auth.uid()
       and n.archived_at is null
       and exists (
         select 1 from public.personal_watchlist_items w
          where w.owner_user_id = n.owner_user_id and w.stock_code = n.stock_code
       )
  ), notice_payload as (
    select coalesce(
      jsonb_agg(payload order by event_date, stock_code, source_id),
      '[]'::jsonb
    ) as events,
    max(updated_at)::text as updated_at,
    count(*) as event_count
      from notices
  )
  select case
    when not public.personal_current_user_is_active() then null
    when not exists (select 1 from base) then null
    when (select event_count from notice_payload) = 0 then (select payload from base)
    else jsonb_set(
      jsonb_set(
        (select payload from base),
        '{events}',
        coalesce((select payload->'events' from base), '[]'::jsonb)
          || (select events from notice_payload),
        true
      ),
      '{calendarNoticeUpdatedAt}',
      to_jsonb((select updated_at from notice_payload)),
      true
    )
  end;
$$;
revoke all on function public.personal_get_part4() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_part4() to authenticated;

-- The header status now includes separately synchronized official notices.
create or replace function public.personal_get_migration_state()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select case when not public.personal_current_user_is_active() then null else jsonb_build_object(
    'schema_version', 3,
    'source_files', (select count(*) from public.personal_import_batches b where b.owner_user_id = auth.uid()),
    'watchlist_count', (select count(*) from public.personal_watchlist_items w where w.owner_user_id = auth.uid()),
    'news_count', (select count(*) from public.personal_news_items n where n.owner_user_id = auth.uid()),
    'trade_count', (select count(*) from public.personal_trade_records t where t.owner_user_id = auth.uid()),
    'feedback_count', (select count(*) from public.personal_strategy_feedback f where f.owner_user_id = auth.uid()),
    'recommendation_count', (select count(*) from public.personal_strategy_recommendations r where r.owner_user_id = auth.uid()),
    'market_stock_count', coalesce((select jsonb_array_length(d.payload->'stocks') from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'market'), 0),
    'calendar_event_count', coalesce((select jsonb_array_length(d.payload->'events') from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'market'), 0)
      + (select count(*) from public.personal_part4_dividend_notices n where n.owner_user_id = auth.uid()),
    'weekly_boll_count', coalesce((select count(*) from jsonb_array_elements(coalesce((select d.payload->'stocks' from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'market'), '[]'::jsonb)) s where jsonb_typeof(s->'weeklyBoll') = 'object'), 0),
    'live_archive_source', (select max(b.source_path) from public.personal_import_batches b where b.owner_user_id = auth.uid() and b.source_path like 'live/%'),
    'latest_imported_at', (select max(b.imported_at) from public.personal_import_batches b where b.owner_user_id = auth.uid())
  ) end;
$$;
revoke all on function public.personal_get_migration_state() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_migration_state() to authenticated;

notify pgrst, 'reload schema';

commit;
