-- STAGED ONLY: user-manual SQL execution. No automatic Hosted migration.
-- Separate deterministic refresh observability; never writes AI health, private
-- documents, quotes, technical, forward basis, recommendations or credentials.
-- Prerequisite: installed personal_get_part4_v3() and existing Part4 writer.
begin;
do $prerequisites$
begin
  if to_regprocedure('public.personal_get_part4_v3()') is null
     or to_regprocedure('public.personal_current_user_is_active()') is null
     or to_regprocedure('extensions.digest(text,text)') is null
     or to_regclass('public.personal_watchlist_items') is null
     or to_regclass('public.personal_part4_sync_writer_credentials') is null then
    raise exception 'refresh_observability_prerequisites_required';
  end if;
end;
$prerequisites$;

create table if not exists public.personal_confirmed_dividend_snapshots (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  stock_code text not null check (stock_code ~ '^[0-9]{6}$'),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  as_of timestamptz not null check (isfinite(as_of)),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, stock_code)
);
do $guard$
begin
  if (select count(*) from pg_attribute where attrelid = 'public.personal_confirmed_dividend_snapshots'::regclass and attnum > 0 and not attisdropped) <> 5
     or exists (select 1 from (values ('owner_user_id','uuid'),('stock_code','text'),('payload','jsonb'),('as_of','timestamp with time zone'),('updated_at','timestamp with time zone')) e(name,typ)
       where not exists (select 1 from pg_attribute a where a.attrelid = 'public.personal_confirmed_dividend_snapshots'::regclass and a.attname = e.name and a.attnotnull and format_type(a.atttypid,a.atttypmod) = e.typ))
     or (select count(*) from pg_constraint where conrelid = 'public.personal_confirmed_dividend_snapshots'::regclass and contype in ('c','p','f')) <> 5
     or exists (select 1 from (values
       ($d$CHECK ((stock_code ~ '^[0-9]{6}$'::text))$d$),
       ($d$CHECK ((jsonb_typeof(payload) = 'object'::text))$d$),
       ($d$CHECK (isfinite(as_of))$d$),
       ('PRIMARY KEY (owner_user_id, stock_code)'),
       ('FOREIGN KEY (owner_user_id) REFERENCES auth.users(id) ON DELETE CASCADE')) e(def)
       where not exists (select 1 from pg_constraint c where c.conrelid = 'public.personal_confirmed_dividend_snapshots'::regclass and c.convalidated and pg_get_constraintdef(c.oid) = e.def))
     or exists (select 1 from pg_policy where polrelid = 'public.personal_confirmed_dividend_snapshots'::regclass)
     or exists (select 1 from pg_trigger where tgrelid = 'public.personal_confirmed_dividend_snapshots'::regclass and not tgisinternal) then
    raise exception 'confirmed_dividends_existing_schema_incompatible';
  end if;
end;
$guard$;
alter table public.personal_confirmed_dividend_snapshots enable row level security;
revoke all on table public.personal_confirmed_dividend_snapshots from PUBLIC, anon, authenticated, service_role;

create or replace function public.personal_sync_confirmed_dividends(p_as_of text, p_records jsonb, p_writer_secret text)
returns jsonb language plpgsql volatile security definer
set search_path = pg_catalog, public
as $$
declare
  v_as_of timestamptz;
  v_end date;
  v_start date;
  v_report date;
  v_payment date;
  v_expected integer;
  v_item jsonb;
  v_component jsonb;
  v_code text;
  v_seen text[] := array[]::text[];
  v_components_seen text[];
  v_identity text;
  v_sum numeric;
  v_amount numeric;
  v_idempotent boolean;
