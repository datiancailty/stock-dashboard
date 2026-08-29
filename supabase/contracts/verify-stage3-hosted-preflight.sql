-- Stage 3 Hosted preflight — READ ONLY.
--
-- Run only when the design has been reviewed, before manually applying the
-- separate Stage 3 SQL draft. This query does not create objects, read private
-- rows, or expose credentials/account/provider data.

with checks as (
  select 'stage1_control_settings_exists'::text as check_name,
         to_regclass('public.vps_control_settings') is not null as passed
  union all
  select 'stage1_whitelist_revision_tables_exist',
         to_regclass('public.vps_whitelist_revisions') is not null
         and to_regclass('public.vps_whitelist_revision_items') is not null
  union all
  select 'stage2_device_table_exists',
         to_regclass('public.vps_sync_devices') is not null
  union all
  select 'stage2_fixed_device_row_exists',
         exists (
           select 1
             from public.vps_sync_devices
            where device_id = 'stock-sim-v31f-15m'
              and enabled = true
         )
  union all
  select 'stage2_protocol_v2_receipt_tables_exist',
         to_regclass('public.vps_sync_request_receipts') is not null
         and to_regclass('public.vps_sync_ack_receipts') is not null
  union all
  select 'protocol_v2_publish_rpc_exists',
         to_regprocedure('public.vps_sync_publish_request(text,text,text,jsonb)') is not null
  union all
  select 'v2_revision_payload_helpers_exist',
         to_regprocedure('public.vps_contract_time_text(timestamp with time zone)') is not null
         and to_regprocedure('public.vps_members_sha256(text[])') is not null
         and to_regprocedure('public.vps_revision_payload_text(bigint,text,text,text,text,text,text,text,text,text,text[])') is not null
  union all
  select 'control_settings_v2_columns_exist',
         (select count(*) = 6
            from information_schema.columns
           where table_schema = 'public'
             and table_name = 'vps_control_settings'
             and column_name in (
               'gateway_protocol_version', 'target_device_id',
               'expected_adapter_id', 'expected_source_policy_sha256',
               'required_snapshot_id', 'required_snapshot_sha256'
             ))
  union all
  select 'legacy_whitelist_submit_signature_is_present',
         to_regprocedure('public.vps_submit_whitelist_revision(text[],text)') is not null
  union all
  select 'stage3_new_signature_not_already_conflicting',
         to_regprocedure('public.vps_submit_whitelist_revision(text[],text,bigint)') is null
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
