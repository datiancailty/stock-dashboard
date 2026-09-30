-- 第一步：只读预检。由用户在 Supabase SQL Editor 手动执行。
-- 仅输出结构/权限布尔值，不读取用户名、持仓、交易或密钥。
with checks(name, ok) as (
  values
  ('auth_uid', to_regprocedure('auth.uid()') is not null),
  ('active_user_guard', to_regprocedure('public.personal_current_user_is_active()') is not null),
  ('writer_credential_table', to_regclass('public.personal_part4_sync_writer_credentials') is not null),
  ('writer_owner_column', exists(select 1 from pg_attribute where attrelid=to_regclass('public.personal_part4_sync_writer_credentials') and attname='owner_user_id' and atttypid='uuid'::regtype and not attisdropped)),
  ('writer_hash_column', exists(select 1 from pg_attribute where attrelid=to_regclass('public.personal_part4_sync_writer_credentials') and attname='secret_sha256' and atttypid='text'::regtype and not attisdropped)),
  ('digest_function', to_regprocedure('extensions.digest(text,text)') is not null),
  ('existing_health_getter', to_regprocedure('public.personal_get_refresh_health()') is not null),
  ('new_table_absent', to_regclass('public.personal_part0_monitor') is null),
  ('new_getter_absent', to_regprocedure('public.personal_get_part0_monitor()') is null),
  ('new_writer_absent', to_regprocedure('public.personal_sync_part0_monitor(jsonb,text)') is null)
)
select count(*) as check_count, count(*) filter(where not ok) as failed_count,
       coalesce(jsonb_agg(name) filter(where not ok),'[]'::jsonb) as failed_checks
from checks;
