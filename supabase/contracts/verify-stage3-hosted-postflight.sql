-- Stage 3 Hosted postflight — READ ONLY.
--
-- Run after the user has manually applied the reviewed Stage 3 SQL draft.
-- This returns one aggregate row and does not return private position values,
-- Auth email, user ids, provider responses, account ids or secrets.

with checks as (
  select 'app_usernames_exists'::text as check_name,
         to_regclass('public.app_usernames') is not null as passed
  union all
  select 'private_scope_tables_exist',
         to_regclass('public.vps_private_scopes') is not null
         and to_regclass('public.vps_private_scope_members') is not null
  union all
  select 'private_projection_tables_exist',
         to_regclass('public.vps_private_projection_state') is not null
         and to_regclass('public.vps_private_sim_positions') is not null
  union all
  select 'all_new_tables_have_rls',
         not exists (
           select 1
             from pg_class as c
             join pg_namespace as n on n.oid = c.relnamespace
            where n.nspname = 'public'
              and c.relname in (
                'app_usernames', 'vps_private_scopes',
                'vps_private_scope_members',
                'vps_private_projection_state',
                'vps_private_sim_positions'
              )
              and c.relrowsecurity is not true
         )
  union all
  select 'private_scope_bootstrap_exists',
         exists (
           select 1 from public.vps_private_scopes
            where scope_key = 'primary'
              and active = true
              and target_device_id = 'stock-sim-v31f-15m'
         )
  union all
  select 'private_projection_initial_state_is_dry_run',
         exists (
           select 1 from public.vps_private_projection_state
            where scope_key = 'primary'
              and mode = 'DRY_RUN'
              and health_status = 'unknown'
              and projection_sequence = 0
              and position_count = 0
         )
  union all
  select 'private_derived_columns_are_database_generated',
         (select count(*) = 4
            from information_schema.columns
           where table_schema = 'public'
             and table_name = 'vps_private_sim_positions'
             and column_name in ('cost_basis', 'market_value', 'unrealized_pnl', 'unrealized_pnl_pct')
             and is_generated = 'ALWAYS')
  union all
  select 'private_input_columns_present',
         (select count(*) = 10
            from information_schema.columns
           where table_schema = 'public'
             and table_name = 'vps_private_sim_positions'
             and column_name in (
               'scope_key', 'symbol', 'held_quantity', 'available_quantity',
               'average_cost_per_share', 'current_unadjusted_price',
               'price_as_of', 'data_status', 'quote_source_kind',
               'projection_sequence'
             ))
  union all
  select 'forbidden_private_columns_absent',
         not exists (
           select 1
             from information_schema.columns
            where table_schema = 'public'
              and table_name in (
                'app_usernames', 'vps_private_scopes',
                'vps_private_scope_members',
                'vps_private_projection_state',
                'vps_private_sim_positions'
              )
              and lower(column_name) in (
                'account_id', 'order_id', 'fill_id', 'raw_provider_payload',
                'service_role_key', 'vps_hmac_secret', 'provider_api_key',
                'trading_credentials', 'password', 'refresh_token'
              )
         )
  union all
  select 'anon_cannot_execute_private_read',
         has_function_privilege('anon', 'public.vps_private_get_portfolio()', 'EXECUTE') = false
  union all
  select 'service_role_cannot_execute_private_read',
         has_function_privilege('service_role', 'public.vps_private_get_portfolio()', 'EXECUTE') = false
  union all
  select 'authenticated_can_execute_private_read',
         has_function_privilege('authenticated', 'public.vps_private_get_portfolio()', 'EXECUTE') = true
  union all
  select 'authenticated_can_execute_private_runtime_display',
         has_function_privilege('authenticated', 'public.vps_private_get_runtime_display()', 'EXECUTE') = true
         and has_function_privilege('anon', 'public.vps_private_get_runtime_display()', 'EXECUTE') = false
         and has_function_privilege('service_role', 'public.vps_private_get_runtime_display()', 'EXECUTE') = false
  union all
  select 'authenticated_can_execute_scope_policy_helper',
         has_function_privilege('authenticated', 'public.vps_private_can_view_scope(text)', 'EXECUTE') = true
         and has_function_privilege('anon', 'public.vps_private_can_view_scope(text)', 'EXECUTE') = false
  union all
  select 'only_service_role_can_execute_username_resolver',
         has_function_privilege('service_role', 'public.app_resolve_username(text)', 'EXECUTE') = true
         and has_function_privilege('anon', 'public.app_resolve_username(text)', 'EXECUTE') = false
         and has_function_privilege('authenticated', 'public.app_resolve_username(text)', 'EXECUTE') = false
  union all
  select 'current_username_rpc_is_authenticated_only',
         has_function_privilege('authenticated', 'public.app_get_current_username()', 'EXECUTE') = true
         and has_function_privilege('anon', 'public.app_get_current_username()', 'EXECUTE') = false
         and has_function_privilege('service_role', 'public.app_get_current_username()', 'EXECUTE') = false
  union all
  select 'anon_cannot_execute_private_writer',
         has_function_privilege('anon', 'public.vps_sync_replace_private_projection(text,text,jsonb)', 'EXECUTE') = false
  union all
  select 'authenticated_cannot_execute_private_writer',
         has_function_privilege('authenticated', 'public.vps_sync_replace_private_projection(text,text,jsonb)', 'EXECUTE') = false
  union all
  select 'service_role_can_execute_private_writer',
         has_function_privilege('service_role', 'public.vps_sync_replace_private_projection(text,text,jsonb)', 'EXECUTE') = true
  union all
  select 'browser_has_no_direct_private_table_select',
         has_table_privilege('anon', 'public.vps_private_sim_positions', 'SELECT') = false
         and has_table_privilege('authenticated', 'public.vps_private_sim_positions', 'SELECT') = false
  union all
  select 'service_role_has_no_direct_private_table_dml',
         has_table_privilege('service_role', 'public.vps_private_sim_positions', 'INSERT') = false
         and has_table_privilege('service_role', 'public.vps_private_sim_positions', 'UPDATE') = false
         and has_table_privilege('service_role', 'public.vps_private_sim_positions', 'DELETE') = false
  union all
  select 'legacy_whitelist_submit_is_not_executable',
         has_function_privilege('authenticated', 'public.vps_submit_whitelist_revision(text[],text)', 'EXECUTE') = false
         and has_function_privilege('anon', 'public.vps_submit_whitelist_revision(text[],text)', 'EXECUTE') = false
  union all
  select 'concurrency_checked_whitelist_submit_is_executable',
         has_function_privilege('authenticated', 'public.vps_submit_whitelist_revision(text[],text,bigint)', 'EXECUTE') = true
         and has_function_privilege('anon', 'public.vps_submit_whitelist_revision(text[],text,bigint)', 'EXECUTE') = false
  union all
  select 'whitelist_state_rpc_is_admin_gated',
         has_function_privilege('authenticated', 'public.vps_get_whitelist_control_state()', 'EXECUTE') = true
         and has_function_privilege('anon', 'public.vps_get_whitelist_control_state()', 'EXECUTE') = false
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
