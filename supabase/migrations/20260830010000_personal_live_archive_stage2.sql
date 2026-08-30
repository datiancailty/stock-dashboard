-- Personal dashboard live archive overlay — forward-only Stage 2.
-- MANUAL HOSTED SQL EXECUTION ONLY.
-- This migration permits provenance paths from the existing live branch and
-- adds owner-scoped private mutation RPCs for the restored dashboard controls.
-- It does not alter vps_* tables, enable trading, or expose any direct table DML.

begin;

alter table public.personal_import_batches
  drop constraint if exists personal_import_batches_path;

alter table public.personal_import_batches
  add constraint personal_import_batches_path check (
    source_path ~ '^(data|live)/[a-z0-9][a-z0-9._-]*\.json$'
  );

create or replace function public.personal_get_migration_state()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select case when not public.personal_current_user_is_active() then null else jsonb_build_object(
    'schema_version', 2,
    'source_files', (select count(*) from public.personal_import_batches b where b.owner_user_id = auth.uid()),
    'watchlist_count', (select count(*) from public.personal_watchlist_items w where w.owner_user_id = auth.uid()),
    'news_count', (select count(*) from public.personal_news_items n where n.owner_user_id = auth.uid()),
    'trade_count', (select count(*) from public.personal_trade_records t where t.owner_user_id = auth.uid()),
    'feedback_count', (select count(*) from public.personal_strategy_feedback f where f.owner_user_id = auth.uid()),
    'recommendation_count', (select count(*) from public.personal_strategy_recommendations r where r.owner_user_id = auth.uid()),
    'market_stock_count', coalesce((select jsonb_array_length(d.payload->'stocks') from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'market'), 0),
    'calendar_event_count', coalesce((select jsonb_array_length(d.payload->'events') from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'market'), 0),
    'weekly_boll_count', coalesce((select count(*) from jsonb_array_elements(coalesce((select d.payload->'stocks' from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'market'), '[]'::jsonb)) s where jsonb_typeof(s->'weeklyBoll') = 'object'), 0),
    'live_archive_source', (select max(b.source_path) from public.personal_import_batches b where b.owner_user_id = auth.uid() and b.source_path like 'live/%'),
    'latest_imported_at', (select max(b.imported_at) from public.personal_import_batches b where b.owner_user_id = auth.uid())
  ) end;
$$;
revoke all on function public.personal_get_migration_state() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_migration_state() to authenticated;

-- Restore the original Part 1 watchlist controls inside the private namespace.
-- The complete list is replaced atomically; it is unrelated to the VPS
-- whitelist and never changes simulated-account positions.
create or replace function public.personal_replace_watchlist(p_items jsonb)
returns jsonb
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
declare
  item jsonb;
  code text;
  display_name text;
  seen_codes text[] := array[]::text[];
  item_count integer;
  replaced_count integer;
begin
  if not public.personal_current_user_is_active() then
    raise exception 'personal_auth_required';
  end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' then
    raise exception 'watchlist_must_be_array';
  end if;
  item_count := jsonb_array_length(p_items);
  if item_count > 50 then
    raise exception 'watchlist_too_large';
  end if;

  for item in select value from jsonb_array_elements(p_items) loop
    if jsonb_typeof(item) <> 'object' then
      raise exception 'watchlist_item_must_be_object';
    end if;
    code := btrim(item->>'code');
    display_name := btrim(item->>'name');
    if code !~ '^\d{6}$' then
      raise exception 'watchlist_code_invalid';
    end if;
    if display_name is null or char_length(display_name) not between 1 and 80 then
      raise exception 'watchlist_name_invalid';
    end if;
    if code = any(seen_codes) then
      raise exception 'watchlist_duplicate_code';
    end if;
    seen_codes := array_append(seen_codes, code);
  end loop;

  delete from public.personal_watchlist_items where owner_user_id = auth.uid();

  for item in select value from jsonb_array_elements(p_items) loop
    code := btrim(item->>'code');
    display_name := btrim(item->>'name');
    insert into public.personal_watchlist_items (
      owner_user_id, source_id, stock_code, display_name, payload, source_sha256
    ) values (
      auth.uid(), code, code, display_name, item,
      encode(extensions.digest(item::text, 'sha256'), 'hex')
    );
  end loop;

  select count(*) into replaced_count
    from public.personal_watchlist_items
   where owner_user_id = auth.uid();
  return jsonb_build_object('count', replaced_count);
end;
$$;
revoke all on function public.personal_replace_watchlist(jsonb) from PUBLIC, anon, service_role;
grant execute on function public.personal_replace_watchlist(jsonb) to authenticated;

-- Append private operation records after full server-side validation. Existing
-- source IDs are idempotent; the browser still filters them before calling.
create or replace function public.personal_append_trade_records(p_records jsonb)
returns jsonb
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
declare
  item jsonb;
  source_id text;
  code text;
  action text;
  date_text text;
  price_text text;
  shares_text text;
  parsed_date date;
  parsed_price numeric;
  parsed_shares bigint;
  seen_ids text[] := array[]::text[];
  requested_count integer;
  inserted_count integer;
