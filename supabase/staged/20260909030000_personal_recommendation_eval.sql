-- MANUAL STAGED MIGRATION ONLY: non-AI outcomes of existing recommendations.
-- Prerequisites: 20260830000000 legacy tables, active-user gate, existing Part4
-- writer credentials and extensions.digest(text,text). No new writer provisioning.
-- Does not change getters/UI, trades, feedback, profile, analysis or recommendations.
-- Existing get_part6 exposes recommendations_meta.performance via recommendations.
-- The full declared owner set + exact old JSONB payloads are compared on server.
-- A brief table lock also blocks INSERT/DELETE by older writers that do not take
-- our advisory lock. This intentionally serializes recommendation writes globally
-- while this bounded RPC runs; use a new context after any stale-context failure.
begin;

create or replace function public.personal_get_recommendation_eval_context()
returns jsonb language sql stable security definer
set search_path = pg_catalog, public
as $$
  select case when not public.personal_current_user_is_active() then null else
    jsonb_build_object(
      'records', coalesce((select jsonb_agg(jsonb_build_object('sourceId', r.source_id, 'payload', r.payload) order by r.source_id)
        from public.personal_strategy_recommendations r where r.owner_user_id = auth.uid()), '[]'::jsonb),
      'meta', (select d.payload from public.personal_documents d
        where d.owner_user_id = auth.uid() and d.document_key = 'recommendations_meta')
    ) end;
$$;
revoke all on function public.personal_get_recommendation_eval_context() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_recommendation_eval_context() to authenticated;

create or replace function public.personal_sync_recommendation_evaluations(
  p_as_of text, p_records jsonb, p_previous_meta jsonb, p_performance jsonb, p_writer_secret text
) returns jsonb language plpgsql security definer
set search_path = pg_catalog, public
as $$
declare
  v_owner uuid := auth.uid();
  v_as_of timestamptz;
  v_cutoff date;
  v_meta jsonb;
  v_base jsonb;
  v_item jsonb;
  v_eval jsonb;
  v_old jsonb;
  v_sid text;
  v_seen text[] := array[]::text[];
  v_n integer;
  v_obs integer;
  v_start date;
  v_observed date;
  v_day date;
  v_entry numeric;
  v_key text;
  v_status text;
  v_retained integer := 0;
  v_expected jsonb;
  v_value numeric;
  v_count integer;
