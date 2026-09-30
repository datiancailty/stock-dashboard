-- Part4 dividend-date ledger. Forward-only; USER MANUAL SQL execution only.
-- No business import or writer invocation. Apply only after the reviewed preflight.
begin;

-- Bind this forward migration to the user's 13/0/[] preflight, not a renamed
-- getter copy or an assumed historical migration state. No business payloads.
do $guard$
declare
  v record;
  v_oid oid;
  v_proc pg_proc%rowtype;
  v_class pg_class%rowtype;
begin
  if current_user <> 'postgres'
     or to_regprocedure('public.personal_current_user_is_active()') is null
     or to_regprocedure('extensions.digest(text,text)') is null then
    raise exception 'part4_dates_preflight_prerequisite';
  end if;
  if to_regclass('public.personal_part4_dividend_date_events') is not null
     or to_regclass('public.personal_part4_dividend_date_sync_state') is not null
     or exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                 where n.nspname = 'public' and p.proname = 'personal_sync_part4_dividend_dates') then
    raise exception 'part4_dates_preflight_new_objects_exist';
  end if;
  for v in select * from (values
    ('public.personal_get_part4()', 'c8243f342412d9dd30946dca83236a2c', 's', 'sql'),
    ('public.personal_get_part4_v4()', 'd1b5d9e8497ed45ad1314097cf547404', 's', 'plpgsql'),
    ('public.personal_sync_part4_dividend_notices(text,text,text,integer,integer,integer,jsonb,text,text,jsonb)', 'a38d4203f46c1e3e15f9da345d3f6f50', 'v', 'plpgsql')
  ) e(signature, source_md5, volatility, language_name) loop
    v_oid := to_regprocedure(v.signature)::oid;
    if v_oid is null then raise exception 'part4_dates_preflight_function_missing'; end if;
    select p.* into strict v_proc from pg_proc p where p.oid = v_oid;
    if md5(v_proc.prosrc) <> v.source_md5 or pg_get_userbyid(v_proc.proowner) <> 'postgres'
       or not v_proc.prosecdef or v_proc.proretset or v_proc.prorettype <> 'jsonb'::regtype
       or v_proc.provolatile::text <> v.volatility
       or v_proc.proconfig is distinct from array['search_path=pg_catalog, public']::text[]
       or (select lanname from pg_language where oid = v_proc.prolang) <> v.language_name
       or not has_function_privilege('authenticated', v_oid, 'EXECUTE')
       or has_function_privilege('anon', v_oid, 'EXECUTE')
       or has_function_privilege('service_role', v_oid, 'EXECUTE')
       or exists (select 1 from aclexplode(coalesce(v_proc.proacl, acldefault('f', v_proc.proowner))) a
         where a.grantee not in (v_proc.proowner, 'authenticated'::regrole::oid)
            or (a.grantee <> v_proc.proowner and a.is_grantable)) then
      raise exception 'part4_dates_preflight_function_drift';
    end if;
  end loop;
  for v in select * from (values
    ('personal_part4_dividend_notices', true, 'PRIMARY KEY (owner_user_id, source_id)'),
    ('personal_part4_dividend_notice_sync_runs', true, 'PRIMARY KEY (owner_user_id, run_id)'),
    ('personal_part4_dividend_notice_run_items', true, 'PRIMARY KEY (owner_user_id, run_id, source_id)'),
    ('personal_part4_sync_writer_credentials', true, 'PRIMARY KEY (owner_user_id)'),
    ('personal_watchlist_items', false, 'PRIMARY KEY (owner_user_id, source_id)'),
    ('personal_documents', false, 'PRIMARY KEY (owner_user_id, document_key)')
  ) e(name, rpc_only, primary_key) loop
    v_oid := to_regclass('public.' || v.name)::oid;
    if v_oid is null then raise exception 'part4_dates_preflight_table_missing'; end if;
    select c.* into strict v_class from pg_class c where c.oid = v_oid;
    if v_class.relkind <> 'r' or not v_class.relrowsecurity
       or pg_get_userbyid(v_class.relowner) <> 'postgres'
       or not exists (select 1 from pg_constraint k where k.conrelid = v_oid and k.contype = 'p'
                       and k.convalidated and pg_get_constraintdef(k.oid) = v.primary_key) then
      raise exception 'part4_dates_preflight_table_drift';
    end if;
    if v.rpc_only and (
      has_table_privilege('anon', v_oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      or has_table_privilege('authenticated', v_oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      or has_table_privilege('service_role', v_oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      or exists (select 1 from aclexplode(coalesce(v_class.relacl, acldefault('r', v_class.relowner))) a
                  where a.grantee <> v_class.relowner)
      or exists (select 1 from pg_attribute a,
        lateral aclexplode(a.attacl) x where a.attrelid = v_oid and x.grantee <> v_class.relowner)
    ) then raise exception 'part4_dates_preflight_table_acl_drift'; end if;
  end loop;
end;
$guard$;

create table public.personal_part4_dividend_date_events (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  source_id text not null,
  stock_code text not null check (stock_code ~ '^[0-9]{6}$'),
  event_date date not null,
  notice_id text not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  observed_at timestamptz not null check (isfinite(observed_at)),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, source_id),
  foreign key (owner_user_id, notice_id)
    references public.personal_part4_dividend_notices(owner_user_id, source_id)
);
create index personal_part4_dividend_date_events_owner_date_idx
  on public.personal_part4_dividend_date_events(owner_user_id, event_date, stock_code, source_id);
alter table public.personal_part4_dividend_date_events enable row level security;
revoke all on table public.personal_part4_dividend_date_events from PUBLIC, anon, authenticated, service_role;

-- One owner watermark is necessary even for an empty batch: otherwise an empty
-- successful run permits time regression or a different payload at the same time.
create table public.personal_part4_dividend_date_sync_state (
  owner_user_id uuid primary key references auth.users(id) on delete cascade,
  run_id text not null,
  observed_at timestamptz not null check (isfinite(observed_at)),
  request_sha256 text not null check (request_sha256 ~ '^[0-9a-f]{64}$'),
  event_count integer not null check (event_count between 0 and 1500),
  updated_at timestamptz not null default now(),
  foreign key (owner_user_id, run_id)
    references public.personal_part4_dividend_notice_sync_runs(owner_user_id, run_id)
);
alter table public.personal_part4_dividend_date_sync_state enable row level security;
revoke all on table public.personal_part4_dividend_date_sync_state from PUBLIC, anon, authenticated, service_role;

create function public.personal_sync_part4_dividend_dates(
  p_run_id text, p_as_of text, p_events jsonb, p_watchlist_codes jsonb, p_writer_secret text
)
returns jsonb language plpgsql volatile security definer
set search_path = pg_catalog, public
as $$
declare
  v_owner uuid := auth.uid();
  v_as_of timestamptz;
  v_codes text[];
  v_current_codes text[];
  v_run public.personal_part4_dividend_notice_sync_runs%rowtype;
  v_item jsonb;
  v_day date;
  v_notice_day date;
  v_report_day date;
  v_kind text;
  v_label text;
  v_period_key text;
  v_seen text[] := array[]::text[];
  v_request_hash text;
  v_previous public.personal_part4_dividend_date_sync_state%rowtype;
begin
  if v_owner is null or public.personal_current_user_is_active() is distinct from true then
    raise exception 'personal_auth_required';
  end if;
  if p_writer_secret is null or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
     or not exists (select 1 from public.personal_part4_sync_writer_credentials c
       where c.owner_user_id = v_owner
         and c.secret_sha256 = encode(extensions.digest(p_writer_secret, 'sha256'), 'hex')) then
    raise exception 'part4_dates_trusted_writer_required';
  end if;
  if p_run_id is null or p_run_id !~ '^[A-Za-z0-9._:-]{1,160}$' then
    raise exception 'part4_dates_run_invalid';
  end if;
  if p_as_of is null or p_as_of !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?(Z|[+-](0[0-9]|1[0-4]):[0-5][0-9])$' then
    raise exception 'part4_dates_as_of_invalid';
  end if;
  begin
    v_as_of := p_as_of::timestamptz;
  exception when others then
    raise exception 'part4_dates_as_of_invalid';
  end;
  if not isfinite(v_as_of) or v_as_of < now() - interval '3 days'
     or v_as_of > now() + interval '5 minutes' then
    raise exception 'part4_dates_as_of_out_of_range';
  end if;
  if jsonb_typeof(p_events) is distinct from 'array' then
    raise exception 'part4_dates_events_invalid';
  end if;
  if jsonb_array_length(p_events) > 1500 or octet_length(p_events::text) > 4194304 then
    raise exception 'part4_dates_events_too_large';
  end if;
  if jsonb_typeof(p_watchlist_codes) is distinct from 'array' then
    raise exception 'part4_dates_watchlist_invalid';
  end if;
  if jsonb_array_length(p_watchlist_codes) not between 1 and 50
     or exists (select 1 from jsonb_array_elements(p_watchlist_codes) c
                where jsonb_typeof(c) is distinct from 'string' or c #>> '{}' !~ '^[0-9]{6}$') then
    raise exception 'part4_dates_watchlist_invalid';
  end if;
  select array_agg(c #>> '{}' order by c #>> '{}') into v_codes
    from jsonb_array_elements(p_watchlist_codes) c;
  if (select count(distinct c) from unnest(v_codes) c) <> cardinality(v_codes) then
    raise exception 'part4_dates_watchlist_invalid';
  end if;
  perform pg_advisory_xact_lock(hashtext('personal_snapshot:' || v_owner::text));
  select array_agg(w.stock_code order by w.stock_code) into v_current_codes
    from public.personal_watchlist_items w where w.owner_user_id = v_owner;
  if v_codes is distinct from v_current_codes then
    raise exception 'part4_dates_watchlist_changed';
  end if;
  -- Existing legacy writer issues the receipt. Lock it rather than inventing or
  -- mutating a run here; old writer does not itself use the snapshot advisory lock.
  select r.* into v_run from public.personal_part4_dividend_notice_sync_runs r
    where r.owner_user_id = v_owner and r.run_id = p_run_id for share;
  if not found or v_run.expected_watchlist_count <> cardinality(v_codes)
     or v_run.scanned_watchlist_count <> cardinality(v_codes)
     or v_run.updated_at < now() - interval '3 days'
     or v_run.updated_at > now() + interval '5 minutes'
     or v_run.source_label <> 'eastmoney_official_announcement_api'
     or v_run.selected_notice_count <> (
       select count(*) from public.personal_part4_dividend_notice_run_items i
        where i.owner_user_id = v_owner and i.run_id = p_run_id) then
    raise exception 'part4_dates_notice_run_invalid';
  end if;
  for v_item in select value from jsonb_array_elements(p_events) loop
    if jsonb_typeof(v_item) is distinct from 'object' then
      raise exception 'part4_dates_event_shape_invalid';
    end if;
    if not (v_item ?& array['id','date','code','name','type','stage','title','description','source','sourceUrl','sourceHash','noticeId','noticeDate','reportDate','reportPeriod','dateField'])
       or (select count(*) from jsonb_object_keys(v_item)) <> 16
       or exists (select 1 from jsonb_each(v_item) e
         where (e.key <> 'reportDate' and jsonb_typeof(e.value) is distinct from 'string')
            or (e.key = 'reportDate' and jsonb_typeof(e.value) not in ('null','string')))
       or octet_length(v_item::text) > 8192 then
      raise exception 'part4_dates_event_shape_invalid';
    end if;
    if v_item->>'code' !~ '^[0-9]{6}$'
       or v_item->>'noticeId' !~ '^eastmoney:AN[0-9]{12,32}$'
       or v_item->>'sourceHash' !~ '^[0-9a-f]{64}$'
       or v_item->>'source' <> '东方财富公司公告 + F10分红日期核对'
       or v_item->>'stage' <> 'implementation'
       or char_length(v_item->>'name') not between 1 and 80
       or char_length(v_item->>'title') not between 1 and 300
       or char_length(v_item->>'description') not between 1 and 450
       or v_item->>'reportPeriod' !~ '^[0-9]{4}[^[:space:]]{1,76}$'
       or exists (select 1 from jsonb_each_text(v_item) e
                   where e.value <> btrim(e.value) or e.value ~ '[[:cntrl:]]') then
      raise exception 'part4_dates_event_value_invalid';
    end if;
    v_kind := case v_item->>'dateField' when 'EQUITY_RECORD_DATE' then 'registration'
      when 'EX_DIVIDEND_DATE' then 'ex_dividend' when 'PAY_CASH_DATE' then 'payment' end;
    v_label := case v_kind when 'registration' then '股权登记日'
      when 'ex_dividend' then '除权除息日' when 'payment' then '派息日' end;
    if v_kind is null or v_item->>'type' <> v_label then
      raise exception 'part4_dates_event_mapping_invalid';
    end if;
    if v_item->>'date' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
       or v_item->>'noticeDate' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
      raise exception 'part4_dates_event_date_invalid';
    end if;
    begin
      v_day := (v_item->>'date')::date;
      v_notice_day := (v_item->>'noticeDate')::date;
      v_report_day := (v_item->>'reportDate')::date;
    exception when others then
      raise exception 'part4_dates_event_date_invalid';
    end;
    if to_char(v_day, 'YYYY-MM-DD') <> v_item->>'date'
       or to_char(v_notice_day, 'YYYY-MM-DD') <> v_item->>'noticeDate'
       or v_day < v_notice_day then
      raise exception 'part4_dates_event_date_invalid';
    end if;
    if v_item->'reportDate' = 'null'::jsonb then
      if v_item->>'reportPeriod' !~ '^[0-9]{4}特别分配$' then
        raise exception 'part4_dates_report_period_invalid';
      end if;
      v_period_key := 'period-' || left(encode(extensions.digest(v_item->>'reportPeriod', 'sha256'), 'hex'), 16);
    else
      if v_item->>'reportDate' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
         or to_char(v_report_day, 'YYYY-MM-DD') <> v_item->>'reportDate'
         or left(v_item->>'reportPeriod', 4) <> left(v_item->>'reportDate', 4)
         or v_item->>'reportPeriod' ~ '^[0-9]{4}特别分配$' then
        raise exception 'part4_dates_report_period_invalid';
      end if;
      v_period_key := v_item->>'reportDate';
    end if;
    if v_item->>'id' <> 'eastmoney-date:' || substr(v_item->>'noticeId', 11) || ':' ||
         (v_item->>'code') || ':' || v_period_key || ':' || v_kind
       or v_item->>'id' = any(v_seen) then
      raise exception 'part4_dates_event_id_invalid';
    end if;
    v_seen := array_append(v_seen, v_item->>'id');
    perform 1 from public.personal_part4_dividend_notices n
      join public.personal_part4_dividend_notice_run_items i
        on i.owner_user_id = n.owner_user_id and i.source_id = n.source_id and i.run_id = p_run_id
      join public.personal_watchlist_items w
        on w.owner_user_id = n.owner_user_id and w.stock_code = n.stock_code
      where n.owner_user_id = v_owner and n.source_id = v_item->>'noticeId'
        and n.stock_code = v_item->>'code' and w.display_name = v_item->>'name'
        and n.archived_at is null and n.stage = 'implementation'
        and n.source_label = '东方财富公司公告'
        and n.title = v_item->>'title' and n.source_url = v_item->>'sourceUrl'
        and n.event_date = v_notice_day
        and n.payload->>'id' = n.source_id and n.payload->>'code' = n.stock_code
        and n.payload->>'stage' = n.stage and n.payload->>'title' = n.title
        and n.payload->>'date' = v_item->>'noticeDate'
        and n.payload->>'sourceUrl' = n.source_url
      for share of n, i;
    if not found then raise exception 'part4_dates_notice_binding_invalid'; end if;
  end loop;
  if exists (
    with effective as (
      select e as item from jsonb_array_elements(p_events) e
      union all
      select d.payload from public.personal_part4_dividend_date_events d
       where d.owner_user_id = v_owner and not (d.source_id = any(v_seen))
    ), periods as (
      select regexp_replace(item->>'id', ':[^:]+$', '') as identity,
        count(distinct item->>'reportPeriod') as period_count,
        max((item->>'date')::date) filter (where item->>'dateField' = 'EQUITY_RECORD_DATE') as registration,
        max((item->>'date')::date) filter (where item->>'dateField' = 'EX_DIVIDEND_DATE') as ex_dividend,
        max((item->>'date')::date) filter (where item->>'dateField' = 'PAY_CASH_DATE') as payment
      from effective group by 1
    ) select 1 from periods where period_count <> 1
       or registration > ex_dividend or registration > payment or ex_dividend > payment
  ) then raise exception 'part4_dates_event_order_invalid'; end if;
  select encode(extensions.digest(jsonb_build_object(
    'runId', p_run_id, 'codes', to_jsonb(v_codes),
    'events', (select coalesce(jsonb_agg(e order by e->>'id'), '[]'::jsonb) from jsonb_array_elements(p_events) e)
  )::text, 'sha256'), 'hex') into v_request_hash;
  select s.* into v_previous from public.personal_part4_dividend_date_sync_state s
    where s.owner_user_id = v_owner for update;
  if found then
    if v_as_of < v_previous.observed_at then raise exception 'part4_dates_time_regression'; end if;
    if v_as_of = v_previous.observed_at then
      if v_request_hash <> v_previous.request_sha256 then raise exception 'part4_dates_same_time_conflict'; end if;
      return jsonb_build_object('stored', jsonb_array_length(p_events), 'as_of', v_as_of::text, 'idempotent', true);
    end if;
  end if;
  insert into public.personal_part4_dividend_date_events (
    owner_user_id, source_id, stock_code, event_date, notice_id, payload, observed_at
  ) select v_owner, e->>'id', e->>'code', (e->>'date')::date, e->>'noticeId', e, v_as_of
      from jsonb_array_elements(p_events) e
  on conflict (owner_user_id, source_id) do update set
    stock_code = excluded.stock_code, event_date = excluded.event_date,
    notice_id = excluded.notice_id, payload = excluded.payload,
    observed_at = excluded.observed_at, updated_at = now();
  insert into public.personal_part4_dividend_date_sync_state (
    owner_user_id, run_id, observed_at, request_sha256, event_count
  ) values (v_owner, p_run_id, v_as_of, v_request_hash, jsonb_array_length(p_events))
  on conflict (owner_user_id) do update set run_id = excluded.run_id,
    observed_at = excluded.observed_at, request_sha256 = excluded.request_sha256,
    event_count = excluded.event_count, updated_at = now();
  return jsonb_build_object('stored', jsonb_array_length(p_events), 'as_of', v_as_of::text, 'idempotent', false);
end;
$$;
revoke all on function public.personal_sync_part4_dividend_dates(text,text,jsonb,jsonb,text) from PUBLIC, anon, service_role;
grant execute on function public.personal_sync_part4_dividend_dates(text,text,jsonb,jsonb,text) to authenticated;

-- Original getter logic retained verbatim in base/notices/legacy; append dates only.
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
  , legacy as (select case
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
  end as payload), date_payload as (
    select coalesce(jsonb_agg(e.payload order by e.event_date, e.stock_code, e.source_id), '[]'::jsonb) as events,
           max(e.observed_at)::text as updated_at, count(*) as event_count
      from public.personal_part4_dividend_date_events e
      join public.personal_watchlist_items w
        on w.owner_user_id = e.owner_user_id and w.stock_code = e.stock_code
      join public.personal_part4_dividend_notices n
        on n.owner_user_id = e.owner_user_id and n.source_id = e.notice_id
       and n.stock_code = e.stock_code and n.archived_at is null
       and n.stage = 'implementation'
     where e.owner_user_id = auth.uid()
  )
  select case when l.payload is null then null
    when d.event_count = 0 then l.payload
    else jsonb_set(jsonb_set(l.payload, '{events}',
      coalesce(l.payload->'events', '[]'::jsonb) || d.events, true),
      '{calendarDateUpdatedAt}', to_jsonb(d.updated_at), true)
    end from legacy l cross join date_payload d;
$$;
revoke all on function public.personal_get_part4() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_part4() to authenticated;

notify pgrst, 'reload schema';
commit;
