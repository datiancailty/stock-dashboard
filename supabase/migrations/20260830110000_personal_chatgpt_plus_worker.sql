-- Personal ChatGPT Plus/Codex worker bridge — forward-only migration.
-- MANUAL HOSTED SQL EXECUTION ONLY.
-- The local worker authenticates as the already-authorized dashboard user, calls
-- the private read RPCs, and writes only validated derived results through the
-- RPC below. No Platform API key, OAuth token, password, order path, or direct
-- table DML is introduced.

begin;

create table if not exists public.personal_strategy_worker_runs (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  run_id text not null check (run_id ~ '^[A-Za-z0-9._:-]{1,160}$'),
  input_sha256 text not null check (input_sha256 ~ '^[0-9a-f]{64}$'),
  output_sha256 text not null check (output_sha256 ~ '^[0-9a-f]{64}$'),
  worker_id text not null check (worker_id = 'macos-local-codex'),
  auth_mode text not null check (auth_mode = 'chatgpt_subscription'),
  model text not null check (char_length(model) between 1 and 120),
  status text not null check (status in ('ok', 'failed', 'unknown')),
  started_at timestamptz,
  finished_at timestamptz not null default now(),
  result_payload jsonb check (result_payload is null or jsonb_typeof(result_payload) = 'object'),
  health_payload jsonb not null check (jsonb_typeof(health_payload) = 'object'),
  primary key (owner_user_id, run_id)
);

alter table public.personal_strategy_worker_runs enable row level security;
revoke all on table public.personal_strategy_worker_runs from PUBLIC, anon, authenticated, service_role;
drop policy if exists personal_strategy_worker_runs_owner_select on public.personal_strategy_worker_runs;
create policy personal_strategy_worker_runs_owner_select
  on public.personal_strategy_worker_runs
  for select to authenticated
  using (owner_user_id = auth.uid());
create index if not exists personal_strategy_worker_runs_owner_time_idx
  on public.personal_strategy_worker_runs (owner_user_id, finished_at desc, run_id);

