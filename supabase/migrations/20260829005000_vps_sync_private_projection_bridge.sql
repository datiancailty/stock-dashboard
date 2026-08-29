-- Stage 3 private projection bridge for the already-applied protocol-v2 gateway.
--
-- STATUS: REVIEWED LOCAL CANDIDATE / MANUAL HOSTED SQL EXECUTION ONLY.
-- Apply only by the user in the Supabase SQL Editor after the new Edge/VPS
-- contract has been reviewed.  Never run this from Pages, GitHub Actions, VPS,
-- or an unattended agent.
--
-- The existing v2 ingest function is preserved under a private base name.  The
-- new wrapper invokes it first and then invokes the Stage-3 private projection
-- writer in the same PostgreSQL transaction.  If the private projection fails,
-- the complete v2 publish transaction (including ACK/runtime mutations) rolls
-- back.  Reports from an older VPS release may omit private_projection and keep
-- the historical v2 path; the new VPS release will include the field.

begin;

do $$
begin
  if to_regprocedure('public.vps_sync_ingest_report_v2_base(text,jsonb)') is null then
    if to_regprocedure('public.vps_sync_ingest_report(text,jsonb)') is null then
      raise exception 'Existing protocol-v2 ingest function is missing';
    end if;
    execute 'alter function public.vps_sync_ingest_report(text,jsonb) rename to vps_sync_ingest_report_v2_base';
  end if;
end;
$$;

create or replace function public.vps_sync_ingest_report(
  p_device_id text,
  p_report jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  result jsonb;
begin
  -- The base function performs HMAC-gateway-side v2 ACK/revision validation
  -- and transitions the control revision before the private writer checks the
  -- same active evidence.  Both calls remain in this one database transaction.
  result := public.vps_sync_ingest_report_v2_base(p_device_id, p_report);

  if p_report ? 'private_projection' then
    if jsonb_typeof(p_report -> 'private_projection') <> 'object' then
      raise exception 'Private projection bridge payload is invalid';
    end if;
    perform public.vps_sync_replace_private_projection(
      p_device_id,
      'primary',
      p_report -> 'private_projection'
    );
  end if;

  return result;
end;
$$;

-- The ingest function remains an internal gateway primitive.  The Edge
-- publish RPC is the only service-role entry point; direct role execution is
-- intentionally not granted here.
revoke all on function public.vps_sync_ingest_report(text, jsonb)
  from public, anon, authenticated, service_role;
revoke all on function public.vps_sync_ingest_report_v2_base(text, jsonb)
  from public, anon, authenticated, service_role;

commit;
