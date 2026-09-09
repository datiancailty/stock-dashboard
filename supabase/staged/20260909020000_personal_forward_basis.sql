-- STAGED ONLY. Forward-only, separate Part 3 storage; do not run automatically.
-- Prerequisites verified structurally from the supplied Hosted CSV: auth.uid(),
-- personal_current_user_is_active(), personal_watchlist_items(stock_code),
-- personal_part4_sync_writer_credentials and the existing personal_get_part4().
-- This does NOT replace any legacy getter/writer or touch formal market fields.
begin;

create table if not exists public.personal_forward_basis_snapshots (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  stock_code text not null check (stock_code ~ '^[0-9]{6}$'),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  as_of timestamptz not null check (isfinite(as_of)),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, stock_code)
);
-- Idempotent on this contract only; never silently adopt an incompatible table.
do $guard$
begin
  if (select count(*) from pg_attribute where attrelid = 'public.personal_forward_basis_snapshots'::regclass
      and attnum > 0 and not attisdropped) <> 5
     or exists (select 1 from (values
        ('owner_user_id','uuid'),('stock_code','text'),('payload','jsonb'),
        ('as_of','timestamp with time zone'),('updated_at','timestamp with time zone')) e(name,typ)
       where not exists (select 1 from pg_attribute a
         where a.attrelid = 'public.personal_forward_basis_snapshots'::regclass and a.attname = e.name
           and a.attnotnull and format_type(a.atttypid,a.atttypmod) = e.typ))
     or (select count(*) from pg_constraint where conrelid = 'public.personal_forward_basis_snapshots'::regclass and contype in ('c','p','f')) <> 5
     or not exists (select 1 from pg_constraint where conrelid = 'public.personal_forward_basis_snapshots'::regclass
       and contype = 'p' and pg_get_constraintdef(oid) = 'PRIMARY KEY (owner_user_id, stock_code)')
     or not exists (select 1 from pg_constraint where conrelid = 'public.personal_forward_basis_snapshots'::regclass
       and contype = 'f' and confrelid = 'auth.users'::regclass and confdeltype = 'c') then
    raise exception 'forward_basis_existing_schema_incompatible';
  end if;
end;
$guard$;
alter table public.personal_forward_basis_snapshots enable row level security;
revoke all on table public.personal_forward_basis_snapshots from PUBLIC, anon, authenticated, service_role;

create or replace function public.personal_sync_forward_basis(p_as_of text, p_records jsonb, p_writer_secret text)
returns jsonb language plpgsql volatile security definer
set search_path = pg_catalog, public
as $$
declare
  v_as_of timestamptz;
  v_expected integer;
  v_item jsonb;
  v_component jsonb;
  v_source jsonb;
  v_code text;
  v_kind text;
  v_status text;
  v_component_status text;
  v_seen text[] := array[]::text[];
  v_sum numeric;
  v_amount numeric;
  v_usable integer;
  v_failures text[];
  v_derived text;
  v_published text;
  v_time timestamptz;
  v_source_keys text[] := array['dto_title','header','raw_plan','raw_progress','raw_pretax','dto_code',
    'field','name_map','published_at','normalization_reason','official_notice','scope_evidence','row_index','distribution_dates'];
