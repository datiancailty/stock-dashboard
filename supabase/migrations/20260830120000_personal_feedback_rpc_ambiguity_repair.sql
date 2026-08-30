-- Part 6 feedback RPC ambiguity repair — forward-only migration.
-- MANUAL HOSTED SQL EXECUTION ONLY. Do not rerun earlier migrations.
--
-- The prior versioned feedback RPC had PL/pgSQL locals named `source_id`,
-- `recommendation_id`, and `code`, matching table columns. PostgreSQL can
-- resolve `ON CONFLICT (owner_user_id, source_id)` ambiguously at runtime
-- (SQLSTATE 42702). Use distinct v_* locals and the named primary-key
-- constraint so a browser feedback click cannot reach an ambiguous column
-- reference. No historical feedback is edited, deleted, or replayed.

begin;

create or replace function public.personal_append_strategy_feedback_v2(p_record jsonb)
returns jsonb
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
declare
  v_source_id text;
  v_recommendation_id text;
  v_status_text text;
  v_code text;
  v_inserted_count integer;
begin
  if not public.personal_current_user_is_active() then
    raise exception 'personal_auth_required';
  end if;
  if p_record is null or jsonb_typeof(p_record) <> 'object' then
    raise exception 'feedback_must_be_object';
  end if;

  v_source_id := btrim(p_record->>'id');
  v_recommendation_id := btrim(p_record->>'recommendationId');
  v_status_text := btrim(p_record->>'status');
  v_code := btrim(coalesce(p_record->>'code', ''));

  if v_source_id is null or v_source_id !~ '^[A-Za-z0-9._:-]{1,160}$' then
    raise exception 'feedback_id_invalid';
  end if;
  if v_recommendation_id is null or char_length(v_recommendation_id) not between 1 and 200 then
    raise exception 'feedback_recommendation_invalid';
  end if;
  if v_status_text not in ('executed', 'not_executed', 'deferred') then
    raise exception 'feedback_status_invalid';
  end if;
  if v_code <> '' and v_code !~ '^\d{6}$' then
    raise exception 'feedback_code_invalid';
  end if;

  insert into public.personal_strategy_feedback (
    owner_user_id, source_id, recommendation_id, stock_code, payload, source_sha256
  ) values (
    auth.uid(), v_source_id, v_recommendation_id, nullif(v_code, ''), p_record,
    encode(digest(p_record::text, 'sha256'), 'hex')
  ) on conflict on constraint personal_strategy_feedback_pkey do nothing;

  get diagnostics v_inserted_count = row_count;
  return jsonb_build_object('inserted', v_inserted_count);
end;
$$;

-- Keep old callers on the same repaired implementation without widening any
-- table privilege or introducing a direct DML route.
create or replace function public.personal_append_strategy_feedback(p_record jsonb)
returns jsonb
language plpgsql
volatile
security definer
set search_path = pg_catalog, public
as $$
begin
  return public.personal_append_strategy_feedback_v2(p_record);
end;
$$;

revoke all on function public.personal_append_strategy_feedback_v2(jsonb)
  from PUBLIC, anon, service_role;
grant execute on function public.personal_append_strategy_feedback_v2(jsonb)
  to authenticated;
revoke all on function public.personal_append_strategy_feedback(jsonb)
  from PUBLIC, anon, service_role;
grant execute on function public.personal_append_strategy_feedback(jsonb)
  to authenticated;

-- Re-discover the hosted pgcrypto schema. The function keeps a constrained
-- search_path: pg_catalog + exactly the digest schema + public.
do $personal_feedback_digest_path$
declare
  digest_schema text;
begin
  select namespace.nspname
    into digest_schema
    from pg_proc as proc
    join pg_namespace as namespace on namespace.oid = proc.pronamespace
   where proc.proname = 'digest'
     and proc.prokind = 'f'
     and proc.pronargs = 2
     and proc.proargtypes[0] = 'text'::regtype::oid
     and proc.proargtypes[1] = 'text'::regtype::oid
   order by case when namespace.nspname = 'extensions' then 0 else 1 end,
            namespace.nspname
   limit 1;

  if digest_schema is null then
    raise exception 'pgcrypto digest(text, text) is not available in the hosted database';
  end if;

  execute format(
    'alter function public.personal_append_strategy_feedback_v2(jsonb) set search_path = pg_catalog, %I, public',
    digest_schema
  );
end;
$personal_feedback_digest_path$;

notify pgrst, 'reload schema';

commit;