begin
  if auth.uid() is null or public.personal_current_user_is_active() is distinct from true then
    raise exception 'personal_auth_required';
  end if;
  if p_writer_secret is null or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
     or not exists (select 1 from public.personal_part4_sync_writer_credentials c where c.owner_user_id = auth.uid()
       and c.secret_sha256 = encode(extensions.digest(p_writer_secret,'sha256'),'hex')) then
    raise exception 'confirmed_dividends_trusted_writer_required';
  end if;
  if p_as_of is null or p_as_of !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$'
     or jsonb_typeof(p_records) is distinct from 'array' or octet_length(p_records::text) > 2000000 then
    raise exception 'confirmed_dividends_input_invalid';
  end if;
  begin v_as_of := p_as_of::timestamptz;
  exception when others then raise exception 'confirmed_dividends_as_of_invalid'; end;
  if not isfinite(v_as_of) or v_as_of < now() - interval '3 days' or v_as_of > now() + interval '5 minutes' then
    raise exception 'confirmed_dividends_as_of_out_of_range';
  end if;
  v_end := (v_as_of at time zone 'Asia/Shanghai')::date;
  -- PostgreSQL calendar-month arithmetic clips Feb29 to prior-year Feb28.
  v_start := (v_end - interval '12 months')::date;
  perform pg_advisory_xact_lock(hashtext('personal_snapshot:' || auth.uid()::text));
  lock table public.personal_watchlist_items in share mode;
  select count(*) into v_expected from public.personal_watchlist_items where owner_user_id = auth.uid();
  if v_expected not between 1 and 50 or jsonb_array_length(p_records) <> v_expected then
    raise exception 'confirmed_dividends_coverage_incomplete';
  end if;
  for v_item in select value from jsonb_array_elements(p_records) loop
    if jsonb_typeof(v_item) is distinct from 'object'
       or not (v_item ?& array['code','asOf','amount','status','reason','windowStart','windowEnd','components','source'])
       or v_item - array['code','asOf','amount','status','reason','windowStart','windowEnd','components','source'] <> '{}'::jsonb
       or jsonb_typeof(v_item->'code') is distinct from 'string'
       or v_item->'asOf' is distinct from to_jsonb(p_as_of)
       or v_item->'windowStart' is distinct from to_jsonb(to_char(v_start,'YYYY-MM-DD'))
       or v_item->'windowEnd' is distinct from to_jsonb(to_char(v_end,'YYYY-MM-DD'))
       or jsonb_typeof(v_item->'status') is distinct from 'string'
       or v_item->>'status' not in ('ready','missing','conflict')
       or v_item->'source' is distinct from '"eastmoney_public_implemented_a_share"'::jsonb
       or jsonb_typeof(v_item->'components') is distinct from 'array'
       or octet_length(v_item::text) > 200000 then
      raise exception 'confirmed_dividends_record_shape_invalid';
    end if;
    v_code := v_item->>'code';
    if v_code !~ '^[0-9]{6}$' or v_code = any(v_seen)
       or not exists (select 1 from public.personal_watchlist_items w where w.owner_user_id = auth.uid() and w.stock_code = v_code) then
      raise exception 'confirmed_dividends_record_identity_invalid';
    end if;
    v_seen := array_append(v_seen,v_code);
    if jsonb_array_length(v_item->'components') > 100 then raise exception 'confirmed_dividends_components_limit'; end if;
    v_sum := 0; v_components_seen := array[]::text[];
    for v_component in select value from jsonb_array_elements(v_item->'components') loop
      if jsonb_typeof(v_component) is distinct from 'object'
         or not (v_component ?& array['reportDate','paymentDate','amount','sourceUrl','plan'])
         or v_component - array['reportDate','paymentDate','amount','sourceUrl','plan'] <> '{}'::jsonb
         or jsonb_typeof(v_component->'reportDate') is distinct from 'string'
         or jsonb_typeof(v_component->'paymentDate') is distinct from 'string'
         or v_component->>'reportDate' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
         or v_component->>'paymentDate' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
         or jsonb_typeof(v_component->'amount') is distinct from 'number'
         or jsonb_typeof(v_component->'sourceUrl') is distinct from 'string'
         or length(v_component->>'sourceUrl') > 300
         or (v_component->>'sourceUrl' !~ ('^https://data\.eastmoney\.com/notices/detail/' || v_code || '/AN[0-9]+\.html$')
             and v_component->>'sourceUrl' <> 'https://data.eastmoney.com/yjfp/detail/' || v_code || '.html')
         or jsonb_typeof(v_component->'plan') is distinct from 'string' or length(v_component->>'plan') > 1200 then
        raise exception 'confirmed_dividends_component_shape_invalid';
      end if;
      begin
        v_report := (v_component->>'reportDate')::date;
        v_payment := (v_component->>'paymentDate')::date;
      exception when others then raise exception 'confirmed_dividends_component_date_invalid'; end;
      if not isfinite(v_report) or not isfinite(v_payment)
         or to_char(v_report,'YYYY-MM-DD') <> v_component->>'reportDate'
         or to_char(v_payment,'YYYY-MM-DD') <> v_component->>'paymentDate'
         or v_report < date '1900-01-01' or v_report > v_end or v_report > v_payment
         or v_payment <= v_start or v_payment > v_end then
        raise exception 'confirmed_dividends_component_date_invalid';
      end if;
      v_identity := v_component->>'reportDate' || ':' || (v_component->>'paymentDate');
      if v_identity = any(v_components_seen) then raise exception 'confirmed_dividends_duplicate_component'; end if;
      v_components_seen := array_append(v_components_seen,v_identity);
      v_amount := (v_component->>'amount')::numeric;
      if v_amount <= 0 or v_amount >= 1000000 or round(v_amount,12) <> v_amount then
        raise exception 'confirmed_dividends_component_amount_invalid';
      end if;
      v_sum := v_sum + v_amount;
    end loop;
    if v_item->>'status' = 'ready' then
      if jsonb_typeof(v_item->'amount') is distinct from 'number' or v_item->'reason' <> 'null'::jsonb then
        raise exception 'confirmed_dividends_total_invalid';
      end if;
      if (v_item->>'amount')::numeric <> v_sum or v_sum >= 1000000 then raise exception 'confirmed_dividends_total_invalid'; end if;
    elsif v_item->'amount' <> 'null'::jsonb or jsonb_typeof(v_item->'reason') is distinct from 'string'
       or v_item->>'reason' !~ '^[A-Za-z0-9._-]{1,120}$' then
      raise exception 'confirmed_dividends_unknown_invalid';
    end if;
  end loop;
  -- Include retained removed symbols when checking the owner's high-water mark.
  if exists (select 1 from public.personal_confirmed_dividend_snapshots s where s.owner_user_id = auth.uid() and s.as_of > v_as_of) then
    raise exception 'confirmed_dividends_time_regression';
  end if;
  if exists (select 1 from public.personal_confirmed_dividend_snapshots s join jsonb_array_elements(p_records) r on r->>'code' = s.stock_code
    where s.owner_user_id = auth.uid() and s.as_of = v_as_of and s.payload <> r - 'code') then
    raise exception 'confirmed_dividends_same_time_conflict';
  end if;
  select count(*) = v_expected into v_idempotent from public.personal_confirmed_dividend_snapshots s
    join jsonb_array_elements(p_records) r on r->>'code' = s.stock_code
    where s.owner_user_id = auth.uid() and s.as_of = v_as_of and s.payload = r - 'code';
  insert into public.personal_confirmed_dividend_snapshots(owner_user_id,stock_code,payload,as_of)
    select auth.uid(), value->>'code', value - 'code', v_as_of from jsonb_array_elements(p_records)
  on conflict (owner_user_id,stock_code) do update set payload = excluded.payload, as_of = excluded.as_of, updated_at = now()
    where personal_confirmed_dividend_snapshots.as_of <> excluded.as_of or personal_confirmed_dividend_snapshots.payload <> excluded.payload;
  return jsonb_build_object('stored',v_expected,'as_of',p_as_of,'idempotent',v_idempotent);