begin
  if auth.uid() is null or public.personal_current_user_is_active() is distinct from true then
    raise exception 'personal_auth_required';
  end if;
  if p_writer_secret is null or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
     or not exists (select 1 from public.personal_part4_sync_writer_credentials c
       where c.owner_user_id = auth.uid()
         and c.secret_sha256 = encode(extensions.digest(p_writer_secret, 'sha256'), 'hex')) then
    raise exception 'forward_basis_trusted_writer_required';
  end if;
  if p_as_of is null or p_as_of !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$'
     or jsonb_typeof(p_records) is distinct from 'array' or octet_length(p_records::text) > 2000000 then
    raise exception 'forward_basis_input_invalid';
  end if;
  begin
    v_as_of := p_as_of::timestamptz;
  exception when others then raise exception 'forward_basis_as_of_invalid';
  end;
  if not isfinite(v_as_of) or v_as_of < now() - interval '3 days' or v_as_of > now() + interval '5 minutes' then
    raise exception 'forward_basis_as_of_out_of_range';
  end if;
  -- Coordinate snapshot writers; the table lock ALSO covers watchlist writers
  -- which omit the advisory lock, including delete/reinsert and empty lists.
  perform pg_advisory_xact_lock(hashtext('personal_snapshot:' || auth.uid()::text));
  lock table public.personal_watchlist_items in share mode;
  select count(*) into v_expected from public.personal_watchlist_items where owner_user_id = auth.uid();
  if v_expected not between 1 and 50 or jsonb_array_length(p_records) <> v_expected then
    raise exception 'forward_basis_coverage_incomplete';
  end if;
  for v_item in select value from jsonb_array_elements(p_records) loop
    if jsonb_typeof(v_item) is distinct from 'object'
       or not (v_item ?& array['code','amount','status','reason','components','asOf'])
       or v_item - array['code','amount','status','reason','components','asOf'] <> '{}'::jsonb
       or jsonb_typeof(v_item->'code') is distinct from 'string'
       or jsonb_typeof(v_item->'status') is distinct from 'string'
       or jsonb_typeof(v_item->'asOf') is distinct from 'string'
       or v_item->>'asOf' <> p_as_of then
      raise exception 'forward_basis_record_shape_invalid';
    end if;
    v_code := v_item->>'code';
    v_status := v_item->>'status';
    if v_code !~ '^[0-9]{6}$' or v_code = any(v_seen)
       or not exists (select 1 from public.personal_watchlist_items w where w.owner_user_id = auth.uid() and w.stock_code = v_code)
       or v_status not in ('ready','missing','ambiguous','conflict','negated') then
      raise exception 'forward_basis_record_invalid';
    end if;
    v_seen := array_append(v_seen, v_code);
    if jsonb_typeof(v_item->'components') is distinct from 'object'
       or not (v_item->'components' ?& array['annual','interim'])
       or (v_item->'components') - array['annual','interim'] <> '{}'::jsonb then
      raise exception 'forward_basis_components_invalid';
    end if;
    v_sum := 0; v_usable := 0; v_failures := array[]::text[];
    foreach v_kind in array array['annual','interim'] loop
      v_component := v_item->'components'->v_kind;
      if jsonb_typeof(v_component) is distinct from 'object'
         or not (v_component ?& array['year','kind','amount','status','reason','amount_scope','published_at','source','evidence'])
         or v_component - array['year','kind','amount','status','reason','amount_scope','published_at','source','evidence'] <> '{}'::jsonb
         or v_component->>'kind' is distinct from v_kind
         or jsonb_typeof(v_component->'status') is distinct from 'string'
         or jsonb_typeof(v_component->'amount_scope') is distinct from 'string'
         or v_component->>'amount_scope' not in ('distribution','full_year','unknown')
         or jsonb_typeof(v_component->'evidence') is distinct from 'array' then
        raise exception 'forward_basis_component_shape_invalid';
      end if;
      v_component_status := v_component->>'status';
      if v_component_status not in ('implemented','announced','no_distribution','missing','ambiguous','conflict','negated')
         or jsonb_array_length(v_component->'evidence') > 200 then
        raise exception 'forward_basis_component_invalid';
      end if;
      if v_component->'year' <> 'null'::jsonb and
         (jsonb_typeof(v_component->'year') <> 'number' or (v_component->>'year') !~ '^[0-9]{4}$'
          or (v_component->>'year')::integer not between 1900 and 9999) then
        raise exception 'forward_basis_fiscal_year_invalid';
      end if;
      v_published := v_component->>'published_at';
      if v_published is not null then
        if jsonb_typeof(v_component->'published_at') <> 'string' then
          raise exception 'forward_basis_publication_date_invalid';
        end if;
        begin
          if v_published ~ '^\d{4}-\d{2}-\d{2}$' then
            if v_published::date > (v_as_of at time zone 'Asia/Shanghai')::date then
              raise exception 'future_date';
            end if;
          elsif v_published ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$' then
            v_time := v_published::timestamptz;
            if not isfinite(v_time) or v_time > v_as_of then raise exception 'future_date'; end if;
          else raise exception 'invalid_date';
          end if;
        exception when others then raise exception 'forward_basis_publication_date_invalid';
        end;
      end if;
      if v_component_status in ('implemented','announced','no_distribution') then
        if jsonb_typeof(v_component->'amount') is distinct from 'number'
           or jsonb_typeof(v_component->'year') is distinct from 'number'
           or v_component->>'amount_scope' <> 'distribution'
           or v_component->'reason' <> 'null'::jsonb
           or jsonb_typeof(v_component->'source') is distinct from 'object'
           or jsonb_array_length(v_component->'evidence') = 0 then
          raise exception 'forward_basis_usable_component_invalid';
        end if;
        v_amount := (v_component->>'amount')::numeric;
        if v_amount < 0 or v_amount >= 1000000 or round(v_amount,12) <> v_amount
           or (v_component_status = 'no_distribution' and v_amount <> 0)
           or (v_component_status <> 'no_distribution' and v_amount <= 0) then
          raise exception 'forward_basis_component_amount_invalid';
        end if;
        v_sum := v_sum + v_amount; v_usable := v_usable + 1;
      else
        v_failures := array_append(v_failures, v_component_status);
        if v_component->'amount' <> 'null'::jsonb
           or jsonb_typeof(v_component->'reason') is distinct from 'string'
           or length(v_component->>'reason') not between 1 and 200 then
          raise exception 'forward_basis_unknown_component_invalid';
        end if;
      end if;
      -- Only source facts, no arbitrary worker payload fields. Evidence copies
      -- retain title/header/raw plan and dates even when versions conflict.
      for v_source in select value from jsonb_array_elements(
        (v_component->'evidence') || case when v_component->'source' = 'null'::jsonb then '[]'::jsonb else jsonb_build_array(v_component->'source') end
      ) loop
        if jsonb_typeof(v_source) is distinct from 'object' or not (v_source ?& v_source_keys)
           or v_source - v_source_keys <> '{}'::jsonb
           or jsonb_typeof(v_source->'header') is distinct from 'string'
           or jsonb_typeof(v_source->'raw_plan') is distinct from 'string'
           or jsonb_typeof(v_source->'raw_progress') is distinct from 'string'
           or jsonb_typeof(v_source->'scope_evidence') is distinct from 'string'
           or jsonb_typeof(v_source->'row_index') is distinct from 'number'
           or (v_source->>'row_index') !~ '^[0-9]+$'
           or jsonb_typeof(v_source->'distribution_dates') is distinct from 'object'
           or not (v_source->'distribution_dates' ?& array['registration','ex_dividend','payment'])
           or (v_source->'distribution_dates') - array['registration','ex_dividend','payment'] <> '{}'::jsonb then
          raise exception 'forward_basis_source_invalid';
        end if;
      end loop;
    end loop;
    v_derived := case when 'conflict' = any(v_failures) then 'conflict'
                      when 'ambiguous' = any(v_failures) then 'ambiguous'
                      when 'negated' = any(v_failures) then 'negated'
                      when 'missing' = any(v_failures) then 'missing' else 'ready' end;
    -- Invalid fiscal rows block a total even when both identifiable slots exist.
    if v_status <> v_derived and not (v_status = 'missing' and v_derived = 'ready' and v_item->>'reason' = 'invalid_fiscal_period') then
      raise exception 'forward_basis_status_inconsistent';
    end if;
    if v_status = 'ready' then
      if v_usable <> 2 or jsonb_typeof(v_item->'amount') is distinct from 'number'
         or v_item->'reason' <> 'null'::jsonb or (v_item->>'amount')::numeric <> v_sum or v_sum >= 1000000 then
        raise exception 'forward_basis_total_invalid';
      end if;
    elsif v_item->'amount' <> 'null'::jsonb or jsonb_typeof(v_item->'reason') is distinct from 'string'
          or length(v_item->>'reason') not between 1 and 200 then
      raise exception 'forward_basis_unknown_total_invalid';
    end if;
  end loop;
  if exists (select 1 from public.personal_forward_basis_snapshots s
             where s.owner_user_id = auth.uid() and s.as_of > v_as_of) then
    raise exception 'forward_basis_time_regression';
  end if;
  if exists (select 1 from public.personal_forward_basis_snapshots s
      join jsonb_array_elements(p_records) r on r->>'code' = s.stock_code
      where s.owner_user_id = auth.uid() and s.as_of = v_as_of and s.payload <> r - 'code') then
    raise exception 'forward_basis_same_time_conflict';
  end if;
  insert into public.personal_forward_basis_snapshots(owner_user_id, stock_code, payload, as_of)
    select auth.uid(), value->>'code', value - 'code', v_as_of from jsonb_array_elements(p_records)
  on conflict (owner_user_id, stock_code) do update
    set payload = excluded.payload, as_of = excluded.as_of, updated_at = now();
  return jsonb_build_object('stored', v_expected, 'as_of', v_as_of::text);
