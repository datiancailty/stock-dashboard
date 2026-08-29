-- READ ONLY diagnostic for the first hosted v2 whitelist revision.
-- Run this SELECT manually in Supabase SQL Editor. It does not call a sync RPC,
-- does not advance revision status, and does not write any table.
with target as (
  select
    r.*,
    item_data.symbols,
    item_data.item_count,
    (select count(*)
       from public.vps_sync_request_receipts as receipt
      where receipt.device_id = 'stock-sim-v31f-15m'
        and receipt.operation = 'pull') as pull_receipt_count
  from public.vps_whitelist_revisions as r
  cross join lateral (
    select
      array_agg(item.symbol order by item.sort_order) as symbols,
      count(*)::integer as item_count
    from public.vps_whitelist_revision_items as item
    where item.revision_id = r.id
  ) as item_data
  where r.revision_no = 1
), calculated as (
  select
    target.*,
    case
      when target.item_count between 1 and 50
      then public.vps_members_sha256(target.symbols)
      else null
    end as calculated_members_sha256,
    case
      when target.item_count between 1 and 50
      then public.vps_revision_payload_text(
        target.revision_no,
        target.target_device_id,
        target.contract_adapter_id,
        target.contract_mode,
        public.vps_contract_time_text(target.contract_created_at),
        public.vps_contract_time_text(target.contract_expires_at),
        target.source_policy_sha256,
        target.members_sha256,
        target.required_snapshot_id,
        target.required_snapshot_sha256,
        target.symbols
      )
      else null
    end as calculated_payload_text
  from target
), hashed as (
  select
    calculated.*,
    case
      when calculated.calculated_payload_text is not null
      then encode(extensions.digest(calculated.calculated_payload_text, 'sha256'::text), 'hex')
      else null
    end as calculated_payload_sha256
  from calculated
), expected as (
  select
    hashed.*,
    case
      when hashed.calculated_payload_sha256 is not null
      then public.vps_revision_raw_contract_text(
        hashed.calculated_payload_text,
        hashed.calculated_payload_sha256
      )
      else null
    end as calculated_raw_contract
  from hashed
), final_check as (
  select
    expected.*,
    case
      when expected.calculated_raw_contract is not null
      then encode(extensions.digest(expected.calculated_raw_contract, 'sha256'::text), 'hex')
      else null
    end as calculated_raw_contract_sha256
  from expected
)
select
  revision_no,
  status::text as status,
  desired_symbol_count,
  item_count,
  target_device_id,
  contract_protocol_version,
  contract_adapter_id,
  contract_mode,
  required_snapshot_id,
  required_snapshot_sha256,
  pull_receipt_count,
  members_sha256 as stored_members_sha256,
  calculated_members_sha256,
  coalesce(calculated_members_sha256 is not null and members_sha256 = calculated_members_sha256, false)
    as members_hash_match,
  control_payload_sha256 as stored_payload_sha256,
  calculated_payload_sha256,
  coalesce(calculated_payload_sha256 is not null and control_payload_sha256 = calculated_payload_sha256, false)
    as payload_hash_match,
  length(control_raw_contract) as stored_raw_contract_length,
  right(control_raw_contract, 1) = E'\n' as stored_raw_contract_has_terminal_newline,
  control_raw_contract_sha256 as stored_raw_contract_sha256,
  calculated_raw_contract_sha256,
  coalesce(calculated_raw_contract_sha256 is not null and control_raw_contract_sha256 = calculated_raw_contract_sha256, false)
    as raw_contract_hash_match,
  coalesce(calculated_raw_contract is not null and control_raw_contract = calculated_raw_contract, false)
    as raw_contract_text_match
from final_check;
