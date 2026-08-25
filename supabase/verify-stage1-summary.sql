-- Compact read-only Stage-1 verification summary.
select
  (
    select count(*)
    from pg_class as c
    join pg_namespace as n on n.oid = c.relnamespace
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
  ) as vps_table_count,
  (
    select count(*)
    from pg_class as c
    join pg_namespace as n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind = 'r'
      and c.relrowsecurity = true
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
  ) as rls_enabled_count,
  settings.max_active_symbols,
  settings.strategy_timezone,
  settings.default_mode,
  runtime.adapter_id,
  runtime.mode,
  runtime.health_status,
  (
    select count(*)::integer
    from public.vps_admins
    where active = true
  ) as active_admin_count,
  (
    select count(*)::integer
    from public.vps_runtime_events
    where event_code = 'auth.github_admin_bootstrapped'
  ) as bootstrap_audit_event_count
from public.vps_control_settings as settings
cross join public.vps_runtime_snapshot as runtime
where settings.id = true
  and runtime.id = true;
