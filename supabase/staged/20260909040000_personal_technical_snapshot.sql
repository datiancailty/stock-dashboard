-- STAGED CANDIDATE ONLY: user-executed SQL, never an automatic migration.
-- Prerequisites: existing personal_get_part4_v2(), owner/watchlist tables,
-- personal_current_user_is_active(), existing Part4 writer credential + digest.
-- No credentials, business values, backfill, legacy-function replacement or
-- direct table grants. This file only introduces technical storage and two RPCs.
-- Collection time is NOT a trading date. The trusted collector must independently
-- verify the exchange calendar, latest session and raw/adjusted row alignment.
begin;

do $prerequisites$
begin
  if to_regprocedure('public.personal_get_part4_v2()') is null then
    raise exception 'technical_snapshot_part4_v2_required';
  end if;
end;
$prerequisites$;

create table if not exists public.personal_technical_snapshots (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  stock_code text not null check (stock_code ~ '^[0-9]{6}$'),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  as_of timestamptz not null check (isfinite(as_of)),
  trading_date date not null check (isfinite(trading_date)),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, stock_code)
);
-- Reinstall is allowed only for this table contract, not an unrelated same-name
-- table. Abort the entire transaction on incompatible shape or injected hooks.
do $guard$
begin
  if (select count(*) from pg_attribute where attrelid = 'public.personal_technical_snapshots'::regclass
      and attnum > 0 and not attisdropped) <> 6
     or exists (select 1 from (values
        ('owner_user_id','uuid'),('stock_code','text'),('payload','jsonb'),
        ('as_of','timestamp with time zone'),('trading_date','date'),
        ('updated_at','timestamp with time zone')) e(name,typ)
       where not exists (select 1 from pg_attribute a
         where a.attrelid = 'public.personal_technical_snapshots'::regclass and a.attname = e.name
           and a.attnotnull and format_type(a.atttypid,a.atttypmod) = e.typ))
     or (select count(*) from pg_constraint where conrelid = 'public.personal_technical_snapshots'::regclass
         and contype in ('c','p','f')) <> 6
     or exists (select 1 from (values
         ($def$CHECK ((jsonb_typeof(payload) = 'object'::text))$def$),
         ($def$CHECK ((stock_code ~ '^[0-9]{6}$'::text))$def$),
         ($def$CHECK (isfinite(as_of))$def$),
         ($def$CHECK (isfinite(trading_date))$def$)) e(definition)
       where not exists (select 1 from pg_constraint c
         where c.conrelid = 'public.personal_technical_snapshots'::regclass
           and c.contype = 'c' and c.convalidated and pg_get_constraintdef(c.oid) = e.definition))
     or not exists (select 1 from pg_constraint where conrelid = 'public.personal_technical_snapshots'::regclass
       and contype = 'p' and pg_get_constraintdef(oid) = 'PRIMARY KEY (owner_user_id, stock_code)')
     or not exists (select 1 from pg_constraint where conrelid = 'public.personal_technical_snapshots'::regclass
       and contype = 'f' and pg_get_constraintdef(oid) = 'FOREIGN KEY (owner_user_id) REFERENCES auth.users(id) ON DELETE CASCADE')
     or exists (select 1 from pg_policy where polrelid = 'public.personal_technical_snapshots'::regclass)
     or exists (select 1 from pg_trigger where tgrelid = 'public.personal_technical_snapshots'::regclass and not tgisinternal) then
    raise exception 'technical_snapshot_existing_schema_incompatible';
  end if;
end;
$guard$;
alter table public.personal_technical_snapshots enable row level security;
revoke all on table public.personal_technical_snapshots from PUBLIC, anon, authenticated, service_role;

create or replace function public.personal_sync_technical_snapshot(
  p_as_of text, p_records jsonb, p_writer_secret text
)
returns jsonb language plpgsql volatile security definer
set search_path = pg_catalog, public
as $$
declare
  v_as_of timestamptz;
  v_date date;
  v_batch_date date;
  v_collection_date date;
  v_expected integer;
  v_item jsonb;
  v_boll jsonb;
  v_positions jsonb;
  v_position jsonb;
  v_key text;
  v_code text;
  v_zone text;
  v_seen text[] := array[]::text[];
  v_middle numeric;
  v_upper numeric;
  v_lower numeric;
  v_percent numeric;
  v_low numeric;
  v_high numeric;
  v_idempotent boolean;