end;
$$;
revoke all on function public.personal_sync_forward_basis(text,jsonb,text) from PUBLIC, anon, service_role;
grant execute on function public.personal_sync_forward_basis(text,jsonb,text) to authenticated;

-- Wrapping preserves ALL existing fields and events, ordering and old frontend.
-- No historical/futureDividend fallback: an uncollected basis is explicitly null.
create or replace function public.personal_get_part4_v2()
returns jsonb language sql stable security definer
set search_path = pg_catalog, public
as $$
  with base as (select public.personal_get_part4() as payload),
  missing_component as (
    select jsonb_build_object('year',null,'amount',null,'status','missing','reason','not_collected',
      'amount_scope','unknown','published_at',null,'source',null,'evidence','[]'::jsonb) as value
  )
  select case when auth.uid() is null or public.personal_current_user_is_active() is distinct from true
                   or b.payload is null then null else
    jsonb_set(b.payload, '{stocks}', coalesce((
      select jsonb_agg(s.stock || jsonb_build_object('forwardBasis', coalesce(f.payload,
        jsonb_build_object('amount',null,'status','missing','reason','not_collected','asOf',null,
          'components',jsonb_build_object('annual',m.value || '{"kind":"annual"}'::jsonb,
                                          'interim',m.value || '{"kind":"interim"}'::jsonb)))) order by s.ordinal)
      from jsonb_array_elements(coalesce(b.payload->'stocks','[]'::jsonb)) with ordinality as s(stock,ordinal)
      left join public.personal_forward_basis_snapshots f on f.owner_user_id = auth.uid()
        and f.stock_code = s.stock->>'code'
        and exists (select 1 from public.personal_watchlist_items w where w.owner_user_id = auth.uid() and w.stock_code = f.stock_code)
    ), '[]'::jsonb), true) end
  from base b cross join missing_component m;
$$;
revoke all on function public.personal_get_part4_v2() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_part4_v2() to authenticated;
notify pgrst, 'reload schema';
commit;
