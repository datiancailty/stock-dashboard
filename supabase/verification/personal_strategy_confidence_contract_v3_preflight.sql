-- Aggregate-only preflight for the Part 6 research-match confidence v3 migration.
-- Run manually in Supabase SQL Editor BEFORE
-- 20260830130000_personal_strategy_confidence_contract_v3.sql.
-- It returns counts only; no private payload, code, user ID, prompt, or hash.

with strategy_documents as (
  select d.payload,
         jsonb_build_array(d.payload #> '{briefCommand,confidence}')
           || case when jsonb_typeof(d.payload->'advice') = 'array'
                   then d.payload->'advice'
                   else '[]'::jsonb
              end as confidence_values
    from public.personal_documents d
   where d.document_key = 'strategy_analysis'
), classified as (
  select case
    when payload->>'schemaVersion' = '3'
     and payload->>'confidenceScale' = 'research_match_percent_0_to_100'
     and payload->>'confidenceMeaning' = '研究匹配度，不是涨跌概率、收益概率或自动下单依据'
     and jsonb_typeof(payload->'briefCommand') = 'object'
     and jsonb_typeof(payload->'advice') = 'array'
      then 'already_v3'
    when payload->>'schemaVersion' = '2'
     and payload->>'confidenceScale' is null
     and jsonb_typeof(payload->'briefCommand') = 'object'
     and jsonb_typeof(payload->'advice') = 'array'
     and not exists (
       select 1
         from jsonb_array_elements(strategy_documents.confidence_values) as confidence_item(value)
        where not coalesce(
          case when jsonb_typeof(confidence_item.value) = 'number'
                 then (confidence_item.value #>> '{}')::numeric between 0 and 1
               else false
          end,
          false
        )
     )
     and exists (
       select 1
         from jsonb_array_elements(strategy_documents.confidence_values) as confidence_item(value)
        where coalesce(
          case when jsonb_typeof(confidence_item.value) = 'number'
                 then (confidence_item.value #>> '{}')::numeric > 0
                  and (confidence_item.value #>> '{}')::numeric < 1
               else false
          end,
          false
        )
     )
     and not exists (
       select 1
         from jsonb_array_elements(payload->'advice') as advice_item(value)
        where jsonb_typeof(advice_item.value) <> 'object'
     )
      then 'safe_legacy_probability_batch'
    else 'requires_fresh_worker'
  end as status
    from strategy_documents
)
select count(*) as total_current_strategy_analysis,
       count(*) filter (where status = 'safe_legacy_probability_batch') as safe_legacy_probability_batches,
       count(*) filter (where status = 'already_v3') as already_v3_batches,
       count(*) filter (where status = 'requires_fresh_worker') as requires_fresh_worker_batches
  from classified;
