-- Hosted protocol-v2 preflight (READ ONLY).
-- Run this in Supabase Dashboard SQL Editor before applying
-- 20260825003000_vps_sync_gateway_protocol_v2.sql.
-- This query does not write rows, settings, functions, grants, or migration state.

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
    ('vps_sync_nonces'::text)
),
expected_v2_tables(table_name) as (
  values
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
expected_functions(function_identity) as (
  values
    ('public.vps_is_admin()'::text),
    ('public.vps_text_is_safe(text)'::text),
    ('public.vps_sync_consume_nonce(text,text,timestamp with time zone)'::text),
    ('public.vps_sync_get_pending_revision(text)'::text),
    ('public.vps_sync_ingest_report(text,jsonb)'::text)
),
checks(check_name, object_name, observed, expected, pass) as (
  select
    'extension', 'pgcrypto',
    case when exists (select 1 from pg_extension where extname = 'pgcrypto') then 'PRESENT' else 'MISSING' end,
    'PRESENT',
    exists (select 1 from pg_extension where extname = 'pgcrypto')
  union all
  select
    'stage2_table', e.table_name,
    case when to_regclass('public.' || e.table_name) is null then 'MISSING' else 'PRESENT' end,
    'PRESENT',
    to_regclass('public.' || e.table_name) is not null
  from expected_tables e
  union all
  select
    'stage2_rls', e.table_name,
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
    'stage2_force_rls', e.table_name,
    case
      when c.oid is null then 'MISSING'
      when c.relforcerowsecurity then 'FORCE_RLS_ON'
      else 'FORCE_RLS_OFF'
    end,
    'FORCE_RLS_OFF_OR_UNSPECIFIED',
    c.oid is not null
  from expected_tables e
  left join pg_class c on c.oid = to_regclass('public.' || e.table_name)
  union all
  select
    'v2_table_before_apply', e.table_name,
    case when to_regclass('public.' || e.table_name) is null then 'MISSING' else 'PRESENT' end,
    'MISSING_OR_REVIEW_PARTIAL_STATE',
    true
  from expected_v2_tables e
  union all
  select
    'v2_column', e.table_name || '.' || e.column_name,
    case when exists (
      select 1
      from information_schema.columns c
      where c.table_schema = 'public'
        and c.table_name = e.table_name
        and c.column_name = e.column_name
    ) then 'PRESENT' else 'MISSING' end,
    'MISSING_BEFORE_FULL_V2_OR_REVIEW_PARTIAL_STATE',
    true
  from expected_v2_columns e
  union all
  select
    'stage2_function', e.function_identity,
    case when to_regprocedure(e.function_identity) is null then 'MISSING' else 'PRESENT' end,
    'PRESENT',
    to_regprocedure(e.function_identity) is not null
  from expected_functions e
  union all
  select
    'gateway_device_row', 'stock-sim-v31f-15m',
    case
      when not exists (select 1 from public.vps_sync_devices where device_id = 'stock-sim-v31f-15m') then 'MISSING'
      when exists (select 1 from public.vps_sync_devices where device_id = 'stock-sim-v31f-15m' and enabled) then 'PRESENT_ENABLED'
      else 'PRESENT_DISABLED'
    end,
    'PRESENT_ENABLED',
    exists (select 1 from public.vps_sync_devices where device_id = 'stock-sim-v31f-15m' and enabled)
  union all
  select
    'control_settings_singleton', 'vps_control_settings.id=true',
    case when exists (select 1 from public.vps_control_settings where id = true) then 'PRESENT' else 'MISSING' end,
    'PRESENT',
    exists (select 1 from public.vps_control_settings where id = true)
)
select check_name, object_name, observed, expected, pass
from checks
order by check_name, object_name;