begin
  if auth.uid() is null or public.personal_current_user_is_active() is distinct from true then
    raise exception 'personal_auth_required';
  end if;
  if p_writer_secret is null or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
     or not exists (select 1 from public.personal_part4_sync_writer_credentials c
       where c.owner_user_id = auth.uid()
         and c.secret_sha256 = encode(extensions.digest(p_writer_secret, 'sha256'), 'hex')) then
    raise exception 'technical_snapshot_trusted_writer_required';
  end if;
  if p_as_of is null or p_as_of !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$'
     or jsonb_typeof(p_records) is distinct from 'array'
     or octet_length(p_records::text) > 2000000 then
    raise exception 'technical_snapshot_input_invalid';
  end if;
  begin
    v_as_of := p_as_of::timestamptz;
  exception when others then raise exception 'technical_snapshot_as_of_invalid';
  end;
  if not isfinite(v_as_of) or v_as_of < now() - interval '3 days'
     or v_as_of > now() + interval '5 minutes' then
    raise exception 'technical_snapshot_as_of_out_of_range';
  end if;
  v_collection_date := (v_as_of at time zone 'Asia/Shanghai')::date;
  -- Same owner-wide advisory namespace as the market/forward snapshot writers.
  -- SHARE also blocks watchlist writers lacking advisory coordination, including
  -- delete/reinsert or an empty-list race. No provider work runs under this lock.
  perform pg_advisory_xact_lock(hashtext('personal_snapshot:' || auth.uid()::text));
  lock table public.personal_watchlist_items in share mode;
  select count(*) into v_expected from public.personal_watchlist_items where owner_user_id = auth.uid();
  if v_expected not between 1 and 50 or jsonb_array_length(p_records) <> v_expected then
    raise exception 'technical_snapshot_coverage_incomplete';
  end if;
  for v_item in select value from jsonb_array_elements(p_records) loop
    if jsonb_typeof(v_item) is distinct from 'object'
       or not (v_item ?& array['code','asOf','source','weeklyBoll','positions'])
       or v_item - array['code','asOf','source','weeklyBoll','positions'] <> '{}'::jsonb
       or jsonb_typeof(v_item->'code') is distinct from 'string'
       or jsonb_typeof(v_item->'asOf') is distinct from 'string'
       or jsonb_typeof(v_item->'source') is distinct from 'string'
       or v_item->>'source' <> 'hithink_daily'
       or octet_length(v_item::text) > 16000 then
      raise exception 'technical_snapshot_record_shape_invalid';
    end if;
    v_code := v_item->>'code';
    if v_code !~ '^[0-9]{6}$' or v_code = any(v_seen)
       or not exists (select 1 from public.personal_watchlist_items w
         where w.owner_user_id = auth.uid() and w.stock_code = v_code) then
      raise exception 'technical_snapshot_record_identity_invalid';
    end if;
    v_seen := array_append(v_seen, v_code);
    if v_item->>'asOf' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
      raise exception 'technical_snapshot_trading_date_invalid';
    end if;
    begin
      v_date := (v_item->>'asOf')::date;
    exception when others then raise exception 'technical_snapshot_trading_date_invalid';
    end;
    if not isfinite(v_date) or to_char(v_date,'YYYY-MM-DD') <> v_item->>'asOf'
       or v_date > v_collection_date or v_date < v_collection_date - 14
       or extract(isodow from v_date) not between 1 and 5
       or (v_batch_date is not null and v_date <> v_batch_date) then
      raise exception 'technical_snapshot_trading_date_invalid';
    end if;
    v_batch_date := v_date;
    v_boll := v_item->'weeklyBoll';
    if jsonb_typeof(v_boll) is distinct from 'object'
       or not (v_boll ?& array['asOf','basis','period','multiplier','stddev','sampleCount','middle','upper','lower'])
       or v_boll - array['asOf','basis','period','multiplier','stddev','sampleCount','middle','upper','lower'] <> '{}'::jsonb
       or v_boll->'asOf' is distinct from v_item->'asOf'
       or v_boll->'basis' is distinct from '"前复权周K"'::jsonb
       or v_boll->'period' is distinct from '20'::jsonb
       or v_boll->'multiplier' is distinct from '2'::jsonb
       or v_boll->'stddev' is distinct from '"sample"'::jsonb
       or v_boll->'sampleCount' is distinct from '20'::jsonb then
      raise exception 'technical_snapshot_boll_shape_invalid';
    end if;
    foreach v_key in array array['middle','upper','lower'] loop
      if jsonb_typeof(v_boll->v_key) is distinct from 'number' then
        raise exception 'technical_snapshot_boll_numeric_invalid';
      end if;
      if abs((v_boll->>v_key)::numeric) >= 1000000
         or round((v_boll->>v_key)::numeric,3) <> (v_boll->>v_key)::numeric then
        raise exception 'technical_snapshot_boll_numeric_invalid';
      end if;
    end loop;
    v_middle := (v_boll->>'middle')::numeric;
    v_upper := (v_boll->>'upper')::numeric;
    v_lower := (v_boll->>'lower')::numeric;
    -- A volatile instrument can legitimately have a negative BOLL lower band.
    if v_middle <= 0 or v_lower > v_middle or v_upper < v_middle
       or abs(v_upper + v_lower - 2*v_middle) > 0.002 then
      raise exception 'technical_snapshot_boll_order_or_symmetry_invalid';
    end if;
    v_positions := v_item->'positions';
    if jsonb_typeof(v_positions) is distinct from 'object'
       or not (v_positions ?& array['asOf','day','week','month'])
       or v_positions - array['asOf','day','week','month'] <> '{}'::jsonb
       or v_positions->'asOf' is distinct from v_item->'asOf' then
      raise exception 'technical_snapshot_positions_shape_invalid';
    end if;
    foreach v_key in array array['day','week','month'] loop
      v_position := v_positions->v_key;
      if jsonb_typeof(v_position) is distinct from 'object'
         or not (v_position ?& array['zone','percent','low','high'])
         or v_position - array['zone','percent','low','high'] <> '{}'::jsonb
         or jsonb_typeof(v_position->'zone') is distinct from 'string'
         or jsonb_typeof(v_position->'percent') is distinct from 'number'
         or jsonb_typeof(v_position->'low') is distinct from 'number'
         or jsonb_typeof(v_position->'high') is distinct from 'number' then
        raise exception 'technical_snapshot_position_shape_invalid';
      end if;
      v_zone := v_position->>'zone';
      v_percent := (v_position->>'percent')::numeric;
      v_low := (v_position->>'low')::numeric;
      v_high := (v_position->>'high')::numeric;
      if v_zone not in ('下部','中部','上部') or v_percent not between 0 and 100
         or round(v_percent,1) <> v_percent or v_low <= 0 or v_high < v_low or v_high >= 1000000
         or round(v_low,3) <> v_low or round(v_high,3) <> v_high then
        raise exception 'technical_snapshot_position_value_invalid';
      end if;
      -- zone is computed BEFORE percent rounding in update_market.position_item.
      -- 33.3 and 66.7 legitimately straddle their respective zone boundaries.
      if (v_zone = '下部' and v_percent > 33.3)
         or (v_zone = '中部' and v_percent not between 33.3 and 66.7)
         or (v_zone = '上部' and v_percent < 66.7) then
        raise exception 'technical_snapshot_position_zone_invalid';
      end if;
    end loop;
  end loop;
  -- Retained removed symbols also preserve the owner's successful high-water
  -- mark; a complete watchlist replacement cannot reset monotonicity.
  if exists (select 1 from public.personal_technical_snapshots s
      where s.owner_user_id = auth.uid() and (s.as_of > v_as_of or s.trading_date > v_batch_date)) then
    raise exception 'technical_snapshot_time_regression';
  end if;
  if exists (select 1 from public.personal_technical_snapshots s
      join jsonb_array_elements(p_records) r on r->>'code' = s.stock_code
      where s.owner_user_id = auth.uid() and s.as_of = v_as_of and s.payload <> r - 'code') then
    raise exception 'technical_snapshot_same_time_conflict';
  end if;
  select count(*) = v_expected into v_idempotent
    from public.personal_technical_snapshots s
    join jsonb_array_elements(p_records) r on r->>'code' = s.stock_code
    where s.owner_user_id = auth.uid() and s.as_of = v_as_of and s.payload = r - 'code';
  insert into public.personal_technical_snapshots(owner_user_id, stock_code, payload, as_of, trading_date)
    select auth.uid(), value->>'code', value - 'code', v_as_of, v_batch_date
    from jsonb_array_elements(p_records)
  on conflict (owner_user_id, stock_code) do update
    set payload = excluded.payload, as_of = excluded.as_of,
        trading_date = excluded.trading_date, updated_at = now()
    where personal_technical_snapshots.as_of <> excluded.as_of
       or personal_technical_snapshots.payload <> excluded.payload;
  return jsonb_build_object('stored',v_expected,'as_of',p_as_of,
    'technical_as_of',to_char(v_batch_date,'YYYY-MM-DD'),'idempotent',v_idempotent);