begin
  if not public.personal_current_user_is_active() then
    raise exception 'personal_auth_required';
  end if;
  if p_records is null or jsonb_typeof(p_records) <> 'array' then
    raise exception 'trade_records_must_be_array';
  end if;
  requested_count := jsonb_array_length(p_records);
  if requested_count > 5000 then
    raise exception 'trade_records_too_large';
  end if;

  for item in select value from jsonb_array_elements(p_records) loop
    if jsonb_typeof(item) <> 'object' then
      raise exception 'trade_record_must_be_object';
    end if;
    source_id := btrim(item->>'id');
    code := btrim(item->>'code');
    action := btrim(item->>'action');
    date_text := btrim(item->>'date');
    price_text := btrim(item->>'price');
    shares_text := btrim(item->>'shares');

    if source_id is null or source_id !~ '^[A-Za-z0-9._:-]{1,160}$' then
      raise exception 'trade_record_id_invalid';
    end if;
    if source_id = any(seen_ids) then
      raise exception 'trade_record_duplicate_id';
    end if;
    seen_ids := array_append(seen_ids, source_id);
    if code !~ '^\d{6}$' then
      raise exception 'trade_record_code_invalid';
    end if;
    if action not in ('买入', '卖出', '做T买入', '做T卖出') then
      raise exception 'trade_record_action_invalid';
    end if;
    if date_text !~ '^\d{4}-\d{2}-\d{2}$' then
      raise exception 'trade_record_date_invalid';
    end if;
    begin
      parsed_date := date_text::date;
    exception when others then
      raise exception 'trade_record_date_invalid';
    end;
    if to_char(parsed_date, 'YYYY-MM-DD') <> date_text then
      raise exception 'trade_record_date_invalid';
    end if;
    if price_text !~ '^[0-9]+(\.[0-9]+)?$' then
      raise exception 'trade_record_price_invalid';
    end if;
    begin
      parsed_price := price_text::numeric;
    exception when others then
      raise exception 'trade_record_price_invalid';
    end;
    if parsed_price <= 0 or parsed_price = 'NaN'::numeric then
      raise exception 'trade_record_price_invalid';
    end if;
    if shares_text !~ '^[1-9][0-9]*$' then
      raise exception 'trade_record_shares_invalid';
    end if;
    begin
      parsed_shares := shares_text::bigint;
    exception when others then
      raise exception 'trade_record_shares_invalid';
    end;
    if parsed_shares <= 0 then
      raise exception 'trade_record_shares_invalid';
    end if;
  end loop;

  insert into public.personal_trade_records (
    owner_user_id, source_id, trade_date, stock_code, payload, source_sha256
  )
  select auth.uid(), btrim(item->>'id'), (item->>'date')::date, btrim(item->>'code'), item,
         encode(extensions.digest(item::text, 'sha256'), 'hex')
    from jsonb_array_elements(p_records) item
  on conflict (owner_user_id, source_id) do nothing;

  get diagnostics inserted_count = row_count;
  return jsonb_build_object('requested', requested_count, 'inserted', inserted_count);
end;
$$;
revoke all on function public.personal_append_trade_records(jsonb) from PUBLIC, anon, service_role;
grant execute on function public.personal_append_trade_records(jsonb) to authenticated;

create or replace function public.personal_delete_trade_record(p_source_id text)
returns boolean
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
declare
  deleted_count integer;
begin
  if not public.personal_current_user_is_active() then
    raise exception 'personal_auth_required';
  end if;
  if p_source_id is null or p_source_id !~ '^[A-Za-z0-9._:-]{1,160}$' then
    raise exception 'trade_record_id_invalid';
  end if;
  delete from public.personal_trade_records
   where owner_user_id = auth.uid() and source_id = p_source_id;
  get diagnostics deleted_count = row_count;
  return deleted_count = 1;
end;
$$;
revoke all on function public.personal_delete_trade_record(text) from PUBLIC, anon, service_role;
grant execute on function public.personal_delete_trade_record(text) to authenticated;

create or replace function public.personal_append_strategy_feedback(p_record jsonb)
returns jsonb
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
declare
  source_id text;
  recommendation_id text;
  status_text text;
  code text;
  inserted_count integer;
begin
  if not public.personal_current_user_is_active() then
    raise exception 'personal_auth_required';
  end if;
  if p_record is null or jsonb_typeof(p_record) <> 'object' then
    raise exception 'feedback_must_be_object';
  end if;
  source_id := btrim(p_record->>'id');
  recommendation_id := btrim(p_record->>'recommendationId');
  status_text := btrim(p_record->>'status');
  code := btrim(coalesce(p_record->>'code', ''));
  if source_id is null or source_id !~ '^[A-Za-z0-9._:-]{1,160}$' then
    raise exception 'feedback_id_invalid';
  end if;
  if recommendation_id is null or char_length(recommendation_id) not between 1 and 200 then
    raise exception 'feedback_recommendation_invalid';
  end if;
  if status_text not in ('executed', 'not_executed', 'deferred') then
    raise exception 'feedback_status_invalid';
  end if;
  if code <> '' and code !~ '^\d{6}$' then
    raise exception 'feedback_code_invalid';
  end if;

  insert into public.personal_strategy_feedback (
    owner_user_id, source_id, recommendation_id, stock_code, payload, source_sha256
  ) values (
    auth.uid(), source_id, recommendation_id, nullif(code, ''), p_record,
    encode(extensions.digest(p_record::text, 'sha256'), 'hex')
  ) on conflict (owner_user_id, source_id) do nothing;
  get diagnostics inserted_count = row_count;
  return jsonb_build_object('inserted', inserted_count);
end;
$$;
revoke all on function public.personal_append_strategy_feedback(jsonb) from PUBLIC, anon, service_role;
grant execute on function public.personal_append_strategy_feedback(jsonb) to authenticated;

commit;
