-- Read-only verification for Stage 1.
-- Execute as the Supabase SQL Editor's postgres role.

select
  c.relname as table_name,
  c.relrowsecurity as rls_enabled,
  c.relforcerowsecurity as force_rls
from pg_class as c
join pg_namespace as n
  on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relkind = 'r'
  and c.relname in (
    'vps_admins',
    'vps_control_settings',
    'vps_whitelist_revisions',
    'vps_whitelist_revision_items',
    'vps_runtime_snapshot',
    'vps_symbol_states',
    'vps_sim_positions',
    'vps_runtime_events',
    'vps_sync_acks'
  )
order by c.relname;

select
  schema_version,
  max_active_symbols,
  strategy_timezone,
  default_mode
from public.vps_control_settings
where id = true;

select
  schema_version,
  adapter_id,
  mode,
  health_status,
  state_summary
from public.vps_runtime_snapshot
where id = true;

-- Identity-free administrator-bootstrap verification.
select
  case when exists (select 1 from public.vps_admins where active = true)
    then 'active_admin_present'
    else 'no_active_admin'
  end as admin_bootstrap_status,
  (select count(*)::integer from public.vps_admins where active = true) as active_admin_count,
  (select count(*)::integer from public.vps_runtime_events where event_code = 'auth.github_admin_bootstrapped') as bootstrap_audit_event_count;
