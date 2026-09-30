-- 第二步：仅由用户手动执行的前向迁移。先确认只读预检 failed_count=0。
-- 独立Part0只读展示；不改旧投影、白名单、业务持仓、委托、ARM或交易授权。
-- 沿用现有服务端 writer capability；浏览器没有写入能力或表访问权限。
begin;
do $$ begin
 if to_regprocedure('public.personal_current_user_is_active()') is null
 or to_regclass('public.personal_part4_sync_writer_credentials') is null
 or to_regprocedure('extensions.digest(text,text)') is null then
  raise exception 'part0_prerequisites_missing';
 end if;
 if to_regclass('public.personal_part0_monitor') is not null then
  raise exception 'part0_already_exists_verify_instead_of_reapply';
 end if;
end $$;

create table public.personal_part0_monitor (
 owner_user_id uuid primary key references auth.users(id) on delete cascade,
 observed_at timestamptz not null check(isfinite(observed_at)),
 payload jsonb not null check(jsonb_typeof(payload)='object' and octet_length(payload::text)<=200000),
 updated_at timestamptz not null default now()
);
alter table public.personal_part0_monitor enable row level security;
revoke all on public.personal_part0_monitor from public,anon,authenticated,service_role;

create function public.personal_part0_keys(v jsonb, k text[]) returns boolean
language sql immutable set search_path=pg_catalog as $$
 select coalesce(jsonb_typeof(v)='object' and v ?& k and v-k='{}'::jsonb,false)