end;
$$;
revoke all on function public.personal_sync_confirmed_dividends(text,jsonb,text) from PUBLIC, anon, service_role;
grant execute on function public.personal_sync_confirmed_dividends(text,jsonb,text) to authenticated;

create or replace function public.personal_get_part4_v4()
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, public
as $$
declare v_base jsonb; v_stocks jsonb;
begin
  if auth.uid() is null or public.personal_current_user_is_active() is distinct from true then return null; end if;
  v_base := public.personal_get_part4_v3();
  if v_base is null then return null; end if;
  if jsonb_typeof(v_base->'stocks') is distinct from 'array' then raise exception 'confirmed_dividends_base_stocks_invalid'; end if;
  select coalesce(jsonb_agg(s.stock || jsonb_build_object('confirmedBasis',coalesce(c.payload,
      jsonb_build_object('amount',null,'status','missing','reason','not_collected','asOf',null,'windowStart',null,'windowEnd',null,'components','[]'::jsonb,'source',null))) order by s.ordinal),'[]'::jsonb)
    into v_stocks from jsonb_array_elements(v_base->'stocks') with ordinality s(stock,ordinal)
    left join public.personal_confirmed_dividend_snapshots c on c.owner_user_id = auth.uid() and c.stock_code = s.stock->>'code'
      and exists (select 1 from public.personal_watchlist_items w where w.owner_user_id = auth.uid() and w.stock_code = c.stock_code);
  return jsonb_set(v_base,'{stocks}',v_stocks,true);
