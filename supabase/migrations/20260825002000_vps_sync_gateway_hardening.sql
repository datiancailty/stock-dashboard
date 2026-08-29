-- Stock dashboard / VPS private control plane — Stage 2 hardening follow-up.
--
-- This migration intentionally follows 20260825001000, which has already been
-- applied through SQL Editor. It narrows the service-role blast radius and adds
-- local ACK-id idempotency without changing browser RLS or exposing any secret.

begin;

-- Edge Functions call only these three hardened SECURITY DEFINER RPCs. They do
-- not retain direct DML on the private vps_* tables even though service_role
-- bypasses RLS if table privileges exist.
revoke all on table
  public.vps_admins,
  public.vps_control_settings,
  public.vps_whitelist_revisions,
  public.vps_whitelist_revision_items,
  public.vps_runtime_snapshot,
  public.vps_symbol_states,
  public.vps_sim_positions,
  public.vps_runtime_events,
  public.vps_sync_acks,
  public.vps_sync_devices,
  public.vps_sync_nonces
  from service_role;

-- Preserve the already-applied bounded ingestion function behind an owner-only
-- implementation name. The wrapper below adds a VPS-local stable ACK id, makes
-- retry acknowledgement deterministic, and rejects stale activation evidence.
alter function public.vps_sync_ingest_report(text, jsonb)
  rename to vps_sync_ingest_report_legacy;

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
  item jsonb;
  legacy_report jsonb;
  legacy_acks jsonb := '[]'::jsonb;
  accepted_ack_ids jsonb := '[]'::jsonb;
  ack_id text;
  ack_stage text;
  ack_revision_no bigint;
  ack_generation bigint;
  ack_pack_sha text;
  prior_active_generation bigint;
  linked_revision_no bigint;
  linked_generation bigint;
  linked_pack_sha text;
  linked_status public.vps_whitelist_status;
  seen_ack_ids text[] := array[]::text[];
begin
  if not exists (
    select 1 from public.vps_sync_devices
    where device_id = p_device_id and enabled = true
  ) then
    raise exception 'VPS gateway device is disabled';
  end if;
  if jsonb_typeof(p_report) <> 'object'
    or jsonb_typeof(coalesce(p_report -> 'acks', '[]'::jsonb)) <> 'array' then
    raise exception 'VPS runtime report acknowledgement envelope is invalid';
  end if;

  for item in select value from jsonb_array_elements(coalesce(p_report -> 'acks', '[]'::jsonb)) loop
    if jsonb_typeof(item) <> 'object' then
      raise exception 'VPS acknowledgement must be an object';
    end if;
    ack_id := item ->> 'ack_id';
    ack_stage := item ->> 'sync_stage';
    begin
      ack_revision_no := (item ->> 'revision_no')::bigint;
      ack_generation := nullif(item ->> 'generation', '')::bigint;
      ack_pack_sha := nullif(item ->> 'pack_sha256', '');
    exception when others then
      raise exception 'VPS acknowledgement identity is invalid';
    end;
    if ack_id !~ '^[0-9a-f]{64}$'
      or ack_id = any(seen_ack_ids)
      or ack_revision_no < 1
      or ack_stage not in ('received', 'preparing', 'activated', 'rejected') then
      raise exception 'VPS acknowledgement identity is invalid';
    end if;
    seen_ack_ids := array_append(seen_ack_ids, ack_id);

    -- An old/superseded revision or a lower/equal new generation cannot change
    -- the current active state. Same-revision retry remains idempotent.
    if ack_stage = 'activated' then
      if ack_generation is null or ack_generation < 1
        or ack_pack_sha is null or ack_pack_sha !~ '^[0-9a-f]{64}$' then
        raise exception 'Activated VPS acknowledgement lacks generation/hash';
      end if;
      select status into linked_status
        from public.vps_whitelist_revisions
       where revision_no = ack_revision_no
       for update;
      if not found or linked_status in ('rejected', 'superseded') then
        raise exception 'Activated VPS acknowledgement references a stale revision';
      end if;
      select max(active_generation) into prior_active_generation
        from public.vps_whitelist_revisions
       where status = 'active'
         and revision_no <> ack_revision_no;
      if prior_active_generation is not null and ack_generation <= prior_active_generation then
        raise exception 'Activated VPS acknowledgement generation regresses';
      end if;
      if nullif(p_report ->> 'active_revision_no', '')::bigint is distinct from ack_revision_no
        or nullif(p_report ->> 'active_generation', '')::bigint is distinct from ack_generation
        or nullif(p_report ->> 'active_pack_sha256', '') is distinct from ack_pack_sha then
        raise exception 'Activated VPS acknowledgement does not match runtime linkage';
      end if;
    end if;

    legacy_acks := legacy_acks || jsonb_build_array(item - 'ack_id');
    accepted_ack_ids := accepted_ack_ids || jsonb_build_array(ack_id);
  end loop;

  legacy_report := jsonb_set(p_report, '{acks}', legacy_acks, true);
  perform public.vps_sync_ingest_report_legacy(p_device_id, legacy_report);

  -- A runtime report that claims an active revision must match the database's
  -- confirmed active revision/generation/hash exactly. A pending browser change
  -- never becomes active just because a stale VPS report arrives.
  if nullif(p_report ->> 'active_revision_no', '') is not null then
    begin
      linked_revision_no := (p_report ->> 'active_revision_no')::bigint;
      linked_generation := (p_report ->> 'active_generation')::bigint;
      linked_pack_sha := p_report ->> 'active_pack_sha256';
    exception when others then
      raise exception 'VPS runtime active linkage is invalid';
    end;
    select revision_no, active_generation, active_pack_sha256, status
      into linked_revision_no, linked_generation, linked_pack_sha, linked_status
      from public.vps_whitelist_revisions
     where revision_no = (p_report ->> 'active_revision_no')::bigint;
    if not found
      or linked_status <> 'active'
      or linked_generation is distinct from (p_report ->> 'active_generation')::bigint
      or linked_pack_sha is distinct from (p_report ->> 'active_pack_sha256') then
      raise exception 'VPS runtime does not match confirmed active revision';
    end if;
  end if;

  return jsonb_build_object('accepted_ack_ids', accepted_ack_ids);
end;
$$;

revoke all on function public.vps_sync_ingest_report_legacy(text, jsonb) from public;
revoke all on function public.vps_sync_ingest_report(text, jsonb) from public;
revoke all on function public.vps_sync_consume_nonce(text, text, timestamptz) from public;
revoke all on function public.vps_sync_get_pending_revision(text) from public;
grant execute on function public.vps_sync_consume_nonce(text, text, timestamptz) to service_role;
grant execute on function public.vps_sync_get_pending_revision(text) to service_role;
grant execute on function public.vps_sync_ingest_report(text, jsonb) to service_role;

commit;