$$;
create function public.personal_part0_number(v jsonb, nullable boolean default false) returns numeric
language plpgsql immutable set search_path=pg_catalog as $$
declare n numeric;
begin
 if nullable and v='null'::jsonb then return null;end if;
 if jsonb_typeof(v) is distinct from 'number' then raise exception 'part0_number_invalid';end if;
 n:=(v#>>'{}')::numeric;
 if n::text in ('NaN','Infinity','-Infinity') or abs(n)>1000000000000 then raise exception 'part0_number_invalid';end if;
 return n;
end $$;
create function public.personal_part0_time(v jsonb, nullable boolean default false) returns timestamptz
language plpgsql immutable set search_path=pg_catalog as $$
declare t timestamptz;
begin
 if nullable and v='null'::jsonb then return null;end if;
 if jsonb_typeof(v) is distinct from 'string' or v#>>'{}' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' then raise exception 'part0_time_invalid';end if;
 t:=(v#>>'{}')::timestamptz;
 if not isfinite(t) then raise exception 'part0_time_invalid';end if;
 return t;
end $$;
revoke all on function public.personal_part0_keys(jsonb,text[]), public.personal_part0_number(jsonb,boolean), public.personal_part0_time(jsonb,boolean) from public,anon,authenticated,service_role;

create function public.personal_sync_part0_monitor(p_payload jsonb,p_writer_secret text)
returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,public as $$
declare
 at_time timestamptz; source_time timestamptz; prior public.personal_part0_monitor%rowtype;
 a jsonb; r jsonb; row_data jsonb; obj jsonb; v jsonb; key_name text; seen text[]:=array[]::text[];
 q numeric; available numeric; cost numeric; mv numeric; px numeric; pnl numeric; pct numeric; expected numeric;
begin
 if auth.uid() is null or public.personal_current_user_is_active() is distinct from true then raise exception 'part0_auth_required';end if;
 if p_writer_secret is null or p_writer_secret !~ '^[A-Za-z0-9_-]{40,160}$'
 or not exists(select 1 from public.personal_part4_sync_writer_credentials c where c.owner_user_id=auth.uid() and c.secret_sha256=encode(extensions.digest(p_writer_secret,'sha256'),'hex')) then raise exception 'part0_trusted_writer_required';end if;
 if not public.personal_part0_keys(p_payload,array['schemaVersion','observedAt','account','trades','runtime','events'])
 or p_payload->'schemaVersion' is distinct from '1'::jsonb or octet_length(p_payload::text)>200000 then raise exception 'part0_shape_invalid';end if;
 at_time:=public.personal_part0_time(p_payload->'observedAt');
 if at_time < now()-interval '30 minutes' or at_time > now()+interval '5 minutes' then raise exception 'part0_observation_stale';end if;
 a:=p_payload->'account';
 if a <> 'null'::jsonb then
  if not public.personal_part0_keys(a,array['asOf','source','totalAssets','availableCash','positionValue','totalProfit','positions']) or a->'source' is distinct from '"vps_saved_account"'::jsonb then raise exception 'part0_account_invalid';end if;
  if public.personal_part0_time(a->'asOf')>at_time then raise exception 'part0_source_future';end if;
  foreach key_name in array array['totalAssets','availableCash','positionValue'] loop
   if public.personal_part0_number(a->key_name)<0 then raise exception 'part0_account_negative';end if;
  end loop;
  perform public.personal_part0_number(a->'totalProfit',true);
  if jsonb_typeof(a->'positions') is distinct from 'array' or jsonb_array_length(a->'positions')>50 then raise exception 'part0_positions_invalid';end if;
  for row_data in select value from jsonb_array_elements(a->'positions') loop
   if not public.personal_part0_keys(row_data,array['symbol','quantity','availableQuantity','averageCost','marketValue','price','pnl','pnlPct'])
   or jsonb_typeof(row_data->'symbol') is distinct from 'string' or row_data->>'symbol' !~ '^[0-9]{6}\.(SH|SZ)$' or row_data->>'symbol'=any(seen) then raise exception 'part0_position_invalid';end if;
   seen:=array_append(seen,row_data->>'symbol');
   q:=public.personal_part0_number(row_data->'quantity');available:=public.personal_part0_number(row_data->'availableQuantity');
   if q<=0 or q>1000000000 or q<>trunc(q) or available<0 or available>q or available<>trunc(available) then raise exception 'part0_quantity_invalid';end if;
   cost:=public.personal_part0_number(row_data->'averageCost',true);mv:=public.personal_part0_number(row_data->'marketValue',true);
   px:=public.personal_part0_number(row_data->'price',true);pnl:=public.personal_part0_number(row_data->'pnl',true);pct:=public.personal_part0_number(row_data->'pnlPct',true);
   if cost<0 or mv<0 then raise exception 'part0_cost_invalid';end if;
   expected:=mv/q;
   if (px is null)<>(expected is null) or abs(px-expected)>0.000001 then raise exception 'part0_price_mismatch';end if;
   expected:=mv-q*cost;
   if (pnl is null)<>(expected is null) or abs(pnl-expected)>0.000001 then raise exception 'part0_pnl_mismatch';end if;
   expected:=case when cost>0 then (mv-q*cost)/(q*cost)*100 else null end;
   if (pct is null)<>(expected is null) or abs(pct-expected)>0.000001 then raise exception 'part0_pnl_percent_mismatch';end if;
  end loop;
 end if;
 if jsonb_typeof(p_payload->'trades') is distinct from 'array' or jsonb_array_length(p_payload->'trades')>100 then raise exception 'part0_trades_invalid';end if;
 for row_data in select value from jsonb_array_elements(p_payload->'trades') loop
  if not public.personal_part0_keys(row_data,array['symbol','side','quantity','price','amount','tradeDate','confirmedAt','state'])
  or jsonb_typeof(row_data->'symbol') is distinct from 'string' or row_data->>'symbol' !~ '^[0-9]{6}\.(SH|SZ)$'
  or jsonb_typeof(row_data->'side') is distinct from 'string' or jsonb_typeof(row_data->'state') is distinct from 'string'
  or row_data->>'side' not in ('buy','sell') or row_data->>'state' not in ('reconciled','fill_observed')
  or jsonb_typeof(row_data->'tradeDate') is distinct from 'string' or row_data->>'tradeDate' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then raise exception 'part0_trade_invalid';end if;
  q:=public.personal_part0_number(row_data->'quantity');px:=public.personal_part0_number(row_data->'price');mv:=public.personal_part0_number(row_data->'amount');
  if q<=0 or q>1000000000 or q<>trunc(q) or px<=0 or abs(mv-q*px)>0.000001 then raise exception 'part0_fill_invalid';end if;
  source_time:=public.personal_part0_time(row_data->'confirmedAt');
  if source_time>at_time or (row_data->>'tradeDate')::date>(source_time at time zone 'Asia/Shanghai')::date then raise exception 'part0_fill_future';end if;
 end loop;
 r:=p_payload->'runtime';
 if not public.personal_part0_keys(r,array['authorization','strategy','dashboard','activeSymbols','strategyCycleAt','quoteAsOf']) then raise exception 'part0_runtime_invalid';end if;
 obj:=r->'authorization';
 if not public.personal_part0_keys(obj,array['status','expiresAt','nextRunAt']) or jsonb_typeof(obj->'status') is distinct from 'string' or obj->>'status' not in ('expired','paused','requires_review','unknown') or obj->'nextRunAt' is distinct from 'null'::jsonb then raise exception 'part0_authority_invalid';end if;
 source_time:=public.personal_part0_time(obj->'expiresAt');
 if obj->>'status'='expired' and source_time>at_time then raise exception 'part0_expiry_invalid';end if;
 foreach key_name in array array['strategy','dashboard'] loop
  obj:=r->key_name;
  if not public.personal_part0_keys(obj,case when key_name='strategy' then array['status','asOf'] else array['status','asOf','targetDate'] end)
  or jsonb_typeof(obj->'status') is distinct from 'string' or obj->>'status' not in ('ok','running','error','unknown') then raise exception 'part0_task_invalid';end if;
  if public.personal_part0_time(obj->'asOf',true)>at_time then raise exception 'part0_task_future';end if;
  if key_name='dashboard' and obj->'targetDate'<>'null'::jsonb then
   if jsonb_typeof(obj->'targetDate') is distinct from 'string' or obj->>'targetDate' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then raise exception 'part0_target_date_invalid';end if;
   perform (obj->>'targetDate')::date;
  end if;
 end loop;
 foreach key_name in array array['strategyCycleAt','quoteAsOf'] loop
  if public.personal_part0_time(r->key_name,true)>at_time then raise exception 'part0_runtime_future';end if;
 end loop;
 if jsonb_typeof(r->'activeSymbols') is distinct from 'array' or jsonb_array_length(r->'activeSymbols')>50 then raise exception 'part0_active_invalid';end if;
 seen:=array[]::text[];
 for v in select value from jsonb_array_elements(r->'activeSymbols') loop
  if jsonb_typeof(v) is distinct from 'string' or v#>>'{}' !~ '^[0-9]{6}\.(SH|SZ)$' or v#>>'{}'=any(seen) then raise exception 'part0_active_invalid';end if;
  seen:=array_append(seen,v#>>'{}');
 end loop;
 if jsonb_typeof(p_payload->'events') is distinct from 'array' or jsonb_array_length(p_payload->'events')>40 then raise exception 'part0_events_invalid';end if;
 for row_data in select value from jsonb_array_elements(p_payload->'events') loop
  if not public.personal_part0_keys(row_data,array['at','kind','status','code'])
  or jsonb_typeof(row_data->'kind') is distinct from 'string' or jsonb_typeof(row_data->'status') is distinct from 'string' or jsonb_typeof(row_data->'code') is distinct from 'string'
  or row_data->>'kind' not in ('strategy','dashboard') or row_data->>'status' not in ('ok','error','running','unknown')
  or row_data->>'code' not in ('cycle_succeeded','cycle_failed','daily_refresh') then raise exception 'part0_event_invalid';end if;
  if public.personal_part0_time(row_data->'at')>at_time then raise exception 'part0_event_future';end if;
 end loop;
 perform pg_advisory_xact_lock(hashtext('personal_part0:'||auth.uid()::text));
 select * into prior from public.personal_part0_monitor where owner_user_id=auth.uid() for update;
 if found then
  if at_time<prior.observed_at then raise exception 'part0_regression_refused';end if;
  if at_time=prior.observed_at then
   if p_payload<>prior.payload then raise exception 'part0_same_time_conflict';end if;
   return jsonb_build_object('stored',true,'idempotent',true);
  end if;
 end if;
 insert into public.personal_part0_monitor(owner_user_id,observed_at,payload) values(auth.uid(),at_time,p_payload)
 on conflict(owner_user_id) do update set observed_at=excluded.observed_at,payload=excluded.payload,updated_at=now();
 return jsonb_build_object('stored',true,'idempotent',false);
end $$;
revoke all on function public.personal_sync_part0_monitor(jsonb,text) from public,anon,authenticated,service_role;
grant execute on function public.personal_sync_part0_monitor(jsonb,text) to authenticated;

create function public.personal_get_part0_monitor() returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,public as $$
begin
 if auth.uid() is null or public.personal_current_user_is_active() is distinct from true then raise exception 'part0_auth_required';end if;
 return (select payload from public.personal_part0_monitor where owner_user_id=auth.uid());
end $$;
revoke all on function public.personal_get_part0_monitor() from public,anon,authenticated,service_role;
grant execute on function public.personal_get_part0_monitor() to authenticated;
notify pgrst,'reload schema';
commit;