end;
$$;
revoke all on function public.personal_get_part4_v4() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_part4_v4() to authenticated;

create table if not exists public.personal_refresh_health (
  owner_user_id uuid primary key references auth.users(id) on delete cascade,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  last_success_at timestamptz check (last_success_at is null or isfinite(last_success_at)),
  updated_at timestamptz not null default now()
);
do $guard$
begin
  if (select count(*) from pg_attribute where attrelid = 'public.personal_refresh_health'::regclass and attnum > 0 and not attisdropped) <> 4
     or exists (select 1 from (values ('owner_user_id','uuid',true),('payload','jsonb',true),('last_success_at','timestamp with time zone',false),('updated_at','timestamp with time zone',true)) e(name,typ,nn)
       where not exists (select 1 from pg_attribute a where a.attrelid = 'public.personal_refresh_health'::regclass and a.attname = e.name and a.attnotnull = e.nn and format_type(a.atttypid,a.atttypmod) = e.typ))
     or (select count(*) from pg_constraint where conrelid = 'public.personal_refresh_health'::regclass and contype in ('c','p','f')) <> 4
     or exists (select 1 from (values
       ($d$CHECK ((jsonb_typeof(payload) = 'object'::text))$d$),
       ($d$CHECK (((last_success_at IS NULL) OR isfinite(last_success_at)))$d$),
       ('PRIMARY KEY (owner_user_id)'),
       ('FOREIGN KEY (owner_user_id) REFERENCES auth.users(id) ON DELETE CASCADE')) e(def)
       where not exists (select 1 from pg_constraint c where c.conrelid = 'public.personal_refresh_health'::regclass and c.convalidated and pg_get_constraintdef(c.oid) = e.def))
     or exists (select 1 from pg_policy where polrelid = 'public.personal_refresh_health'::regclass)
     or exists (select 1 from pg_trigger where tgrelid = 'public.personal_refresh_health'::regclass and not tgisinternal) then
    raise exception 'refresh_health_existing_schema_incompatible';
  end if;
end;
$guard$;
alter table public.personal_refresh_health enable row level security;
revoke all on table public.personal_refresh_health from PUBLIC, anon, authenticated, service_role;

create or replace function public.personal_sync_refresh_health(p_run_id text, p_health jsonb, p_writer_secret text)
returns jsonb language plpgsql volatile security definer
set search_path = pg_catalog, public
as $$
declare
  v_started timestamptz;
  v_finished timestamptz;
  v_target date;
  v_key text;
  v_stage jsonb;
  v_all_ok boolean := true;
  v_old public.personal_refresh_health%rowtype;
  v_time_pattern constant text := '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$';
