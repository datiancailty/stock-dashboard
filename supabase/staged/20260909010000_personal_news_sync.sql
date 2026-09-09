-- Part 5 private news search: MANUAL HOSTED EXECUTION ONLY, staged not applied.
-- Matches the supplied Hosted CSV: personal_news_items PK(owner_user_id,source_id),
-- stock_code/published_at/payload/source_sha256; personal_documents news_meta.
-- No credential provisioning, no new table grants, no Part4/strategy mutations.
-- RPC input is a completed search declaration, NOT a claim of exhaustive news
-- discovery. Empty news results are valid ONLY with full nonempty batch coverage.
-- Search hits never become independently verified official Part4 notices.
-- Requires existing personal_part4_sync_writer_credentials + active auth.uid().
-- Existing watchlist writer has no shared advisory lock: a short SHARE table lock
-- is necessary to prevent races with its delete/reinsert, including empty sets.
-- The table lock can briefly block OTHER users' watchlist edits, but no network
-- work runs inside this transaction. Schedule integration is a separate gate.
begin;

create or replace function public.personal_sync_news(
  p_scan_started_at text,
  p_scan_completed_at text,
  p_expected_last_scan_at text,
  p_watchlist jsonb,
  p_batches jsonb,
  p_items jsonb,
  p_writer_secret text,
  p_watchlist_snapshot jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  v_started timestamptz;
  v_completed timestamptz;
  v_last timestamptz;
  v_published timestamptz;
  v_first_seen timestamptz;
  v_meta jsonb;
  v_next_meta jsonb;
  v_current_watchlist jsonb;
  v_sorted_watchlist jsonb;
  v_tracked jsonb;
  v_item jsonb;
  v_batch jsonb;
  v_value jsonb;
  v_code text;
  v_key text;
  v_text text;
  v_since text;
  v_kind text;
  v_digest text;
  v_ids text[] := array[]::text[];
  v_codes text[] := array[]::text[];
  v_scanned text[] := array[]::text[];
  v_tracked_codes text[] := array[]::text[];
  v_count integer;
  v_stored integer := 0;
  v_rows integer;
  v_bound integer;
  v_ts_re constant text := '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$';
begin
  if auth.uid() is null or public.personal_current_user_is_active() is distinct from true then
    raise exception 'personal_auth_required';
  end if;
  if p_writer_secret is null or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
     or not exists (
       select 1 from public.personal_part4_sync_writer_credentials c
        where c.owner_user_id = auth.uid()
          and c.secret_sha256 = encode(extensions.digest(p_writer_secret, 'sha256'), 'hex')
     ) then
    raise exception 'news_trusted_writer_required';
  end if;
  if jsonb_typeof(p_watchlist) is distinct from 'array'
     or jsonb_typeof(p_watchlist_snapshot) is distinct from 'array'
     or jsonb_typeof(p_batches) is distinct from 'array'
     or jsonb_typeof(p_items) is distinct from 'array' then
    raise exception 'news_input_shape_invalid';
  end if;
  -- Caps bound lock duration and validate before any persistent DML.
  if jsonb_array_length(p_watchlist) not between 1 and 50
     or jsonb_array_length(p_watchlist_snapshot) <> jsonb_array_length(p_watchlist)
     or jsonb_array_length(p_batches) not between 1 and 50
     or jsonb_array_length(p_items) > 1000
     or octet_length(p_watchlist::text) > 100000
     or octet_length(p_watchlist_snapshot::text) > 100000
     or octet_length(p_batches::text) > 50000
     or octet_length(p_items::text) > 8000000 then
    raise exception 'news_input_size_invalid';
  end if;
  if p_scan_started_at is null or p_scan_completed_at is null
     or p_scan_started_at !~ v_ts_re or p_scan_completed_at !~ v_ts_re
     or (p_expected_last_scan_at is not null and p_expected_last_scan_at !~ v_ts_re) then
    raise exception 'news_timestamp_invalid';
  end if;
  begin
    v_started := p_scan_started_at::timestamptz;
    v_completed := p_scan_completed_at::timestamptz;
  exception when others then
    raise exception 'news_timestamp_invalid';
  end;
  if v_started < now() - interval '3 days' or v_completed > now() + interval '5 minutes'
     or v_completed < v_started or v_completed - v_started > interval '2 hours' then
    raise exception 'news_timestamp_out_of_range';
  end if;

  perform pg_advisory_xact_lock(hashtext('personal_news:' || auth.uid()::text));
  lock table public.personal_watchlist_items in share mode;
  select coalesce(jsonb_agg(w.payload order by w.stock_code), '[]'::jsonb), count(*)
    into v_current_watchlist, v_count
    from public.personal_watchlist_items w where w.owner_user_id = auth.uid();
  if v_count not between 1 and 50 or jsonb_array_length(p_watchlist) <> v_count then
    raise exception 'news_watchlist_stale';
  end if;
  for v_item in select value from jsonb_array_elements(p_watchlist) loop
    if jsonb_typeof(v_item) is distinct from 'object'
       or v_item - array['code','name'] <> '{}'::jsonb
       or jsonb_typeof(v_item->'code') is distinct from 'string'
       or jsonb_typeof(v_item->'name') is distinct from 'string' then
      raise exception 'news_watchlist_invalid';
    end if;
    v_code := v_item->>'code';
    if v_code !~ '^[0-9]{6}$' or v_code = any(v_codes)
       or char_length(v_item->>'name') not between 1 and 80
       or btrim(v_item->>'name') <> v_item->>'name'
       or not exists (select 1 from public.personal_watchlist_items w
          where w.owner_user_id = auth.uid() and w.stock_code = v_code
            and w.display_name = v_item->>'name') then
      raise exception 'news_watchlist_stale';
    end if;
    v_codes := array_append(v_codes, v_code);
  end loop;
  select jsonb_agg(value order by value->>'code') into v_sorted_watchlist
    from jsonb_array_elements(p_watchlist_snapshot);
  if v_sorted_watchlist is distinct from v_current_watchlist then
    raise exception 'news_watchlist_stale';
  end if;

  select d.payload into v_meta from public.personal_documents d
    where d.owner_user_id = auth.uid() and d.document_key = 'news_meta' for update;
  v_meta := coalesce(v_meta, '{}'::jsonb);
  if v_meta ? 'lastScanAt' then
    if jsonb_typeof(v_meta->'lastScanAt') is distinct from 'string'
       or (v_meta->>'lastScanAt') !~ v_ts_re then
      raise exception 'news_stored_metadata_invalid';
    end if;
    begin
      v_last := (v_meta->>'lastScanAt')::timestamptz;
    exception when others then
      raise exception 'news_stored_metadata_invalid';
    end;
    if v_last < '2000-01-01T00:00:00Z'::timestamptz then
      raise exception 'news_stored_metadata_invalid';
    end if;
  end if;
  v_digest := encode(extensions.digest(jsonb_build_object(
    'started', p_scan_started_at, 'completed', p_scan_completed_at,
    'expectedLastScanAt', p_expected_last_scan_at, 'watchlist', p_watchlist,
    'watchlistSnapshot', p_watchlist_snapshot,
    'batches', p_batches, 'items', p_items)::text, 'sha256'), 'hex');
  -- Exact retry after a lost response is a read-only success, not another scan.
  -- The full current watchlist and current owner/credential were rechecked above.
  if v_meta->>'newsSyncInputSha256' = v_digest and v_meta->>'lastScanAt' = p_scan_started_at then
    return jsonb_build_object('status', 'ok', 'stored', 0, 'lastScanAt', p_scan_started_at, 'idempotent', true);
  end if;
  if (v_meta->>'lastScanAt') is distinct from p_expected_last_scan_at
     or (v_last is not null and v_started <= v_last) then
    raise exception 'news_scan_stale';
  end if;
  v_tracked := coalesce(v_meta->'trackedStockCodes', '[]'::jsonb);
  if jsonb_typeof(v_tracked) is distinct from 'array' then
    raise exception 'news_stored_metadata_invalid';
  end if;
  if jsonb_array_length(v_tracked) > 50 then
    raise exception 'news_stored_metadata_invalid';
  end if;
  for v_value in select value from jsonb_array_elements(v_tracked) loop
    if jsonb_typeof(v_value) is distinct from 'string' then
      raise exception 'news_stored_metadata_invalid';
    end if;
    v_code := v_value #>> '{}';
    if v_code !~ '^[0-9]{6}$' or v_code = any(v_tracked_codes) then
      raise exception 'news_stored_metadata_invalid';
    end if;
    v_tracked_codes := array_append(v_tracked_codes, v_code);
  end loop;

  -- A batch declaration is accepted only after the trusted worker completed the
  -- actual service request. The database cannot independently prove search recall.
  for v_batch in select value from jsonb_array_elements(p_batches) loop
    if jsonb_typeof(v_batch) is distinct from 'object'
       or not (v_batch ?& array['kind','since','codes'])
       or v_batch - array['kind','since','codes'] <> '{}'::jsonb
       or jsonb_typeof(v_batch->'kind') is distinct from 'string'
       or jsonb_typeof(v_batch->'since') is distinct from 'string'
       or jsonb_typeof(v_batch->'codes') is distinct from 'array' then
      raise exception 'news_batch_invalid';
    end if;
    if v_batch->>'kind' not in ('incremental','history')
       or jsonb_array_length(v_batch->'codes') not between 1 and 5 then
      raise exception 'news_batch_invalid';
    end if;
    for v_value in select value from jsonb_array_elements(v_batch->'codes') loop
      if jsonb_typeof(v_value) is distinct from 'string' then
        raise exception 'news_batch_invalid';
      end if;
      v_code := v_value #>> '{}';
      if v_code !~ '^[0-9]{6}$' or not (v_code = any(v_codes)) then
        raise exception 'news_batch_invalid';
      end if;
      if v_code = any(v_scanned) then
        raise exception 'news_batch_duplicate_code';
      end if;
      v_scanned := array_append(v_scanned, v_code);
      if v_last is not null and v_code = any(v_tracked_codes) then
        v_kind := 'incremental';
        v_since := to_char((v_last at time zone 'Asia/Shanghai')::date - 2, 'YYYY-MM-DD');
      else
        v_kind := 'history';
        v_since := (extract(year from v_started at time zone 'Asia/Shanghai')::integer - 1)::text || '-01-01';
      end if;
      if v_batch->>'kind' <> v_kind or v_batch->>'since' <> v_since then
        raise exception 'news_batch_window_invalid';
      end if;
    end loop;
  end loop;
  if cardinality(v_scanned) <> v_count then
    raise exception 'news_batch_coverage_incomplete';
  end if;

  for v_item in select value from jsonb_array_elements(p_items) loop
    if jsonb_typeof(v_item) is distinct from 'object'
       or not (v_item ?& array['id','code','name','title','publishedAt','type','source','url','summary',
          'firstSeenAt','estimatedDividendPerShare','calculation','confidence','status','inputs','sourceClass','officialVerified'])
       or v_item - array['id','code','name','title','publishedAt','type','source','url','summary',
          'firstSeenAt','estimatedDividendPerShare','calculation','confidence','status','inputs','sourceClass','officialVerified'] <> '{}'::jsonb
       or octet_length(v_item::text) > 24000 then
      raise exception 'news_item_shape_invalid';
    end if;
    -- All required textual fields reject JSON null and unexpected JSON scalars.
    foreach v_key in array array['id','code','name','title','publishedAt','type','source','url','summary',
                                  'firstSeenAt','calculation','confidence','status','sourceClass'] loop
      if jsonb_typeof(v_item->v_key) is distinct from 'string' then
        raise exception 'news_item_field_invalid';
      end if;
      v_text := v_item->>v_key;
      v_bound := case v_key when 'title' then 500 when 'source' then 200
          when 'url' then 2048 when 'summary' then 421 when 'calculation' then 2000
          when 'id' then 20 when 'code' then 6 else 80 end;
      if char_length(v_text) > v_bound or v_text <> btrim(v_text)
         or v_text ~ '[[:cntrl:]]' or (v_key not in ('url','summary') and char_length(v_text) = 0) then
        raise exception 'news_item_field_invalid';
      end if;
    end loop;
    v_code := v_item->>'code';
    if v_item->>'id' !~ '^[0-9a-f]{20}$' or v_code !~ '^[0-9]{6}$'
       or not (v_code = any(v_codes)) or not exists (
         select 1 from public.personal_watchlist_items w where w.owner_user_id = auth.uid()
           and w.stock_code = v_code and w.display_name = v_item->>'name') then
      raise exception 'news_item_identity_invalid';
    end if;
    if v_item->>'id' = any(v_ids) then
      raise exception 'news_item_duplicate_id';
    end if;
    v_ids := array_append(v_ids, v_item->>'id');
    -- type is kept verbatim (NEWS/公告/etc); status describes extracted text, NOT
    -- official verification. Neither a company-looking title nor a URL upgrades it.
    if v_item->>'sourceClass' <> 'news_search'
       or (v_item->'officialVerified') is distinct from 'false'::jsonb
       or ((v_item->>'url') <> '' and (v_item->>'url') !~ '^https?://[^[:space:]/]+[^[:space:]]*$')
       or v_item->>'confidence' not in ('高','中','待补充')
       or v_item->>'status' not in ('信息提示','正式预案','已实施','政策预估') then
      raise exception 'news_item_source_invalid';
    end if;
    v_text := v_item->>'publishedAt';
    if v_text !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}([T ][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})?)?$'
       or (v_item->>'firstSeenAt') !~ v_ts_re then
      raise exception 'news_item_timestamp_invalid';
    end if;
    begin
      if char_length(v_text) = 10 then
        v_published := (v_text || 'T00:00:00+08:00')::timestamptz;
      elsif v_text ~ '(Z|[+-][0-9]{2}:[0-9]{2})$' then
        v_published := v_text::timestamptz;
      else
        v_published := (v_text || '+08:00')::timestamptz;
      end if;
      v_first_seen := (v_item->>'firstSeenAt')::timestamptz;
    exception when others then
      raise exception 'news_item_timestamp_invalid';
    end;
    if v_published < '2000-01-01T00:00:00+08:00'::timestamptz
       or v_published > v_completed + interval '5 minutes'
       or v_first_seen < v_started or v_first_seen > v_completed then
      raise exception 'news_item_timestamp_invalid';
    end if;
    -- Null DPS and null individual numeric inputs mean "not calculable", exactly
    -- as in legacy; JSON null is denied for containers and required provenance.
    if jsonb_typeof(v_item->'estimatedDividendPerShare') not in ('null','number') then
      raise exception 'news_item_numeric_invalid';
    end if;
    if jsonb_typeof(v_item->'estimatedDividendPerShare') = 'number' then
      if (v_item->>'estimatedDividendPerShare')::numeric not between 0 and 1000000 then
        raise exception 'news_item_numeric_invalid';
      end if;
    end if;
    if jsonb_typeof(v_item->'inputs') is distinct from 'object'
       or (v_item->'inputs') - array['directPerShare','totalDividend','shares','distributionBase','payoutRatio','quotes'] <> '{}'::jsonb then
      raise exception 'news_item_inputs_invalid';
    end if;
    for v_key, v_value in select key, value from jsonb_each(v_item->'inputs') loop
      if v_key = 'quotes' then
        if jsonb_typeof(v_value) is distinct from 'array' then
          raise exception 'news_item_inputs_invalid';
        end if;
        if jsonb_array_length(v_value) > 3 or exists (
          select 1 from jsonb_array_elements(v_value) q
           where jsonb_typeof(q) is distinct from 'string' or char_length(q #>> '{}') > 2000
              or (q #>> '{}') ~ '[[:cntrl:]]') then
          raise exception 'news_item_inputs_invalid';
        end if;
      else
        if jsonb_typeof(v_value) not in ('null','number') then
          raise exception 'news_item_inputs_invalid';
        end if;
        if jsonb_typeof(v_value) = 'number' and (v_value #>> '{}')::numeric not between 0 and 10000000000000000 then
          raise exception 'news_item_inputs_invalid';
        end if;
      end if;
    end loop;
    -- Never overwrite a legacy historical observation, and reject identity
    -- reuse across stocks rather than turning it into a silent first-wins skip.
    if exists (select 1 from public.personal_news_items n
       where n.owner_user_id = auth.uid() and n.source_id = v_item->>'id'
         and n.stock_code is distinct from v_code) then
      raise exception 'news_item_identity_conflict';
    end if;
    insert into public.personal_news_items(owner_user_id, source_id, stock_code, published_at, payload, source_sha256)
    values (auth.uid(), v_item->>'id', v_code, v_published, v_item,
            encode(extensions.digest(v_item::text, 'sha256'), 'hex'))
    on conflict (owner_user_id, source_id) do nothing;
    get diagnostics v_rows = row_count;
    v_stored := v_stored + v_rows;
  end loop;

  -- Only this document key changes. Unknown legacy metadata survives; all news
  -- history stays in personal_news_items without a 1000-row retention truncation.
  -- lastScanAt uses the START of a successful search, not fetching/import time,
  -- so articles arriving during a slow query remain in the next overlap window.
  v_next_meta := v_meta || jsonb_build_object(
    'updatedAt', p_scan_completed_at, 'lastScanAt', p_scan_started_at,
    'scanCompletedAt', p_scan_completed_at, 'source', '东方财富妙想资讯搜索',
    'trackedStockCodes', to_jsonb(v_codes), 'newsSyncInputSha256', v_digest,
    'coverageComplete', true, 'coverageMeaning', 'successful_search_batches_not_exhaustive_results',
    'attemptedBatchCount', jsonb_array_length(p_batches), 'successfulBatchCount', jsonb_array_length(p_batches),
    'scannedWatchlistCount', v_count, 'expectedWatchlistCount', v_count,
    'overlapDays', 2, 'sourceClass', 'news_search', 'officialVerified', false);
  insert into public.personal_documents(owner_user_id, document_key, payload, source_path, source_sha256)
  values (auth.uid(), 'news_meta', v_next_meta, 'private/personal-news-sync',
          encode(extensions.digest(v_next_meta::text, 'sha256'), 'hex'))
  on conflict (owner_user_id, document_key) do update set
    payload = excluded.payload, source_sha256 = excluded.source_sha256, updated_at = now();
  return jsonb_build_object('status', 'ok', 'stored', v_stored, 'lastScanAt', p_scan_started_at,
                           'idempotent', false, 'scannedWatchlistCount', v_count);
end;
$$;

revoke all on function public.personal_sync_news(text,text,text,jsonb,jsonb,jsonb,text,jsonb) from PUBLIC, anon, service_role;
grant execute on function public.personal_sync_news(text,text,text,jsonb,jsonb,jsonb,text,jsonb) to authenticated;
comment on function public.personal_sync_news(text,text,text,jsonb,jsonb,jsonb,text,jsonb) is
  'Owner-scoped Part5 insert-only news history plus news_meta; active auth and existing Part4 writer capability; all successful search batches, not exhaustive factual coverage.';
notify pgrst, 'reload schema';
commit;