end;
$$;
revoke all on function public.personal_sync_technical_snapshot(text,jsonb,text) from PUBLIC, anon, service_role;
grant execute on function public.personal_sync_technical_snapshot(text,jsonb,text) to authenticated;

-- v2 is the sole base: preserve forwardBasis/events/trades and unknown fields.
-- No new snapshot: suppress imported technical values (null/missing), retaining
-- them ONLY in the untouched legacy document/v2 getter, never relabel as fresh.
-- Expired validated snapshot: keep its original values/dates with explicit stale.
-- "ready" means validated/recent collection, NOT an SQL-verified latest session.
create or replace function public.personal_get_part4_v3()
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, public
as $$
declare
  v_base jsonb;
  v_stocks jsonb;
begin
  if auth.uid() is null or public.personal_current_user_is_active() is distinct from true then
    return null;
  end if;
  v_base := public.personal_get_part4_v2();
  if v_base is null then return null; end if;
  if jsonb_typeof(v_base->'stocks') is distinct from 'array' then
    raise exception 'technical_snapshot_base_stocks_invalid';
  end if;
  select coalesce(jsonb_agg(s.stock || jsonb_build_object(
      'weeklyBoll', t.payload->'weeklyBoll',
      'positions', t.payload->'positions',
      'technicalSource', t.payload->'source',
      'technicalAsOf', t.payload->'asOf',
      'technicalCollectedAt', to_char(t.as_of at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
      'technicalStatus', case when t.stock_code is null then 'missing'
        when t.as_of < now() - interval '3 days'
          or t.trading_date < (now() at time zone 'Asia/Shanghai')::date - 14 then 'stale'
        else 'ready' end,
      'technicalReason', case when t.stock_code is null then 'not_collected'
        when t.as_of < now() - interval '3 days'
          or t.trading_date < (now() at time zone 'Asia/Shanghai')::date - 14 then 'freshness_window_elapsed'
        else null end
    ) order by s.ordinal), '[]'::jsonb) into v_stocks
    from jsonb_array_elements(v_base->'stocks') with ordinality s(stock,ordinal)
    join public.personal_watchlist_items w
      on w.owner_user_id = auth.uid() and w.stock_code = s.stock->>'code'
    left join public.personal_technical_snapshots t
      on t.owner_user_id = w.owner_user_id and t.stock_code = w.stock_code;
  return jsonb_set(v_base,'{stocks}',v_stocks,true);
end;
$$;
revoke all on function public.personal_get_part4_v3() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_part4_v3() to authenticated;
comment on function public.personal_sync_technical_snapshot(text,jsonb,text) is
  'Full current-watchlist technical snapshot; authenticated active owner + existing trusted writer; ISO collection time separate from consistent trading dates; no legacy document mutation.';
comment on function public.personal_get_part4_v3() is
  'v2 plus owner/current-watchlist technical overlay; missing legacy indicators suppressed, aged validated indicators explicitly stale; never advances quote updatedAt.';
notify pgrst, 'reload schema';
commit;