begin
  if auth.uid() is null or public.personal_current_user_is_active() is distinct from true then
    raise exception 'personal_auth_required';
  end if;
  if p_writer_secret is null or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
     or not exists (select 1 from public.personal_part4_sync_writer_credentials c where c.owner_user_id = auth.uid()
       and c.secret_sha256 = encode(extensions.digest(p_writer_secret,'sha256'),'hex')) then
    raise exception 'refresh_health_trusted_writer_required';
  end if;
  if p_run_id is null or p_run_id !~ '^[A-Za-z0-9._-]{1,160}$'
     or jsonb_typeof(p_health) is distinct from 'object' or octet_length(p_health::text) > 16000
     or not (p_health ?& array['schemaVersion','runId','sourceRelease','status','targetDate','startedAt','finishedAt','stages'])
     or p_health - array['schemaVersion','runId','sourceRelease','status','targetDate','startedAt','finishedAt','stages'] <> '{}'::jsonb
     or p_health->'schemaVersion' is distinct from '1'::jsonb
     or p_health->'runId' is distinct from to_jsonb(p_run_id)
     or jsonb_typeof(p_health->'sourceRelease') is distinct from 'string'
     or p_health->>'sourceRelease' !~ '^[A-Za-z0-9._-]{1,100}$'
     or jsonb_typeof(p_health->'status') is distinct from 'string' or p_health->>'status' not in ('running','ok','partial','error')
     or jsonb_typeof(p_health->'targetDate') is distinct from 'string' or p_health->>'targetDate' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
     or jsonb_typeof(p_health->'startedAt') is distinct from 'string' or p_health->>'startedAt' !~ v_time_pattern
     or jsonb_typeof(p_health->'stages') is distinct from 'object'
     or not (p_health->'stages' ?& array['notices','quotes','technical','news','forward','recommendations'])
     or (p_health->'stages') - array['notices','quotes','technical','news','forward','recommendations'] <> '{}'::jsonb then
    raise exception 'refresh_health_shape_invalid';
  end if;
  begin
    v_started := (p_health->>'startedAt')::timestamptz;
    v_target := (p_health->>'targetDate')::date;
  exception when others then raise exception 'refresh_health_time_invalid'; end;
  if not isfinite(v_started) or not isfinite(v_target)
     or v_started < now() - interval '3 days' or v_started > now() + interval '5 minutes'
     or to_char(v_target,'YYYY-MM-DD') <> p_health->>'targetDate'
     or v_target > (now() at time zone 'Asia/Shanghai')::date
     or v_target < (now() at time zone 'Asia/Shanghai')::date - 30 then
    raise exception 'refresh_health_time_out_of_range';
  end if;
  if p_health->>'status' = 'running' then
    if p_health->'finishedAt' <> 'null'::jsonb then raise exception 'refresh_health_finished_invalid'; end if;
  else
    if jsonb_typeof(p_health->'finishedAt') is distinct from 'string' or p_health->>'finishedAt' !~ v_time_pattern then
      raise exception 'refresh_health_finished_invalid';
    end if;
    begin v_finished := (p_health->>'finishedAt')::timestamptz;
    exception when others then raise exception 'refresh_health_finished_invalid'; end;
    if not isfinite(v_finished) or v_finished < v_started or v_finished > now() + interval '5 minutes'
       or v_finished > v_started + interval '3 days' then raise exception 'refresh_health_finished_out_of_range'; end if;
  end if;
  foreach v_key in array array['notices','quotes','technical','news','forward','recommendations'] loop
    v_stage := p_health->'stages'->v_key;
    if jsonb_typeof(v_stage) is distinct from 'object'
       or not (v_stage ?& array['status','category','published','source'])
       or v_stage - array['status','category','published','source'] <> '{}'::jsonb
       or jsonb_typeof(v_stage->'status') is distinct from 'string' or v_stage->>'status' not in ('pending','running','ok','error','skipped')
       or jsonb_typeof(v_stage->'category') is distinct from 'string'
       or v_stage->>'category' not in ('pending','running','complete','fallback_used','rate_limited','timeout','empty_response','auth_failed','provider_failed','invalid_data','missing_contract','write_failed','readback_failed','unknown_error','dependency_failed')
       or jsonb_typeof(v_stage->'published') is distinct from 'boolean'
       or (v_stage->'source' <> 'null'::jsonb and (jsonb_typeof(v_stage->'source') is distinct from 'string'
         or v_stage->>'source' not in ('hithink_snapshot','eastmoney_snapshot','hithink_daily','eastmoney_public','legacy_public_daily','public_company_notice_index'))) then
      raise exception 'refresh_health_stage_invalid';
    end if;
    if v_stage->>'status' <> 'ok' or v_stage->'published' <> 'true'::jsonb then v_all_ok := false; end if;
  end loop;
  if p_health->>'status' = 'ok' and not v_all_ok then raise exception 'refresh_health_false_success'; end if;
  -- Serializes even the first insert; row lock alone cannot lock a missing row.
  perform pg_advisory_xact_lock(hashtext('personal_refresh_health:' || auth.uid()::text));
  select * into v_old from public.personal_refresh_health where owner_user_id = auth.uid() for update;
  if found then
    if v_old.payload->>'runId' = p_run_id then
      if v_old.payload->'startedAt' <> p_health->'startedAt' or v_old.payload->'targetDate' <> p_health->'targetDate'
         or v_old.payload->'sourceRelease' <> p_health->'sourceRelease' then
        raise exception 'refresh_health_run_identity_immutable';
      end if;
      if v_old.payload = p_health then return jsonb_build_object('runId',p_run_id,'status',p_health->>'status','idempotent',true); end if;
      if v_old.payload->>'status' <> 'running' then raise exception 'refresh_health_final_run_immutable'; end if;
    elsif v_started <= (v_old.payload->>'startedAt')::timestamptz then
      raise exception 'refresh_health_previous_run_rejected';
    end if;
    if p_health->>'status' = 'ok' and v_finished < v_old.last_success_at then raise exception 'refresh_health_success_time_regression'; end if;
  end if;
  insert into public.personal_refresh_health(owner_user_id,payload,last_success_at)
    values(auth.uid(),p_health,case when p_health->>'status' = 'ok' then v_finished else null end)
  on conflict (owner_user_id) do update set payload = excluded.payload,
    last_success_at = case when p_health->>'status' = 'ok' then v_finished else personal_refresh_health.last_success_at end,
    updated_at = now();
  return jsonb_build_object('runId',p_run_id,'status',p_health->>'status','idempotent',false);
