-- READ ONLY diagnostic for the exact v2 revision envelope shape consumed by Edge.
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
    public.vps_contract_time_text(target.contract_created_at) as created_at_wire,
    public.vps_contract_time_text(target.contract_expires_at) as expires_at_wire,
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
)
select
  revision_no,
  status::text as stored_status,
  case when status::text = 'submitted' then 'sync_pending' else status::text end as expected_pull_status,
  desired_symbol_count,
  item_count,
  contract_protocol_version,
  target_device_id,
  contract_adapter_id,
  contract_mode,
  source_policy_sha256,
  coalesce(source_policy_sha256 = '7330a793c79b3c2f2bf0c52b2d085b98cdc9b851fbc88f7c3d399030d8d54c75', false)
    as source_policy_match,
  required_snapshot_id,
  required_snapshot_sha256,
  created_at_wire,
  expires_at_wire,
  created_at_wire ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
    as created_at_wire_shape_ok,
  expires_at_wire ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
    as expires_at_wire_shape_ok,
  coalesce(contract_expires_at > contract_created_at, false) as expiry_after_creation,
  jsonb_typeof(candidate_revision -> 'protocol_version') as protocol_json_type,
  jsonb_typeof(candidate_revision -> 'revision_no') as revision_no_json_type,
  jsonb_typeof(candidate_revision -> 'symbols') as symbols_json_type,
  candidate_revision ->> 'status' as candidate_status,
  candidate_revision ->> 'source_policy_sha256' as candidate_source_policy_sha256,
  length(candidate_revision ->> 'raw_contract') as candidate_raw_contract_length,
  right(candidate_revision ->> 'raw_contract', 1) = E'\n' as candidate_raw_contract_terminal_newline,
  (
    select string_agg(key, ',' order by key)
    from jsonb_object_keys(candidate_revision) as object_key(key)
  ) as candidate_keys,
  candidate_revision::text as candidate_revision_json
from candidate;
