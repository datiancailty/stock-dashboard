-- READ-ONLY aggregate postflight; execute manually AFTER the staged migration.
-- Reports no credentials, recommendation bodies, owner IDs, profile or trades.
select p.oid::regprocedure::text as rpc,
       p.prosecdef as security_definer,
       p.proconfig as function_settings,
       has_function_privilege('authenticated', p.oid, 'EXECUTE') as authenticated_execute,
       has_function_privilege('anon', p.oid, 'EXECUTE') as anon_execute,
       has_function_privilege('service_role', p.oid, 'EXECUTE') as service_role_execute
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname='public' and p.proname in
  ('personal_get_recommendation_eval_context','personal_sync_recommendation_evaluations')
order by p.proname;

select to_regclass('public.personal_part4_sync_writer_credentials') is not null as writer_credential_table_exists,
       to_regprocedure('extensions.digest(text,text)') is not null as digest_available,
       to_regprocedure('public.personal_current_user_is_active()') is not null as active_user_gate_available,
       to_regprocedure('public.personal_get_part6()') is not null as part6_getter_preserved;

select count(*) as recommendation_count,
       count(*) filter(where payload->>'action'='分批买入') as explicit_buy_count,
       count(*) filter(where payload#>>'{evaluation,status}' in ('success','failed')) as resolved_count,
       count(*) filter(where payload#>>'{evaluation,status}' in ('pending','no_data')
          or payload#>>'{evaluation,status}' is null) as pending_or_unknown_count,
       max(payload#>>'{evaluation,observationAsOf}') as latest_observation_date
from public.personal_strategy_recommendations;

select count(*) as recommendation_meta_documents,
       count(*) filter(where payload ? 'performance') as documents_with_separate_non_ai_performance,
       max(payload->>'asOf') as latest_outcome_refresh_as_of,
       max(payload#>>'{performance,dataAsOf}') as latest_data_date
from public.personal_documents where document_key='recommendations_meta';
-- The previous strategy_analysis.updatedAt is intentionally NOT refreshed.
-- RPC existence/grants do NOT prove Hosted writes or real-provider coverage.
-- Then run the local worker with --dry-run (no --publish) only after approval.
