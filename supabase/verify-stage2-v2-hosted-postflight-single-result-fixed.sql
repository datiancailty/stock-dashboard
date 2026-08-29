-- Hosted protocol-v2 postflight (READ ONLY).
-- Run this in Supabase Dashboard SQL Editor after the full
-- 20260825003000_vps_sync_gateway_protocol_v2.sql returned success.
--
-- It reads PostgreSQL catalogs and bounded control metadata only. It does not
-- insert/update/delete rows, change settings, call the sync RPCs, or expose
-- credentials, request bodies, account values, stock values, or hash contents.

with
expected_tables(table_name) as (
  values
    ('vps_admins'::text),
    ('vps_control_settings'::text),
    ('vps_whitelist_revisions'::text),
    ('vps_whitelist_revision_items'::text),
    ('vps_runtime_snapshot'::text),
    ('vps_symbol_states'::text),
    ('vps_sim_positions'::text),
    ('vps_runtime_events'::text),
    ('vps_sync_acks'::text),
    ('vps_sync_devices'::text),
    ('vps_sync_nonces'::text),
    ('vps_sync_request_receipts'::text),
    ('vps_sync_ack_receipts'::text),
    ('vps_control_contract_audit'::text)
),
expected_v2_columns(table_name, column_name) as (
  values
    ('vps_control_settings', 'gateway_protocol_version'),
    ('vps_control_settings', 'target_device_id'),
    ('vps_control_settings', 'expected_adapter_id'),
    ('vps_control_settings', 'expected_source_policy_sha256'),
    ('vps_control_settings', 'required_snapshot_id'),
    ('vps_control_settings', 'required_snapshot_sha256'),
    ('vps_control_settings', 'revision_ttl_seconds'),
    ('vps_whitelist_revisions', 'contract_protocol_version'),
    ('vps_whitelist_revisions', 'target_device_id'),
    ('vps_whitelist_revisions', 'contract_adapter_id'),
    ('vps_whitelist_revisions', 'contract_mode'),
    ('vps_whitelist_revisions', 'source_policy_sha256'),
    ('vps_whitelist_revisions', 'members_sha256'),
    ('vps_whitelist_revisions', 'required_snapshot_id'),
    ('vps_whitelist_revisions', 'required_snapshot_sha256'),
    ('vps_whitelist_revisions', 'contract_created_at'),
    ('vps_whitelist_revisions', 'contract_expires_at'),
    ('vps_whitelist_revisions', 'control_payload_sha256'),
    ('vps_whitelist_revisions', 'control_raw_contract'),
    ('vps_whitelist_revisions', 'control_raw_contract_sha256'),
    ('vps_runtime_snapshot', 'active_control_payload_sha256'),
    ('vps_runtime_snapshot', 'active_control_raw_contract_sha256'),
    ('vps_runtime_snapshot', 'active_members_sha256'),
    ('vps_runtime_snapshot', 'active_snapshot_id'),
    ('vps_runtime_snapshot', 'active_snapshot_sha256'),
    ('vps_sync_acks', 'vps_ack_id'),
    ('vps_sync_acks', 'control_payload_sha256'),
    ('vps_sync_acks', 'control_raw_contract_sha256'),
    ('vps_sync_acks', 'members_sha256'),
    ('vps_sync_acks', 'required_snapshot_id'),
    ('vps_sync_acks', 'required_snapshot_sha256'),
    ('vps_sync_acks', 'adapter_id'),
    ('vps_sync_acks', 'mode')
),
expected_functions(function_signature, expected_security_definer) as (
  values
    ('public.vps_is_admin()'::text, true),
    ('public.vps_submit_whitelist_revision(text[],text)'::text, true),
    ('public.vps_set_control_snapshot_requirement(text,text,integer)'::text, true),
    ('public.vps_sync_consume_nonce(text,text,timestamptz)'::text, true),
    ('public.vps_sync_get_pending_revision(text)'::text, true),
    ('public.vps_sync_ingest_report_v1(text,jsonb)'::text, true),
    ('public.vps_sync_ingest_report(text,jsonb)'::text, true),
    ('public.vps_sync_pull_request(text,text,text)'::text, true),
    ('public.vps_sync_publish_request(text,text,text,jsonb)'::text, true)
),
function_catalog as (
  select
    e.function_signature,
    e.expected_security_definer,
    to_regprocedure(e.function_signature)::oid as function_oid
  from expected_functions e
),
expected_triggers(trigger_name, table_name, function_name) as (
  values
    ('vps_v2_revision_immutability'::text, 'vps_whitelist_revisions'::text, 'vps_guard_v2_revision_immutability'::text),
    ('vps_v2_revision_item_mutation'::text, 'vps_whitelist_revision_items'::text, 'vps_guard_v2_revision_item_mutation'::text),
    ('vps_v2_revision_item_consistency'::text, 'vps_whitelist_revision_items'::text, 'vps_assert_v2_revision_item_consistency'::text)
),
checks(check_group, object_name, observed, expected, pass) as (
  select
    'table', e.table_name,
    case when c.oid is null then 'MISSING' else 'PRESENT' end,
    'PRESENT',
    c.oid is not null
  from expected_tables e
  left join pg_class c on c.oid = to_regclass('public.' || e.table_name)

  union all
  select
    'rls', e.table_name,
    case
      when c.oid is null then 'MISSING'
      when c.relrowsecurity then 'RLS_ON'
      else 'RLS_OFF'
    end,
    'RLS_ON',
    c.oid is not null and c.relrowsecurity
  from expected_tables e
  left join pg_class c on c.oid = to_regclass('public.' || e.table_name)

  union all
  select
    'force_rls', e.table_name,
    case
      when c.oid is null then 'MISSING'
      when c.relforcerowsecurity then 'FORCE_RLS_ON'
      else 'FORCE_RLS_OFF'
    end,
    'FORCE_RLS_OFF',
    c.oid is not null and not c.relforcerowsecurity
  from expected_tables e
  left join pg_class c on c.oid = to_regclass('public.' || e.table_name)

  union all
  select
    'v2_column', e.table_name || '.' || e.column_name,
    case when c.ordinal_position is null then 'MISSING' else 'PRESENT' end,
    'PRESENT',
    c.ordinal_position is not null
  from expected_v2_columns e
  left join information_schema.columns c
    on c.table_schema = 'public'
   and c.table_name = e.table_name
   and c.column_name = e.column_name

  union all
  select
    'extension', 'pgcrypto',
    case when exists (select 1 from pg_extension where extname = 'pgcrypto') then 'PRESENT' else 'MISSING' end,
    'PRESENT',
    exists (select 1 from pg_extension where extname = 'pgcrypto')

  union all
  select
    'function', f.function_signature,
    case when p.oid is null then 'MISSING' else 'PRESENT' end,
    'PRESENT',
    p.oid is not null
  from function_catalog f
  left join pg_proc p on p.oid = f.function_oid

  union all
  select
    'function_security_definer', f.function_signature,
    case
      when p.oid is null then 'MISSING'
      when p.prosecdef then 'SECURITY_DEFINER'
      else 'SECURITY_INVOKER'
    end,
    case when f.expected_security_definer then 'SECURITY_DEFINER' else 'SECURITY_INVOKER' end,
    p.oid is not null and p.prosecdef = f.expected_security_definer
  from function_catalog f
  left join pg_proc p on p.oid = f.function_oid

  union all
  select
    'function_search_path', f.function_signature,
    case
      when p.oid is null then 'MISSING'
      when exists (
        select 1
        from unnest(coalesce(p.proconfig, array[]::text[])) as cfg(setting)
        where replace(cfg.setting, ' ', '') = 'search_path=pg_catalog,public'
      ) then 'pg_catalog_public'
      else 'UNSAFE_OR_UNSET'
    end,
    'pg_catalog_public',
    p.oid is not null and exists (
      select 1
      from unnest(coalesce(p.proconfig, array[]::text[])) as cfg(setting)
      where replace(cfg.setting, ' ', '') = 'search_path=pg_catalog,public'
    )
  from function_catalog f
  left join pg_proc p on p.oid = f.function_oid

  union all
  select
    'function_owner', f.function_signature,
    case
      when p.oid is null then 'MISSING'
      when pg_get_userbyid(p.proowner) in ('anon', 'authenticated', 'service_role') then 'CLIENT_ROLE_OWNER'
      else 'NON_CLIENT_ROLE_OWNER'
    end,
    'NON_CLIENT_ROLE_OWNER',
    p.oid is not null and pg_get_userbyid(p.proowner) not in ('anon', 'authenticated', 'service_role')
  from function_catalog f
  left join pg_proc p on p.oid = f.function_oid

  union all
  select
    'trigger', e.table_name || '.' || e.trigger_name,
    case
      when t.oid is null then 'MISSING'
      when t.tgenabled <> 'O' then 'DISABLED'
      when n.nspname <> 'public' or p.proname <> e.function_name then 'WRONG_FUNCTION'
      else 'PRESENT_ENABLED'
    end,
    'PRESENT_ENABLED',
    t.oid is not null
      and t.tgenabled = 'O'
      and n.nspname = 'public'
      and p.proname = e.function_name
  from expected_triggers e
  left join pg_trigger t
    on t.tgname = e.trigger_name
   and t.tgrelid = to_regclass('public.' || e.table_name)
   and not t.tgisinternal
  left join pg_proc p on p.oid = t.tgfoid
  left join pg_namespace n on n.oid = p.pronamespace

  union all
  select
    'policy', 'vps_control_contract_audit_admin_select',
    case when exists (
      select 1
      from pg_policies p
      where p.schemaname = 'public'
        and p.tablename = 'vps_control_contract_audit'
        and p.policyname = 'vps_control_contract_audit_admin_select'
        and p.cmd = 'SELECT'
        and 'authenticated' = any(p.roles)
        and p.qual like '%vps_is_admin%'
    ) then 'PRESENT_ADMIN_SELECT' else 'MISSING_OR_WRONG' end,
    'PRESENT_ADMIN_SELECT',
    exists (
      select 1
      from pg_policies p
      where p.schemaname = 'public'
        and p.tablename = 'vps_control_contract_audit'
        and p.policyname = 'vps_control_contract_audit_admin_select'
        and p.cmd = 'SELECT'
        and 'authenticated' = any(p.roles)
        and p.qual like '%vps_is_admin%'
    )

  union all
  select
    'policy', 'v2_receipt_tables_have_no_policy',
    case when not exists (
      select 1
      from pg_policies p
      where p.schemaname = 'public'
        and p.tablename in ('vps_sync_request_receipts', 'vps_sync_ack_receipts')
    ) then 'NO_POLICIES' else 'POLICY_PRESENT_REVIEW' end,
    'NO_POLICIES',
    not exists (
      select 1
      from pg_policies p
      where p.schemaname = 'public'
        and p.tablename in ('vps_sync_request_receipts', 'vps_sync_ack_receipts')
    )

  union all
  select
    'index', 'vps_sync_acks_vps_ack_id_unique',
    case when to_regclass('public.vps_sync_acks_vps_ack_id_unique') is null then 'MISSING' else 'PRESENT' end,
    'PRESENT',
    to_regclass('public.vps_sync_acks_vps_ack_id_unique') is not null

  union all
  select
    'primary_key', e.table_name,
    case when exists (
      select 1 from pg_constraint k
      where k.conrelid = to_regclass('public.' || e.table_name)
        and k.contype = 'p'
    ) then 'PRESENT' else 'MISSING' end,
    'PRESENT',
    exists (
      select 1 from pg_constraint k
      where k.conrelid = to_regclass('public.' || e.table_name)
        and k.contype = 'p'
    )
  from (values
    ('vps_sync_request_receipts'::text),
    ('vps_sync_ack_receipts'::text),
    ('vps_control_contract_audit'::text)
  ) as e(table_name)

  union all
  select
    'settings_contract', 'vps_control_settings.safe_defaults',
    case when exists (
      select 1
      from public.vps_control_settings s
      where s.id = true
        and s.gateway_protocol_version = 2
        and s.max_active_symbols between 1 and 50
        and s.strategy_timezone = 'Asia/Shanghai'
        and s.default_mode = 'DRY_RUN'
        and s.target_device_id ~ '^[a-z0-9][a-z0-9._-]{2,79}$'
        and s.expected_adapter_id = 'v31f-15m-miaoxiang-sim-adapter'
        and s.expected_source_policy_sha256 ~ '^[0-9a-f]{64}$'
        and s.revision_ttl_seconds between 300 and 2592000
        and (
          (s.required_snapshot_id is null and s.required_snapshot_sha256 is null)
          or (
            s.required_snapshot_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
            and s.required_snapshot_sha256 ~ '^[0-9a-f]{64}$'
          )
        )
    ) then 'SAFE_CONTRACT' else 'INVALID_CONTRACT' end,
    'SAFE_CONTRACT',
    exists (
      select 1
      from public.vps_control_settings s
      where s.id = true
        and s.gateway_protocol_version = 2
        and s.max_active_symbols between 1 and 50
        and s.strategy_timezone = 'Asia/Shanghai'
        and s.default_mode = 'DRY_RUN'
        and s.target_device_id ~ '^[a-z0-9][a-z0-9._-]{2,79}$'
        and s.expected_adapter_id = 'v31f-15m-miaoxiang-sim-adapter'
        and s.expected_source_policy_sha256 ~ '^[0-9a-f]{64}$'
        and s.revision_ttl_seconds between 300 and 2592000
        and (
          (s.required_snapshot_id is null and s.required_snapshot_sha256 is null)
          or (
            s.required_snapshot_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$'
            and s.required_snapshot_sha256 ~ '^[0-9a-f]{64}$'
          )
        )
    )
),
expected_function_privileges(function_signature, role_name, should_execute) as (
  values
    ('public.vps_is_admin()'::text, 'anon'::name, false),
    ('public.vps_is_admin()'::text, 'authenticated'::name, true),
    ('public.vps_is_admin()'::text, 'service_role'::name, false),
    ('public.vps_submit_whitelist_revision(text[],text)'::text, 'anon'::name, false),
    ('public.vps_submit_whitelist_revision(text[],text)'::text, 'authenticated'::name, true),
    ('public.vps_submit_whitelist_revision(text[],text)'::text, 'service_role'::name, false),
    ('public.vps_set_control_snapshot_requirement(text,text,integer)'::text, 'anon'::name, false),
    ('public.vps_set_control_snapshot_requirement(text,text,integer)'::text, 'authenticated'::name, true),
    ('public.vps_set_control_snapshot_requirement(text,text,integer)'::text, 'service_role'::name, false),
    ('public.vps_sync_consume_nonce(text,text,timestamptz)'::text, 'anon'::name, false),
    ('public.vps_sync_consume_nonce(text,text,timestamptz)'::text, 'authenticated'::name, false),
    ('public.vps_sync_consume_nonce(text,text,timestamptz)'::text, 'service_role'::name, true),
    ('public.vps_sync_get_pending_revision(text)'::text, 'anon'::name, false),
    ('public.vps_sync_get_pending_revision(text)'::text, 'authenticated'::name, false),
    ('public.vps_sync_get_pending_revision(text)'::text, 'service_role'::name, false),
    ('public.vps_sync_ingest_report_v1(text,jsonb)'::text, 'anon'::name, false),
    ('public.vps_sync_ingest_report_v1(text,jsonb)'::text, 'authenticated'::name, false),
    ('public.vps_sync_ingest_report_v1(text,jsonb)'::text, 'service_role'::name, false),
    ('public.vps_sync_ingest_report(text,jsonb)'::text, 'anon'::name, false),
    ('public.vps_sync_ingest_report(text,jsonb)'::text, 'authenticated'::name, false),
    ('public.vps_sync_ingest_report(text,jsonb)'::text, 'service_role'::name, false),
    ('public.vps_sync_pull_request(text,text,text)'::text, 'anon'::name, false),
    ('public.vps_sync_pull_request(text,text,text)'::text, 'authenticated'::name, false),
    ('public.vps_sync_pull_request(text,text,text)'::text, 'service_role'::name, true),
    ('public.vps_sync_publish_request(text,text,text,jsonb)'::text, 'anon'::name, false),
    ('public.vps_sync_publish_request(text,text,text,jsonb)'::text, 'authenticated'::name, false),
    ('public.vps_sync_publish_request(text,text,text,jsonb)'::text, 'service_role'::name, true)
),
resolved as (
  select e.*, to_regprocedure(e.function_signature)::oid as function_oid
  from expected_function_privileges e
),
function_privilege_checks as (
select
  'function_execute' as check_group,
  function_signature || ':' || role_name::text as object_name,
  case
    when function_oid is null then 'FUNCTION_MISSING'
    when has_function_privilege(role_name, function_oid, 'EXECUTE') then 'EXECUTE'
    else 'NO_EXECUTE'
  end as observed,
  case when should_execute then 'EXECUTE' else 'NO_EXECUTE' end as expected,
  case
    when function_oid is null then false
    else has_function_privilege(role_name, function_oid, 'EXECUTE') = should_execute
  end as pass
from resolved
),
service_expected_tables(table_name) as (
  values
    ('vps_admins'::text),
    ('vps_control_settings'::text),
    ('vps_whitelist_revisions'::text),
    ('vps_whitelist_revision_items'::text),
    ('vps_runtime_snapshot'::text),
    ('vps_symbol_states'::text),
    ('vps_sim_positions'::text),
    ('vps_runtime_events'::text),
    ('vps_sync_acks'::text),
    ('vps_sync_devices'::text),
    ('vps_sync_nonces'::text),
    ('vps_sync_request_receipts'::text),
    ('vps_sync_ack_receipts'::text),
    ('vps_control_contract_audit'::text)
),
privileges(privilege_name) as (
  values ('SELECT'::text), ('INSERT'::text), ('UPDATE'::text), ('DELETE'::text)
),
service_role_table_checks as (
select
  'service_role_table_privilege' as check_group,
  e.table_name || ':' || p.privilege_name as object_name,
  case
    when to_regclass('public.' || e.table_name) is null then 'TABLE_MISSING'
    when has_table_privilege('service_role', 'public.' || e.table_name, p.privilege_name) then 'GRANTED'
    else 'NO_PRIVILEGE'
  end as observed,
  'NO_PRIVILEGE' as expected,
  to_regclass('public.' || e.table_name) is not null
    and not has_table_privilege('service_role', 'public.' || e.table_name, p.privilege_name) as pass
from service_expected_tables e
cross join privileges p
),
projection_acl_checks as (
select
  'v2_projection_table_acl' as check_group,
  table_name || ':authenticated_select' as object_name,
  case when has_table_privilege('authenticated', 'public.' || table_name, 'SELECT') then 'GRANTED' else 'NO_PRIVILEGE' end as observed,
  case when table_name = 'vps_control_contract_audit' then 'GRANTED' else 'NO_PRIVILEGE' end as expected,
  has_table_privilege('authenticated', 'public.' || table_name, 'SELECT') = (table_name = 'vps_control_contract_audit') as pass
from (values
  ('vps_sync_request_receipts'::text),
  ('vps_sync_ack_receipts'::text),
  ('vps_control_contract_audit'::text)
) as v(table_name)
)

select check_group, object_name, observed, expected, pass from checks
union all
select check_group, object_name, observed, expected, pass from function_privilege_checks
union all
select check_group, object_name, observed, expected, pass from service_role_table_checks
union all
select check_group, object_name, observed, expected, pass from projection_acl_checks
order by check_group, object_name;
