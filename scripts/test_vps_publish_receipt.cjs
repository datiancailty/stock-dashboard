'use strict';
// Synthetic-only full PostgreSQL publish chain, no network/Hosted/credentials.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {createRequire}=require('node:module'),{createHash}=require('node:crypto');
const req=createRequire(process.env.PGLITE_REQUIRE_FROM||__filename);
const {PGlite}=req('@electric-sql/pglite'),{pgcrypto}=req('@electric-sql/pglite/contrib/pgcrypto');
const ROOT=path.resolve(__dirname,'..'),M=path.join(ROOT,'supabase/migrations');
const migrations=['20260825000000_vps_control_plane_stage1.sql','20260825001000_vps_sync_gateway_stage2.sql','20260825002000_vps_sync_gateway_hardening.sql','20260825003000_vps_sync_gateway_protocol_v2.sql','20260825004000_vps_sync_gateway_protocol_v2_acl_closure.sql','20260829001000_vps_digest_search_path_fix.sql','20260829005000_vps_sync_private_projection_bridge.sql'];
const sha=x=>createHash('sha256').update(x).digest('hex'),h=x=>x.repeat(64);
const adapter='v31f-15m-miaoxiang-sim-adapter',device='stock-sim-v31f-15m';
const flag=process.argv.indexOf('--candidate'),candidate=flag<0?null:fs.readFileSync(process.argv[flag+1],'utf8');
let checks=0;const check=(x,msg)=>{assert.ok(x,msg);checks++;};
async function run(namespace){
 const db=new PGlite({extensions:{pgcrypto}});
 async function reject(sql,args,code,pattern){let error;try{await db.query(sql,args);}catch(e){error=e;}check(!!error,'expected rejection');check(error.code===code,'SQLSTATE '+code);if(pattern)check(pattern.test(error.message),'expected error class');}
 async function counts(){return (await db.query("select (select count(*) from public.vps_sync_ack_receipts)::int as ack_receipts,(select count(*) from public.vps_sync_request_receipts)::int as requests,(select count(*) from public.vps_sync_acks)::int as acks,(select active_revision_no from public.vps_runtime_snapshot where id=true) as active")).rows[0];}
 async function state(){return (await db.query("select jsonb_build_object('runtime',(select to_jsonb(r) from public.vps_runtime_snapshot r where id=true),'revisions',(select jsonb_agg(to_jsonb(r) order by revision_no) from public.vps_whitelist_revisions r),'acks',(select jsonb_agg(to_jsonb(r) order by ack_id) from public.vps_sync_ack_receipts r),'receipts',(select jsonb_agg(to_jsonb(r) order by request_id) from public.vps_sync_request_receipts r),'symbols',(select jsonb_agg(to_jsonb(r) order by symbol) from public.vps_symbol_states r)) as s")).rows[0].s;}
 async function seed(revision,expired=false){
  const times=(await db.query("select public.vps_contract_time_text(now()-interval '1 day') as created,public.vps_contract_time_text(now()+($1::int * interval '1 day')) as expires",[expired?-1:7])).rows[0];
  const symbols=Array.from({length:28},(_,i)=>String(i+1).padStart(6,'0')+'.SZ');
  const members=(await db.query('select public.vps_members_sha256($1::text[]) as value',[symbols])).rows[0].value;
  const payload=(await db.query("select public.vps_revision_payload_text($1,$2,$3,'DRY_RUN',$4,$5,$6,$7,'synthetic-local-snapshot',$8,$9::text[]) as value",[revision,device,adapter,times.created,times.expires,h('1'),members,h('3'),symbols])).rows[0].value;
  const payloadSha=sha(payload),raw=(await db.query('select public.vps_revision_raw_contract_text($1,$2) as value',[payload,payloadSha])).rows[0].value;
  await db.exec('begin');
  await db.query(`insert into public.vps_whitelist_revisions(revision_no,status,desired_symbol_count,contract_protocol_version,target_device_id,contract_adapter_id,contract_mode,source_policy_sha256,members_sha256,required_snapshot_id,required_snapshot_sha256,contract_created_at,contract_expires_at,control_payload_sha256,control_raw_contract,control_raw_contract_sha256) values($1,'sync_pending',28,2,$2,$3,'DRY_RUN',$4,$5,'synthetic-local-snapshot',$6,$7::timestamptz,$8::timestamptz,$9,$10,$11)`,[revision,device,adapter,h('1'),members,h('3'),times.created,times.expires,payloadSha,raw,sha(raw)]);
  await db.query('insert into public.vps_whitelist_revision_items(revision_id,symbol,sort_order) select r.id,x.symbol,x.n-1 from public.vps_whitelist_revisions r cross join unnest($1::text[]) with ordinality x(symbol,n) where r.revision_no=$2',[symbols,revision]);await db.exec('commit');
  const stamp=new Date().toISOString(),generation=revision+3;
  const ack=(stage)=>({ack_id:sha(revision+':'+stage),revision_no:revision,sync_stage:stage,generation,pack_sha256:h('6'),reported_at_cn:stamp,message:'维护同步；未运行策略周期。',rejection_code:null,control_payload_sha256:payloadSha,control_raw_contract_sha256:sha(raw),members_sha256:members,required_snapshot_id:'synthetic-local-snapshot',required_snapshot_sha256:h('3'),adapter_id:adapter,mode:'DRY_RUN'});
  return {schema_version:2,adapter_id:adapter,mode:'DRY_RUN',health_status:'unknown',generated_at_cn:stamp,active_revision_no:revision,active_generation:generation,active_pack_sha256:h('6'),active_control_payload_sha256:payloadSha,active_control_raw_contract_sha256:sha(raw),active_members_sha256:members,active_snapshot_id:'synthetic-local-snapshot',active_snapshot_sha256:h('3'),last_control_pull_at_cn:stamp,last_strategy_cycle_at_cn:null,last_quote_snapshot_at_cn:null,last_account_snapshot_at_cn:null,last_eod_at_cn:null,provider_reads_used:null,provider_reads_cap:null,state_summary:'维护同步；未运行策略周期；maintenance-only/data-ready；无行情或账户调用。',sanitized_error:null,symbol_states:symbols.map(symbol=>({symbol,status_key:'maintenance_data_ready',status_reason:'维护同步；未运行策略周期。',source_generated_at_cn:stamp,data_fresh_at_cn:null,active_revision_no:revision,is_in_active_whitelist:true})),paper_positions:[],events:[],acks:[ack('received'),ack('activated')]};
 }
 try{
  await db.exec(`create role anon;create role authenticated;create role service_role;create schema auth;create schema extensions;create table auth.users(id uuid primary key);create extension pgcrypto with schema ${namespace};create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;`);
  for(const name of migrations)await db.exec(fs.readFileSync(path.join(M,name),'utf8'));
  // Reproduce the manually observed legacy ACL, without calling a live service.
  await db.exec('grant execute on function public.vps_sync_ingest_report_legacy(text,jsonb) to public,anon,authenticated,service_role');
  await db.query('insert into public.vps_sync_devices(device_id,enabled) values($1,true) on conflict(device_id) do update set enabled=true',[device]);
  const report=await seed(3);const before=await state();
  if(candidate)await db.exec(candidate);
  if(process.argv.includes('--ack-only-diagnostic')){
   const def=(await db.query("select pg_get_functiondef('public.vps_sync_ingest_report_v2_base(text,jsonb)'::regprocedure) as def")).rows[0].def;
   const changed=def.replace('on conflict (device_id, ack_id) do nothing','on conflict on constraint vps_sync_ack_receipts_pkey do nothing');assert.notEqual(changed,def);await db.exec(changed);
  }
  if(process.argv.includes('--partial-index-diagnostic')){
   const def=(await db.query("select pg_get_functiondef('public.vps_sync_ingest_report_legacy(text,jsonb)'::regprocedure) as def")).rows[0].def;
   const changed=def.replace('on conflict (source_ack_key) do nothing','on conflict (source_ack_key) where source_ack_key is not null do nothing');assert.notEqual(changed,def);await db.exec(changed);
   report.events=[{occurred_at_cn:report.generated_at_cn,severity:'info',event_code:'maintenance_probe',message:'本地合成事件。',symbol:null,action:null,revision_no:3,generation:6}];
  }
  assert.deepEqual(await state(),before);checks++;
  // RED: exact old source fails here with 42702/ack_id; no failed-row fabrication.
  await db.exec('set role service_role');
  const publish='select public.vps_sync_publish_request($1,$2,$3,$4::jsonb) as result';
  const args=[device,h('c'),sha(JSON.stringify(report)),JSON.stringify(report)];
  const response=(await db.query(publish,args)).rows[0].result;await db.exec('reset role');
  assert.deepEqual([...response.accepted_ack_ids].sort(),report.acks.map(x=>x.ack_id).sort());checks++;
  assert.deepEqual(await counts(),{ack_receipts:2,requests:1,acks:2,active:3});checks++;
  const runtime=(await db.query('select * from public.vps_runtime_snapshot where id=true')).rows[0];
  check(runtime.mode==='DRY_RUN'&&runtime.health_status==='unknown','maintenance mode and health');
  for(const key of ['last_strategy_cycle_at','last_quote_snapshot_at','last_account_snapshot_at','last_eod_at','provider_reads_used','provider_reads_cap'])check(runtime[key]===null,'no fake observation '+key);
  const symbols=(await db.query('select count(*)::int as n from public.vps_symbol_states where status_key=$1 and data_fresh_at is null',['maintenance_data_ready'])).rows[0].n;check(symbols===28,'exact state count, no quote freshness');
  const eventReport=structuredClone(report);eventReport.events=[{occurred_at_cn:report.generated_at_cn,severity:'info',event_code:'maintenance_probe',message:'本地合成事件。',symbol:null,action:null,revision_no:3,generation:6}];
  for(const identity of ['event1','event2'])await db.query(publish,[device,sha(identity),sha(JSON.stringify(eventReport)),JSON.stringify(eventReport)]);
  check((await db.query('select count(*)::int as n from public.vps_runtime_events')).rows[0].n===1,'event partial-index conflict target is idempotent');
  const accepted=await state();assert.deepEqual((await db.query(publish,args)).rows[0].result,response);assert.deepEqual(await state(),accepted);checks+=2;
  await reject(publish,[device,h('c'),h('e'),JSON.stringify(report)],'P0001',/id conflicts/);assert.deepEqual(await state(),accepted);checks++;
  for(const kind of ['ack_content','immutable','mode']){
   const changed=structuredClone(report);if(kind==='ack_content')changed.acks[0].message='changed';if(kind==='immutable')changed.acks[0].members_sha256=h('f');if(kind==='mode')changed.mode='ARMED';
   await reject(publish,[device,sha(kind),sha(JSON.stringify(changed)),JSON.stringify(changed)],'P0001');assert.deepEqual(await state(),accepted);checks++;
  }
  const expired=await seed(7,true),beforeExpired=await state();await reject(publish,[device,sha('expired'),sha(JSON.stringify(expired)),JSON.stringify(expired)],'P0001',/expired/);assert.deepEqual(await state(),beforeExpired);checks++;
  for(const role of ['anon','authenticated','service_role']){
   await db.exec('set role '+role);await reject('select public.vps_sync_ingest_report_legacy($1,$2::jsonb)',[device,JSON.stringify(report)],'42501');await db.exec('reset role');
  }
  for(const role of ['anon','authenticated']){
   await db.exec('set role '+role);await reject(publish,args,'42501');await db.exec('reset role');
  }
  if(candidate){
   const beforeRepeat=await state();await db.exec(candidate);assert.deepEqual(await state(),beforeRepeat);checks++;
   const sig='public.vps_sync_ingest_report_v2_base(text,jsonb)';const original=(await db.query('select pg_get_functiondef($1::regprocedure) as def',[sig])).rows[0].def;
   const altered=original.replace(/declare/i,'declare\n-- synthetic drift');check(altered!==original,'drift probe changed body');await db.exec(altered);
   let blocked;try{await db.exec(candidate);}catch(e){blocked=e;}await db.exec('rollback');check(blocked?.code==='P0001'&&/source changed/.test(blocked.message),'unexpected source drift fails closed');
  }
  return {namespace,passed:true};
 }finally{await db.close();}
}
(async()=>{try{const results=[];for(const ns of ['extensions','public'])results.push(await run(ns));console.log(JSON.stringify({passed:true,checks,results,syntheticOnly:true,hostedUsed:false}));}catch(e){console.error(JSON.stringify({passed:false,checks,code:e.code||null,message:e.message,where:e.where||null}));process.exitCode=1;}})();
