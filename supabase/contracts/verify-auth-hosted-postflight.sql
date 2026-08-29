-- Username-auth Hosted postflight — READ ONLY.
--
-- Run in Supabase SQL Editor after the rate-limit migration. It returns one
-- aggregate row and does not return rate keys, users, emails or credentials.

with checks as (
  select 'rate_limit_table_exists'::text as check_name,
         to_regclass('public.app_auth_rate_limit_windows') is not null as passed
  union all
  select 'rate_limit_table_has_rls',
         not exists (
           select 1
             from pg_class as c
             join pg_namespace as n on n.oid = c.relnamespace
            where n.nspname = 'public'
              and c.relname = 'app_auth_rate_limit_windows'
              and c.relrowsecurity is not true
         )
  union all
  select 'rate_limit_table_has_no_direct_privileges',
         has_table_privilege('anon', 'public.app_auth_rate_limit_windows', 'SELECT') = false
         and has_table_privilege('anon', 'public.app_auth_rate_limit_windows', 'INSERT') = false
         and has_table_privilege('authenticated', 'public.app_auth_rate_limit_windows', 'SELECT') = false
         and has_table_privilege('authenticated', 'public.app_auth_rate_limit_windows', 'INSERT') = false
         and has_table_privilege('service_role', 'public.app_auth_rate_limit_windows', 'SELECT') = false
         and has_table_privilege('service_role', 'public.app_auth_rate_limit_windows', 'INSERT') = false
  union all
  select 'rate_limit_admit_function_exists',
         to_regprocedure('public.app_auth_rate_limit_admit(text,text)') is not null
  union all
  select 'rate_limit_record_function_exists',
         to_regprocedure('public.app_auth_rate_limit_record_failure(text,text)') is not null
  union all
  select 'rate_limit_admit_service_role_only',
         has_function_privilege('service_role', 'public.app_auth_rate_limit_admit(text,text)', 'EXECUTE') = true
         and has_function_privilege('anon', 'public.app_auth_rate_limit_admit(text,text)', 'EXECUTE') = false
         and has_function_privilege('authenticated', 'public.app_auth_rate_limit_admit(text,text)', 'EXECUTE') = false
  union all
  select 'rate_limit_record_service_role_only',
         has_function_privilege('service_role', 'public.app_auth_rate_limit_record_failure(text,text)', 'EXECUTE') = true
         and has_function_privilege('anon', 'public.app_auth_rate_limit_record_failure(text,text)', 'EXECUTE') = false
         and has_function_privilege('authenticated', 'public.app_auth_rate_limit_record_failure(text,text)', 'EXECUTE') = false
  union all
  select 'rate_limit_schema_is_hash_only',
         not exists (
           select 1
             from information_schema.columns
            where table_schema = 'public'
              and table_name = 'app_auth_rate_limit_windows'
              and lower(column_name) in (
                'password', 'email', 'username', 'ip_address', 'raw_ip',
                'service_role_key', 'provider_api_key', 'refresh_token'
              )
         )
)
select count(*) as total_checks,
       count(*) filter (where passed) as passed_checks,
       count(*) filter (where not passed) as failed_checks,
       coalesce(
         jsonb_agg(
           jsonb_build_object('check', check_name, 'passed', passed)
           order by check_name
         ) filter (where not passed),
         '[]'::jsonb
       ) as failed_check_names
  from checks;
