-- Stock dashboard username-auth rate-limit support.
--
-- STATUS: REVIEWED LOCAL CANDIDATE / MANUAL HOSTED SQL EXECUTION ONLY.
-- Apply only from the Supabase SQL Editor after reviewing this exact file.
-- The Edge Functions store only keyed hashes, never raw IP addresses,
-- usernames, passwords or email addresses. This migration does not create
-- users, send recovery mail, or change the Supabase Auth password policy.

begin;

create table if not exists public.app_auth_rate_limit_windows (
  rate_key text primary key,
  window_started_at timestamptz not null,
  failed_count smallint not null default 0
    check (failed_count between 0 and 5),
  updated_at timestamptz not null default now(),
  constraint app_auth_rate_limit_key_format check (
    rate_key ~ '^(ip|user|combo):[0-9a-f]{64}$'
  )
);

alter table public.app_auth_rate_limit_windows enable row level security;
revoke all on table public.app_auth_rate_limit_windows
  from PUBLIC, anon, authenticated, service_role;

create or replace function public.app_auth_rate_limit_admit(
  p_ip_hash text,
  p_username_hash text
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  key_value text;
  row_value public.app_auth_rate_limit_windows%rowtype;
  now_value timestamptz := clock_timestamp();
  blocked boolean := false;
begin
  if p_ip_hash is null or p_username_hash is null
    or p_ip_hash !~ '^[0-9a-f]{64}$'
    or p_username_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'Rate-limit key is invalid';
  end if;

  foreach key_value in array array[
    'ip:' || p_ip_hash,
    'user:' || p_username_hash,
    'combo:' || encode(
      extensions.digest(convert_to(p_ip_hash || ':' || p_username_hash, 'utf8'), 'sha256'),
      'hex'
    )
  ] loop
    select * into row_value
      from public.app_auth_rate_limit_windows
     where rate_key = key_value
     for update;
    if found
      and row_value.window_started_at > now_value - interval '15 minutes'
      and row_value.failed_count >= 5 then
      blocked := true;
    end if;
  end loop;

  return jsonb_build_object('allowed', not blocked);
end;
$$;

create or replace function public.app_auth_rate_limit_record_failure(
  p_ip_hash text,
  p_username_hash text
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  key_value text;
  now_value timestamptz := clock_timestamp();
  combo_hash text;
begin
  if p_ip_hash is null or p_username_hash is null
    or p_ip_hash !~ '^[0-9a-f]{64}$'
    or p_username_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'Rate-limit key is invalid';
  end if;

  combo_hash := encode(
    extensions.digest(convert_to(p_ip_hash || ':' || p_username_hash, 'utf8'), 'sha256'),
    'hex'
  );

  foreach key_value in array array[
    'ip:' || p_ip_hash,
    'user:' || p_username_hash,
    'combo:' || combo_hash
  ] loop
    insert into public.app_auth_rate_limit_windows (
      rate_key, window_started_at, failed_count, updated_at
    ) values (
      key_value, now_value, 1, now_value
    )
    on conflict (rate_key) do update set
      window_started_at = case
        when public.app_auth_rate_limit_windows.window_started_at
          <= now_value - interval '15 minutes' then now_value
        else public.app_auth_rate_limit_windows.window_started_at
      end,
      failed_count = case
        when public.app_auth_rate_limit_windows.window_started_at
          <= now_value - interval '15 minutes' then 1
        else least(public.app_auth_rate_limit_windows.failed_count + 1, 5)
      end,
      updated_at = now_value;
  end loop;

  delete from public.app_auth_rate_limit_windows
   where updated_at < now_value - interval '2 days';

  return jsonb_build_object('recorded', true);
end;
$$;

revoke all on function public.app_auth_rate_limit_admit(text, text)
  from PUBLIC, anon, authenticated, service_role;
revoke all on function public.app_auth_rate_limit_record_failure(text, text)
  from PUBLIC, anon, authenticated, service_role;
grant execute on function public.app_auth_rate_limit_admit(text, text)
  to service_role;
grant execute on function public.app_auth_rate_limit_record_failure(text, text)
  to service_role;

commit;
