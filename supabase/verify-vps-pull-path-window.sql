-- READ ONLY diagnostic for the signed pull path after revision creation.
-- Run this SELECT manually in Supabase SQL Editor.
-- It does not call a sync RPC and does not write any table.
with target_revision as (
  select
    revision_no,
    status,
    contract_created_at,
    contract_expires_at
  from public.vps_whitelist_revisions
  where revision_no = 1
), nonce_window as (
  select
    nonce.created_at,
    nonce.seen_at,
    nonce.expires_at
  from public.vps_sync_nonces as nonce
  cross join target_revision
  where nonce.device_id = 'stock-sim-v31f-15m'
    and nonce.created_at >= target_revision.contract_created_at
), receipt_window as (
  select
    receipt.created_at,
    receipt.operation,
    jsonb_typeof(receipt.response_json) as response_json_type,
    jsonb_typeof(receipt.response_json -> 'revision') as response_revision_type,
    receipt.response_json -> 'revision' ->> 'revision_no' as response_revision_no,
    receipt.response_json -> 'revision' ->> 'status' as response_status
  from public.vps_sync_request_receipts as receipt
  cross join target_revision
  where receipt.device_id = 'stock-sim-v31f-15m'
    and receipt.operation = 'pull'
    and receipt.created_at >= target_revision.contract_created_at
)
select
  target_revision.revision_no,
  target_revision.status,
  target_revision.contract_created_at,
  target_revision.contract_expires_at,
  (select count(*)::integer from nonce_window) as nonce_count_at_or_after_revision,
  (select min(nonce_window.created_at) from nonce_window) as first_nonce_created_at,
  (select max(nonce_window.created_at) from nonce_window) as last_nonce_created_at,
  (select count(*)::integer from receipt_window) as pull_receipt_count_at_or_after_revision,
  (select min(receipt_window.created_at) from receipt_window) as first_pull_receipt_created_at,
  (select max(receipt_window.created_at) from receipt_window) as last_pull_receipt_created_at,
  coalesce((select count(*) from nonce_window), 0) > 0 as hmac_nonce_stage_reached,
  coalesce((select count(*) from receipt_window), 0) > 0 as pull_rpc_receipt_written,
  (select jsonb_agg(jsonb_build_object(
      'created_at', receipt_window.created_at,
      'response_json_type', receipt_window.response_json_type,
      'response_revision_type', receipt_window.response_revision_type,
      'response_revision_no', receipt_window.response_revision_no,
      'response_status', receipt_window.response_status
    ) order by receipt_window.created_at) from receipt_window) as post_revision_pull_receipts
from target_revision;
