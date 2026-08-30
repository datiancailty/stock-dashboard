-- Aggregate-only preflight for the Part 6 research-match confidence v3 migration.
-- Run manually in Supabase SQL Editor BEFORE
-- 20260830130000_personal_strategy_confidence_contract_v3.sql.
-- It returns classification counts only. It never infers, converts, or updates
-- historical confidence values, and never returns private payloads, codes,
-- user IDs, prompts, hashes, or Worker-run content.

with strategy_documents as (
  select d.payload
    from public.personal_documents d
   where d.document_key = 'strategy_analysis'
), classified as (
  select case
    when d.payload->>'schemaVersion' = '3'
     and d.payload->>'confidenceScale' = 'research_match_percent_0_to_100'
     and d.payload->>'confidenceMeaning' = '研究匹配度，不是涨跌概率、收益概率或自动下单依据'
     and jsonb_typeof(d.payload->'briefCommand') = 'object'
     and jsonb_typeof(d.payload->'advice') = 'array'
     and not exists (
       select 1
         from (
           select d.payload #> '{briefCommand,confidence}' as confidence_value
           union all
           select advice_item.value->'confidence'
             from jsonb_array_elements(
               case when jsonb_typeof(d.payload->'advice') = 'array'
                    then d.payload->'advice'
                    else '[]'::jsonb
               end
             ) as advice_item(value)
         ) as confidence_entries
        where jsonb_typeof(confidence_entries.confidence_value) is distinct from 'number'
           or case
                when jsonb_typeof(confidence_entries.confidence_value) = 'number'
                then not (
                  (confidence_entries.confidence_value #>> '{}')::numeric between 0 and 100
                  and trunc((confidence_entries.confidence_value #>> '{}')::numeric)
                      = (confidence_entries.confidence_value #>> '{}')::numeric
                )
                else true
              end
     ) then 'already_v3'
    when d.payload->>'schemaVersion' = '2'
     and d.payload->>'confidenceScale' is null
     and jsonb_typeof(d.payload->'briefCommand') = 'object'
     and jsonb_typeof(d.payload->'advice') = 'array'
      then 'legacy_pending_refresh'
    else 'requires_fresh_worker'
  end as status
    from strategy_documents d
)
select count(*) as total_current_strategy_analysis,
       count(*) filter (where status = 'already_v3') as already_v3,
       count(*) filter (where status = 'legacy_pending_refresh') as legacy_pending_refresh,
       count(*) filter (where status = 'requires_fresh_worker') as requires_fresh_worker
  from classified;
