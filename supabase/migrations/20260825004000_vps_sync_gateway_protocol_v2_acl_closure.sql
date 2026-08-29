-- Hosted protocol-v2 function EXECUTE ACL closure (CORRECTIVE PATCH).
--
-- Apply only after the full 20260825003000_vps_sync_gateway_protocol_v2.sql
-- has succeeded and the hosted postflight has identified the ACL mismatch.
-- This transaction changes function EXECUTE privileges only. It does not
-- insert/update/delete data, call any RPC, alter tables, or create secrets.
-- Do not rerun the protocol-v2 migration because of the postflight mismatch.

begin;

-- Start from an explicit closed matrix for the nine audited functions. This
-- also removes pre-existing explicit grants that are not removed by revoking
-- PUBLIC alone.
revoke all on function public.vps_is_admin() from PUBLIC, anon, authenticated, service_role;
revoke all on function public.vps_submit_whitelist_revision(text[], text) from PUBLIC, anon, authenticated, service_role;
revoke all on function public.vps_set_control_snapshot_requirement(text, text, integer) from PUBLIC, anon, authenticated, service_role;
revoke all on function public.vps_sync_consume_nonce(text, text, timestamptz) from PUBLIC, anon, authenticated, service_role;
revoke all on function public.vps_sync_get_pending_revision(text) from PUBLIC, anon, authenticated, service_role;
revoke all on function public.vps_sync_ingest_report_v1(text, jsonb) from PUBLIC, anon, authenticated, service_role;
revoke all on function public.vps_sync_ingest_report(text, jsonb) from PUBLIC, anon, authenticated, service_role;
revoke all on function public.vps_sync_pull_request(text, text, text) from PUBLIC, anon, authenticated, service_role;
revoke all on function public.vps_sync_publish_request(text, text, text, jsonb) from PUBLIC, anon, authenticated, service_role;

-- Browser/admin functions: authenticated only.
grant execute on function public.vps_is_admin() to authenticated;
grant execute on function public.vps_submit_whitelist_revision(text[], text) to authenticated;
grant execute on function public.vps_set_control_snapshot_requirement(text, text, integer) to authenticated;

-- Machine gateway functions: service_role only.
grant execute on function public.vps_sync_consume_nonce(text, text, timestamptz) to service_role;
grant execute on function public.vps_sync_pull_request(text, text, text) to service_role;
grant execute on function public.vps_sync_publish_request(text, text, text, jsonb) to service_role;

commit;
