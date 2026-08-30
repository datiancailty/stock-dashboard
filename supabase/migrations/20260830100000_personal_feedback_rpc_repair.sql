begin;

-- Forward repair for the three Part 6 feedback buttons.
-- The browser calls the versioned RPC below; the legacy name is retained as
-- a compatibility wrapper. No table privileges are granted and no trading
-- path is introduced.
create or replace function public.personal_append_strategy_feedback_v2(p_record jsonb)
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
    encode(digest(p_record::text, 'sha256'), 'hex')
  ) on conflict (owner_user_id, source_id) do nothing;
  get diagnostics inserted_count = row_count;
  return jsonb_build_object('inserted', inserted_count);
end;
$$;
revoke all on function public.personal_append_strategy_feedback_v2(jsonb) from PUBLIC, anon, service_role;
grant execute on function public.personal_append_strategy_feedback_v2(jsonb) to authenticated;

-- Keep callers that still use the original function on the repaired path.
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
revoke all on function public.personal_append_strategy_feedback(jsonb) from PUBLIC, anon, service_role;
grant execute on function public.personal_append_strategy_feedback(jsonb) to authenticated;

-- Resolve the actual pgcrypto schema instead of assuming the extension's
-- placement. This is the same locked-down schema rule used by the VPS RPCs.
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

-- Ask PostgREST to reload the function catalog after the manual SQL Editor run.
notify pgrst, 'reload schema';

commit;