end;
$$;
revoke all on function public.personal_sync_refresh_health(text,jsonb,text) from PUBLIC, anon, service_role;
grant execute on function public.personal_sync_refresh_health(text,jsonb,text) to authenticated;

create or replace function public.personal_get_refresh_health()
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, public
as $$
declare v_health public.personal_refresh_health%rowtype;
begin
  if auth.uid() is null or public.personal_current_user_is_active() is distinct from true then return null; end if;
  select * into v_health from public.personal_refresh_health where owner_user_id = auth.uid();
  if not found then return jsonb_build_object('status','missing','lastSuccessAt',null,'updatedAt',null); end if;
  return v_health.payload || jsonb_build_object(
    'lastSuccessAt',to_char(v_health.last_success_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'updatedAt',to_char(v_health.updated_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
end;
$$;
revoke all on function public.personal_get_refresh_health() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_refresh_health() to authenticated;

comment on function public.personal_sync_confirmed_dividends(text,jsonb,text) is
  'Active owner + existing Part4 trusted writer; complete locked watchlist; implemented A-share RMB/share calendar trailing-12-month basis. No legacy document mutation.';
comment on function public.personal_get_part4_v4() is
  'v3 plus separate confirmedBasis only; preserves forwardBasis, quotes, technical and original dates; uncollected is missing/null, never zero.';
comment on function public.personal_sync_refresh_health(text,jsonb,text) is
  'Sanitized deterministic refresh health, not AI health. Latest run is monotonic; terminal immutable; only all-six-published final ok advances last success.';
comment on function public.personal_get_refresh_health() is
  'Owner-only flat health payload with lastSuccessAt and updatedAt; missing explicit; no writer capability in browser.';
notify pgrst, 'reload schema';
commit;