create or replace function public.personal_publish_strategy_worker_result(
  p_run_id text,
  p_input_sha256 text,
  p_output_sha256 text,
  p_analysis jsonb,
  p_health jsonb
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
declare
  item jsonb;
  code text;
  action text;
  status_text text;
  model_text text;
  auth_mode_text text;
  worker_id_text text;
  checked_at_text text;
  reason_text text;
  now_value timestamptz := now();
  analysis_written boolean := false;
  brief_code text;
  brief_action text;
  confidence_value numeric;
begin
  if not public.personal_current_user_is_active() then
    raise exception 'personal_auth_required';
  end if;
  if p_run_id is null or p_run_id !~ '^[A-Za-z0-9._:-]{1,160}$' then
    raise exception 'worker_run_id_invalid';
  end if;
  if p_input_sha256 is null or p_input_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'worker_input_hash_invalid';
  end if;
  if p_output_sha256 is null or p_output_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'worker_output_hash_invalid';
  end if;
  if p_health is null or jsonb_typeof(p_health) <> 'object' then
    raise exception 'worker_health_must_be_object';
  end if;
  if pg_column_size(p_health) > 16000 then
    raise exception 'worker_health_too_large';
  end if;

  status_text := btrim(p_health->>'status');
  model_text := btrim(p_health->>'model');
  auth_mode_text := btrim(p_health->>'authMode');
  worker_id_text := btrim(p_health->>'workerId');
  checked_at_text := btrim(p_health->>'checkedAt');
  reason_text := btrim(p_health->>'reason');
  if status_text not in ('ok', 'failed', 'unknown') then
    raise exception 'worker_health_status_invalid';
  end if;
  if model_text is null or char_length(model_text) not between 1 and 120 then
    raise exception 'worker_model_invalid';
  end if;
  if auth_mode_text <> 'chatgpt_subscription' then
    raise exception 'worker_auth_mode_invalid';
  end if;
  if worker_id_text <> 'macos-local-codex' then
    raise exception 'worker_id_invalid';
  end if;
  if checked_at_text is null or char_length(checked_at_text) > 64 then
    raise exception 'worker_checked_at_invalid';
  end if;
  if reason_text is null or char_length(reason_text) not between 1 and 240 then
    raise exception 'worker_reason_invalid';
  end if;
  if coalesce(p_health->>'platformApiKeyUsed', 'false') <> 'false' then
    raise exception 'worker_platform_key_forbidden';
  end if;
  if p_health->>'schemaVersion' is distinct from '2'
     or p_health->>'provider' is distinct from 'openai-codex'
     or p_health->>'check' is distinct from 'private_local_strategy_worker'
     or p_health->>'runId' is distinct from p_run_id
     or p_health->>'inputSha256' is distinct from p_input_sha256
     or p_health->>'outputSha256' is distinct from p_output_sha256 then
    raise exception 'worker_health_hash_mismatch';
  end if;

  if status_text = 'ok' then
    if p_analysis is null or jsonb_typeof(p_analysis) <> 'object' then
      raise exception 'worker_analysis_required';
    end if;
    if pg_column_size(p_analysis) > 120000 then
      raise exception 'worker_analysis_too_large';
    end if;
    if p_analysis->>'schemaVersion' is distinct from '2'
       or p_analysis->>'status' is distinct from 'success'
       or p_analysis->>'provider' is distinct from 'openai-codex'
       or p_analysis->>'authMode' is distinct from 'chatgpt_subscription'
       or p_analysis->>'runId' is distinct from p_run_id
       or p_analysis->>'inputSha256' is distinct from p_input_sha256
       or p_analysis->>'model' is distinct from model_text then
      raise exception 'worker_analysis_metadata_invalid';
    end if;
    if p_analysis->>'profileSummary' is null
       or char_length(p_analysis->>'profileSummary') not between 1 and 1500 then
      raise exception 'worker_profile_summary_invalid';
    end if;
    if jsonb_typeof(p_analysis->'learnedRules') is distinct from 'array' then
      raise exception 'worker_learned_rules_invalid';
    end if;
    if jsonb_array_length(p_analysis->'learnedRules') > 12 then
      raise exception 'worker_learned_rules_invalid';
    end if;
    for item in select value from jsonb_array_elements(p_analysis->'learnedRules') loop
      if jsonb_typeof(item) <> 'string'
         or char_length(item #>> '{}') not between 1 and 500 then
        raise exception 'worker_learned_rule_invalid';
      end if;
    end loop;
    if jsonb_typeof(p_analysis->'recordInsights') is distinct from 'object' then
      raise exception 'worker_record_insights_invalid';
    end if;
    if (select count(*) from jsonb_object_keys(p_analysis->'recordInsights')) > 20 then
      raise exception 'worker_record_insights_invalid';
    end if;
    for item in select value from jsonb_each(p_analysis->'recordInsights') loop
      if jsonb_typeof(item) <> 'string'
         or char_length(item #>> '{}') not between 1 and 500 then
        raise exception 'worker_record_insight_invalid';
      end if;
    end loop;

    if jsonb_typeof(p_analysis->'briefCommand') <> 'object' then
      raise exception 'worker_brief_command_invalid';
    end if;
    brief_code := btrim(coalesce(p_analysis->'briefCommand'->>'code', ''));
    brief_action := btrim(coalesce(p_analysis->'briefCommand'->>'action', ''));
    if brief_code <> '' and brief_code !~ '^[0-9]{6}$' then
      raise exception 'worker_brief_code_invalid';
    end if;
    if brief_code <> '' and not exists (
      select 1 from public.personal_watchlist_items w
       where w.owner_user_id = auth.uid() and w.stock_code = brief_code
    ) then
      raise exception 'worker_brief_code_not_in_watchlist';
    end if;
    if brief_action not in ('分批买入', '暂不买入', '当前不买')
       or (brief_action = '分批买入' and brief_code = '') then
      raise exception 'worker_brief_action_invalid';
    end if;
    if p_analysis->'briefCommand'->>'reason' is null
       or char_length(p_analysis->'briefCommand'->>'reason') not between 1 and 350
       or p_analysis->'briefCommand'->>'condition' is null
       or char_length(p_analysis->'briefCommand'->>'condition') not between 1 and 250 then
      raise exception 'worker_brief_text_invalid';
    end if;
    begin
      confidence_value := (p_analysis->'briefCommand'->>'confidence')::numeric;
    exception when others then
      raise exception 'worker_brief_confidence_invalid';
    end;
    if confidence_value is null or confidence_value < 0 or confidence_value > 100 then
      raise exception 'worker_brief_confidence_invalid';
    end if;

    if jsonb_typeof(p_analysis->'advice') is distinct from 'array' then
      raise exception 'worker_advice_invalid';
    end if;
    if jsonb_array_length(p_analysis->'advice') > 30 then
      raise exception 'worker_advice_invalid';
    end if;
    for item in select value from jsonb_array_elements(p_analysis->'advice') loop
      if jsonb_typeof(item) <> 'object' then
        raise exception 'worker_advice_item_invalid';
      end if;
      code := btrim(coalesce(item->>'code', ''));
      action := btrim(coalesce(item->>'action', ''));
      if code !~ '^[0-9]{6}$' or not exists (
        select 1 from public.personal_watchlist_items w
         where w.owner_user_id = auth.uid() and w.stock_code = code
      ) then
        raise exception 'worker_advice_code_invalid';
      end if;
      if action not in (
        '继续观察', '等待接近5%', '可分批买入', '高性价比分批买',
        '小仓分批/等待', '做T卖出观察', '卖出区提醒', '暂不追入', '等待正式数据'
      ) then
        raise exception 'worker_advice_action_invalid';
      end if;
      if item->>'reason' is null or char_length(item->>'reason') not between 1 and 600 then
        raise exception 'worker_advice_reason_invalid';
      end if;
      begin
        confidence_value := (item->>'confidence')::numeric;
      exception when others then
        raise exception 'worker_advice_confidence_invalid';
      end;
      if confidence_value is null or confidence_value < 0 or confidence_value > 100 then
        raise exception 'worker_advice_confidence_invalid';
      end if;
    end loop;

    insert into public.personal_documents (
      owner_user_id, document_key, payload, source_path, source_sha256, imported_at, updated_at
    ) values (
      auth.uid(), 'strategy_analysis', p_analysis, 'local/codex-worker/strategy-analysis',
      p_output_sha256, now_value, now_value
    )
    on conflict (owner_user_id, document_key) do update set
      payload = excluded.payload,
      source_path = excluded.source_path,
      source_sha256 = excluded.source_sha256,
      updated_at = excluded.updated_at;
    analysis_written := true;
  elsif p_analysis is not null then
    raise exception 'worker_failed_run_cannot_write_analysis';
  end if;

  insert into public.personal_documents (
    owner_user_id, document_key, payload, source_path, source_sha256, imported_at, updated_at
  ) values (
    auth.uid(), 'strategy_api_health', p_health, 'local/codex-worker/strategy-api-health',
    p_output_sha256, now_value, now_value
  )
  on conflict (owner_user_id, document_key) do update set
    payload = excluded.payload,
    source_path = excluded.source_path,
    source_sha256 = excluded.source_sha256,
    updated_at = excluded.updated_at;

  insert into public.personal_strategy_worker_runs (
    owner_user_id, run_id, input_sha256, output_sha256, worker_id, auth_mode,
    model, status, started_at, finished_at, result_payload, health_payload
  ) values (
    auth.uid(), p_run_id, p_input_sha256, p_output_sha256, worker_id_text,
    auth_mode_text, model_text, status_text, null, now_value, p_analysis, p_health
  )
  on conflict (owner_user_id, run_id) do update set
    input_sha256 = excluded.input_sha256,
    output_sha256 = excluded.output_sha256,
    worker_id = excluded.worker_id,
    auth_mode = excluded.auth_mode,
    model = excluded.model,
    status = excluded.status,
    finished_at = excluded.finished_at,
    result_payload = excluded.result_payload,
    health_payload = excluded.health_payload;

  return jsonb_build_object(
    'run_id', p_run_id,
    'status', status_text,
    'analysis_written', analysis_written,
    'health_written', true
  );
end;
$$;

revoke all on function public.personal_publish_strategy_worker_result(text, text, text, jsonb, jsonb)
  from PUBLIC, anon, service_role;
grant execute on function public.personal_publish_strategy_worker_result(text, text, text, jsonb, jsonb)
  to authenticated;

notify pgrst, 'reload schema';

commit;