begin
  if v_owner is null or not public.personal_current_user_is_active() then
    raise exception 'recommendation_auth_required';
  end if;
  if p_writer_secret is null or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
     or not exists (select 1 from public.personal_part4_sync_writer_credentials c
        where c.owner_user_id = v_owner
          and c.secret_sha256 = encode(extensions.digest(p_writer_secret, 'sha256'), 'hex')) then
    raise exception 'recommendation_trusted_writer_required';
  end if;
  if jsonb_typeof(p_records) is distinct from 'array'
     or jsonb_typeof(p_previous_meta) is distinct from 'object'
     or jsonb_typeof(p_performance) is distinct from 'object'
     or octet_length(p_records::text) > 10485760
     or octet_length(p_performance::text) > 8192
     or coalesce(p_performance->>'calendarSource','') not in
       ('legacy_sz399001_daily_session_proxy','provided_exchange_calendar','retained_only')
     or jsonb_array_length(p_records) not between 1 and 5000 then
    raise exception 'recommendation_input_invalid';
  end if;
  if p_as_of is null or p_as_of !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$'
     or coalesce(p_performance->>'dataAsOf', '') !~ '^\d{4}-\d{2}-\d{2}$' then
    raise exception 'recommendation_as_of_invalid';
  end if;
  v_as_of := p_as_of::timestamptz;
  v_cutoff := (p_performance->>'dataAsOf')::date;
  if v_as_of < now() - interval '3 days' or v_as_of > now() + interval '5 minutes'
     or v_cutoff > (v_as_of at time zone 'Asia/Shanghai')::date
     or v_cutoff < (v_as_of at time zone 'Asia/Shanghai')::date - 10 then
    raise exception 'recommendation_as_of_invalid';
  end if;

  lock table public.personal_strategy_recommendations in share row exclusive mode;
  select d.payload into v_meta from public.personal_documents d
    where d.owner_user_id = v_owner and d.document_key = 'recommendations_meta' for update;
  if not found or v_meta is distinct from p_previous_meta then
    raise exception 'recommendation_context_stale';
  end if;
  if (v_meta->>'asOf')::timestamptz >= v_as_of
     or (v_meta#>>'{performance,dataAsOf}')::date > v_cutoff then
    raise exception 'recommendation_context_stale';
  end if;
  select count(*) into v_n from public.personal_strategy_recommendations where owner_user_id = v_owner;
  if v_n <> jsonb_array_length(p_records) then
    raise exception 'recommendation_coverage_invalid';
  end if;

  -- Validate the entire batch before the first mutation.
  for v_item in select value from jsonb_array_elements(p_records) loop
    if jsonb_typeof(v_item) is distinct from 'object'
       or not v_item ?& array['sourceId','previousPayload','evaluation']
       or v_item - array['sourceId','previousPayload','evaluation'] <> '{}'::jsonb
       or jsonb_typeof(v_item->'sourceId') is distinct from 'string'
       or jsonb_typeof(v_item->'previousPayload') is distinct from 'object'
       or jsonb_typeof(v_item->'evaluation') is distinct from 'object' then
      raise exception 'recommendation_input_invalid';
    end if;
    v_sid := v_item->>'sourceId';
    if char_length(v_sid) not between 1 and 300 or v_sid = any(v_seen) then
      raise exception 'recommendation_coverage_invalid';
    end if;
    v_seen := array_append(v_seen, v_sid);
    select r.payload into v_base from public.personal_strategy_recommendations r
      where r.owner_user_id = v_owner and r.source_id = v_sid;
    if not found or v_base is distinct from v_item->'previousPayload' then
      raise exception 'recommendation_context_stale';
    end if;
    v_eval := v_item->'evaluation';
    v_old := coalesce(v_base->'evaluation', '{}'::jsonb);
    if (v_old->>'evaluatedAt')::timestamptz > v_as_of
       or (v_old->>'observationAsOf')::date > v_cutoff then
      raise exception 'recommendation_context_stale';
    end if;
    if v_base->>'action' is distinct from '分批买入' then
      -- Unscored legacy objects can be retained byte-for-byte; a new value has
      -- exactly these fixed non-AI keys, not arbitrary prose or analysis fields.
      if v_eval = v_old and v_eval->>'status' = 'not_scored' then continue; end if;
      if v_eval <> jsonb_build_object('status','not_scored','reason','非明确买入指令，不计入买入命中率') then
        raise exception 'recommendation_evaluation_invalid';
      end if;
      continue;
    end if;
    if v_old->>'status' in ('success','failed') and v_old->>'observedTradingDays' = '30' then
      if v_eval is distinct from v_old then raise exception 'recommendation_outcome_regression'; end if;
      v_retained := v_retained + 1;
      continue;
    end if;
    if v_eval - array['status','criterion','entryPrice','targetPrice','observedTradingDays',
        'maxGainPct','latestReturnPct','firstHitAt','tradingDaysToHit','calendarDaysToHit',
        'weeklyUpperFirstAt','tradingDaysToWeeklyUpper','calendarDaysToWeeklyUpper',
        'evaluatedAt','observationAsOf','dataSource'] <> '{}'::jsonb
       or not v_eval ?& array['status','criterion','entryPrice','targetPrice','observedTradingDays',
        'maxGainPct','latestReturnPct','evaluatedAt','observationAsOf','dataSource']
       or v_eval->>'criterion' is distinct from '30个交易日内最高价达到指令价+5%'
       or v_eval->>'dataSource' is distinct from 'legacy_eastmoney_tencent_unadjusted_daily'
       or v_eval->>'evaluatedAt' is distinct from p_as_of
       or coalesce(v_eval->>'observationAsOf','') !~ '^\d{4}-\d{2}-\d{2}$'
       or coalesce(v_base->>'code','') !~ '^[0-9]{6}$'
       or jsonb_typeof(v_base#>'{snapshot,price}') is distinct from 'number' then
      raise exception 'recommendation_evaluation_invalid';
    end if;
    v_status := v_eval->>'status';
    if v_status is null or v_status not in ('pending','success','failed') then
      -- No publishing an active no_data/unknown evaluation over previous results.
      raise exception 'recommendation_observations_unavailable';
    end if;
    foreach v_key in array array['entryPrice','targetPrice','observedTradingDays','maxGainPct','latestReturnPct'] loop
      if jsonb_typeof(v_eval->v_key) is distinct from 'number' then
        raise exception 'recommendation_evaluation_invalid';
      end if;
    end loop;
    if (v_eval->>'observedTradingDays') !~ '^([1-9]|[12][0-9]|30)$' then
      raise exception 'recommendation_evaluation_invalid';
    end if;
    v_obs := (v_eval->>'observedTradingDays')::integer;
    v_start := substring(coalesce(nullif(v_base->>'recommendedAt',''), v_base->>'date') from 1 for 10)::date;
    v_observed := (v_eval->>'observationAsOf')::date;
    v_entry := (v_base#>>'{snapshot,price}')::numeric;
    if v_start is null or v_entry not between 0.000001 and 1000000
       or v_observed <= v_start or v_observed > v_cutoff
       or (v_obs < 30 and v_observed <> v_cutoff)
       or abs((v_eval->>'entryPrice')::numeric - v_entry) > 0.000501
       or abs((v_eval->>'targetPrice')::numeric - v_entry * 1.05) > 0.000501
       or (v_eval->>'maxGainPct')::numeric not between -100 and 1000000
       or (v_eval->>'latestReturnPct')::numeric not between -100 and 1000000
       or (v_eval->>'latestReturnPct')::numeric > (v_eval->>'maxGainPct')::numeric
       or (v_status = 'failed' and v_obs <> 30)
       or (v_status = 'pending' and v_obs >= 30)
       or (v_status = 'success' and (v_eval->>'maxGainPct')::numeric < 5)
       or (v_status <> 'success' and (v_eval->>'maxGainPct')::numeric > 5) then
      raise exception 'recommendation_evaluation_invalid';
    end if;
    if (v_status = 'success') is distinct from (v_eval ? 'firstHitAt')
       or (v_eval ? 'firstHitAt') is distinct from (v_eval ?& array['firstHitAt','tradingDaysToHit','calendarDaysToHit'])
       or (not v_eval ? 'firstHitAt' and v_eval ?| array['tradingDaysToHit','calendarDaysToHit'])
       or (v_eval ? 'weeklyUpperFirstAt') is distinct from (v_eval ?& array['weeklyUpperFirstAt','tradingDaysToWeeklyUpper','calendarDaysToWeeklyUpper'])
       or (not v_eval ? 'weeklyUpperFirstAt' and v_eval ?| array['tradingDaysToWeeklyUpper','calendarDaysToWeeklyUpper']) then
      raise exception 'recommendation_evaluation_invalid';
    end if;
    foreach v_key in array array['firstHitAt','weeklyUpperFirstAt'] loop
      if v_eval ? v_key then
        if jsonb_typeof(v_eval->v_key) is distinct from 'string'
           or coalesce(v_eval->>v_key,'') !~ '^\d{4}-\d{2}-\d{2}$' then
          raise exception 'recommendation_evaluation_invalid';
        end if;
        v_day := (v_eval->>v_key)::date;
        if v_day <= v_start or v_day > v_observed then raise exception 'recommendation_evaluation_invalid'; end if;
        if v_key = 'firstHitAt' then
          if jsonb_typeof(v_eval->'tradingDaysToHit') is distinct from 'number'
             or jsonb_typeof(v_eval->'calendarDaysToHit') is distinct from 'number'
             or coalesce(v_eval->>'tradingDaysToHit','') !~ '^[0-9]+$'
             or (v_eval->>'tradingDaysToHit')::numeric not between 1 and v_obs
             or (v_eval->>'calendarDaysToHit')::numeric <> v_day - v_start then
            raise exception 'recommendation_evaluation_invalid';
          end if;
        else
          if jsonb_typeof(v_eval->'tradingDaysToWeeklyUpper') is distinct from 'number'
             or jsonb_typeof(v_eval->'calendarDaysToWeeklyUpper') is distinct from 'number'
             or coalesce(v_eval->>'tradingDaysToWeeklyUpper','') !~ '^[0-9]+$'
             or (v_eval->>'tradingDaysToWeeklyUpper')::numeric not between 1 and v_obs
             or (v_eval->>'calendarDaysToWeeklyUpper')::numeric <> v_day - v_start then
            raise exception 'recommendation_evaluation_invalid';
          end if;
        end if;
      end if;
    end loop;
    if (v_old->>'status' in ('success','failed') and v_old->>'status' <> v_status)
       or coalesce((v_old->>'observedTradingDays')::integer,0) > v_obs then
      raise exception 'recommendation_outcome_regression';
    end if;
    foreach v_key in array array['firstHitAt','tradingDaysToHit','calendarDaysToHit',
                                'weeklyUpperFirstAt','tradingDaysToWeeklyUpper','calendarDaysToWeeklyUpper'] loop
      if v_old->>v_key is not null and v_old->v_key is distinct from v_eval->v_key then
        raise exception 'recommendation_outcome_regression';
      end if;
    end loop;
  end loop;

  -- Independently reconcile the caller's legacy statistics against this full set.
  -- Compare rounded values to unrounded ratios with half-unit tolerance at 1dp,
  -- accommodating Python round ties without accepting arbitrary reported metrics.
  with rows as (
    select value->'previousPayload'->>'action' as action, value->'evaluation' as e
    from jsonb_array_elements(p_records)
  ), stats as (
    select count(*) as total,
      count(*) filter(where action='分批买入') as buys,
      count(*) filter(where action='分批买入' and e->>'status'='success') as successes,
      count(*) filter(where action='分批买入' and e->>'status'='failed') as failures,
      count(*) filter(where action='分批买入' and (e->>'status' in ('pending','no_data') or e->>'status' is null)) as pending,
      avg(coalesce((e->>'tradingDaysToHit')::numeric,0)) filter(where action='分批买入' and e->>'status'='success') as hit_days,
      avg(coalesce((e->>'calendarDaysToHit')::numeric,0)) filter(where action='分批买入' and e->>'status'='success') as cal_days,
      count(*) filter(where action='分批买入' and nullif(e->>'weeklyUpperFirstAt','') is not null) as weekly,
      avg(coalesce((e->>'tradingDaysToWeeklyUpper')::numeric,0)) filter(where action='分批买入' and nullif(e->>'weeklyUpperFirstAt','') is not null) as weekly_days
    from rows
  ) select jsonb_build_object(
    'criterion','分批买入后30个交易日内，盘中最高价达到指令价+5%', 'targetGainPct',5, 'windowTradingDays',30,
    'totalCommands',total,'buyCommands',buys,'resolved',successes+failures,
    'successes',successes,'failures',failures,'pending',pending,
    'successRate',successes*100.0/nullif(successes+failures,0),
    'avgTradingDaysToHit',hit_days,'avgCalendarDaysToHit',cal_days,
    'weeklyUpperHits',weekly,'weeklyUpperRate',weekly*100.0/nullif(buys,0),'avgTradingDaysToWeeklyUpper',weekly_days,
    'asOf',p_as_of,'dataAsOf',v_cutoff::text,'dataSource','legacy_eastmoney_tencent_unadjusted_daily',
    'method','legacy_30d_plus5_v1','retainedSettled',v_retained,'calendarSource',p_performance->>'calendarSource'
  ) into v_expected from stats;
  if (select array_agg(key order by key) from jsonb_object_keys(p_performance) key)
     is distinct from (select array_agg(key order by key) from jsonb_object_keys(v_expected) key) then
    raise exception 'recommendation_performance_invalid';
  end if;
  for v_key in select jsonb_object_keys(v_expected) loop
    if v_key = any(array['successRate','avgTradingDaysToHit','avgCalendarDaysToHit','weeklyUpperRate','avgTradingDaysToWeeklyUpper'])
       and v_expected->v_key <> 'null'::jsonb then
      if jsonb_typeof(p_performance->v_key) is distinct from 'number' then
        raise exception 'recommendation_performance_invalid';
      end if;
      v_value := (p_performance->>v_key)::numeric;
      if v_value < 0 or abs(v_value-(v_expected->>v_key)::numeric) > 0.050000001
         or v_value <> round(v_value,1) then raise exception 'recommendation_performance_invalid'; end if;
    elsif p_performance->v_key is distinct from v_expected->v_key then
      raise exception 'recommendation_performance_invalid';
    end if;
  end loop;

  update public.personal_strategy_recommendations r
    set payload = jsonb_set(r.payload, '{evaluation}', item.value->'evaluation', true), updated_at = now()
    from jsonb_array_elements(p_records) item(value)
    where r.owner_user_id = v_owner and r.source_id = item.value->>'sourceId'
      and r.payload->'evaluation' is distinct from item.value->'evaluation';
  get diagnostics v_count = row_count;
  update public.personal_documents d
    set payload = jsonb_set(jsonb_set(d.payload, '{performance}', p_performance, true), '{asOf}', to_jsonb(p_as_of), true),
        updated_at = now()
    where d.owner_user_id = v_owner and d.document_key = 'recommendations_meta';
  return jsonb_build_object('stored',v_n,'changed',v_count,'asOf',p_as_of);
exception
  when raise_exception then
    if sqlerrm = any(array['recommendation_auth_required','recommendation_trusted_writer_required',
      'recommendation_input_invalid','recommendation_as_of_invalid','recommendation_context_stale',
      'recommendation_coverage_invalid','recommendation_evaluation_invalid','recommendation_outcome_regression',
      'recommendation_observations_unavailable','recommendation_performance_invalid']) then raise; end if;
    raise exception 'recommendation_input_invalid';
  when others then raise exception 'recommendation_input_invalid';
end;
$$;
revoke all on function public.personal_sync_recommendation_evaluations(text,jsonb,jsonb,jsonb,text) from PUBLIC, anon, service_role;
grant execute on function public.personal_sync_recommendation_evaluations(text,jsonb,jsonb,jsonb,text) to authenticated;
comment on function public.personal_sync_recommendation_evaluations(text,jsonb,jsonb,jsonb,text) is
  'Non-AI existing-owned recommendation evaluation CAS; full-set coverage; Part4 writer gate; no analysis/profile/trade writes.';
notify pgrst, 'reload schema';
commit;
