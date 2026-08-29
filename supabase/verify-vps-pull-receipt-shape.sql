-- READ ONLY diagnostic for the exact response_json persisted by prior pull RPC calls.
-- Run this SELECT manually in Supabase SQL Editor.
-- It does not call a sync RPC, does not advance status, and does not write any table.
with target as (
  select
    r.id,
    r.revision_no,
    r.status,
    r.desired_symbol_count,
    r.target_device_id,
    r.contract_protocol_version,
    r.contract_adapter_id,
    r.contract_mode,
    r.source_policy_sha256,
    r.members_sha256,
    r.required_snapshot_id,
    r.required_snapshot_sha256,
    r.contract_created_at,
    r.contract_expires_at,
    r.control_payload_sha256,
    r.control_raw_contract,
    r.control_raw_contract_sha256,
    item_data.symbols,
    item_data.symbols_json,
    item_data.item_count
  from public.vps_whitelist_revisions as r
  cross join lateral (
    select
      array_agg(upper(btrim(item.symbol)) order by item.sort_order) as symbols,
      jsonb_agg(item.symbol order by item.sort_order) as symbols_json,
      count(*)::integer as item_count
    from public.vps_whitelist_revision_items as item
    where item.revision_id = r.id
  ) as item_data
  where r.revision_no = 1
), candidate as (
  select
    target.*,
    jsonb_build_object(
      'protocol_version', 2,
      'revision_no', target.revision_no,
      'status', case when target.status::text = 'submitted' then 'sync_pending' else target.status::text end,
      'target_device_id', target.target_device_id,
      'adapter_id', target.contract_adapter_id,
      'mode', target.contract_mode,
      'created_at', public.vps_contract_time_text(target.contract_created_at),
      'expires_at', public.vps_contract_time_text(target.contract_expires_at),
      'source_policy_sha256', target.source_policy_sha256,
      'members_sha256', target.members_sha256,
      'required_snapshot_id', target.required_snapshot_id,
      'required_snapshot_sha256', target.required_snapshot_sha256,
      'payload_sha256', target.control_payload_sha256,
      'raw_contract_sha256', target.control_raw_contract_sha256,
      'raw_contract', target.control_raw_contract,
      'symbols', target.symbols_json
    ) as candidate_revision
  from target
), expected as (
  select
    candidate.*,
    jsonb_build_object('revision', candidate_revision) as expected_response
  from candidate
), receipt_rows as (
  select
    receipt.created_at,
    receipt.operation,
    receipt.response_json
  from public.vps_sync_request_receipts as receipt
  where receipt.device_id = 'stock-sim-v31f-15m'
    and receipt.operation = 'pull'
)
select
  count(*) over () as pull_receipt_count,
  receipt.created_at,
  receipt.operation,
  jsonb_typeof(receipt.response_json) as response_json_type,
  case when jsonb_typeof(receipt.response_json) = 'object' then (
    select string_agg(object_key.key, ',' order by object_key.key)
    from jsonb_object_keys(receipt.response_json) as object_key(key)
  ) end as response_keys,
  jsonb_typeof(receipt.response_json -> 'revision') as actual_revision_json_type,
  case when jsonb_typeof(receipt.response_json -> 'revision') = 'object' then (
    select string_agg(object_key.key, ',' order by object_key.key)
    from jsonb_object_keys(receipt.response_json -> 'revision') as object_key(key)
  ) end as actual_revision_keys,
  jsonb_typeof(receipt.response_json -> 'revision' -> 'protocol_version') as actual_protocol_json_type,
  jsonb_typeof(receipt.response_json -> 'revision' -> 'revision_no') as actual_revision_no_json_type,
  jsonb_typeof(receipt.response_json -> 'revision' -> 'symbols') as actual_symbols_json_type,
  case when jsonb_typeof(receipt.response_json -> 'revision' -> 'symbols') = 'array'
    then jsonb_array_length(receipt.response_json -> 'revision' -> 'symbols') end as actual_symbol_count,
  receipt.response_json -> 'revision' ->> 'revision_no' as actual_revision_no,
  receipt.response_json -> 'revision' ->> 'status' as actual_status,
  receipt.response_json -> 'revision' ->> 'target_device_id' as actual_target_device_id,
  receipt.response_json -> 'revision' ->> 'source_policy_sha256' as actual_source_policy_sha256,
  length(receipt.response_json -> 'revision' ->> 'raw_contract') as actual_raw_contract_length,
  right(receipt.response_json -> 'revision' ->> 'raw_contract', 1) = E'\n' as actual_raw_contract_terminal_newline,
  receipt.response_json = expected.expected_response as response_matches_reconstructed_current_revision,
  (receipt.response_json -> 'revision') = expected.candidate_revision as revision_matches_reconstructed_current_revision,
  jsonb_typeof(expected.expected_response) as expected_response_json_type,
  jsonb_typeof(expected.candidate_revision) as expected_revision_json_type,
  expected.candidate_revision ->> 'status' as expected_status,
  expected.candidate_revision ->> 'revision_no' as expected_revision_no
from receipt_rows as receipt
cross join expected
order by receipt.created_at;
