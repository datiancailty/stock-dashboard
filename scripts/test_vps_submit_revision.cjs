'use strict';
// Local PostgreSQL/WASM regression; synthetic identities and symbols only.
// No Hosted, SSH, provider, broker, environment secret or browser access.
const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
const {createRequire}=require('node:module');const {createHash}=require('node:crypto');
const req=createRequire(process.env.PGLITE_REQUIRE_FROM||__filename);
const {PGlite}=req('@electric-sql/pglite');const {pgcrypto}=req('@electric-sql/pglite/contrib/pgcrypto');
const ROOT=path.resolve(__dirname,'..');const M=path.join(ROOT,'supabase/migrations');
const USER='00000000-0000-4000-8000-000000000001';const OTHER='00000000-0000-4000-8000-000000000002';
const TARGET='stock-sim-v31f-15m';
const read=n=>fs.readFileSync(path.join(M,n),'utf8');
const base=read('20260825000000_vps_control_plane_stage1.sql');
const v2=read('20260825003000_vps_sync_gateway_protocol_v2.sql');
const stage3=read('20260829003000_dashboard_private_data_schema_stage3.sql');
function fn(source,name){const escaped=name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');const m=source.match(new RegExp('create or replace function public\\.'+escaped+'\\s*\\([\\s\\S]*?\\$\\$;','i'));assert.ok(m,'function missing: '+name);return m[0];}
const original=fn(stage3,'vps_submit_whitelist_revision');
const md5=s=>createHash('md5').update(s).digest('hex');
assert.equal(md5(original.split('as $$')[1].split('$$;')[0]),'17ff3d784f7695565f678fb22c7945fc');
const symbols=Array.from({length:28},(_,i)=>String(i+1).padStart(6,'0')+'.SZ');
const db=new PGlite({extensions:{pgcrypto}});let checks=0;
const check=(value,message)=>{assert.ok(value,message);checks++;};
async function reject(sql,args,code,pattern){let error;try{await db.query(sql,args);}catch(e){error=e;}check(!!error,'expected rejection');if(code)check(error.code===code,'expected SQLSTATE '+code);if(pattern)check(pattern.test(error.message),'unexpected rejection reason');}
async function submit(list,expected,note=null){return (await db.query('select * from public.vps_submit_whitelist_revision($1::text[],$2::text,$3::bigint)',[list,note,expected])).rows[0];}
async function count(){return Number((await db.query('select count(*) as n from public.vps_whitelist_revisions')).rows[0].n);}
(async()=>{try{
 await db.exec(`create role anon;create role authenticated;create role service_role;create schema auth;create schema extensions;create extension pgcrypto with schema extensions;
 create table auth.users(id uuid primary key);insert into auth.users values('${USER}'),('${OTHER}');
 create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
 grant usage on schema auth to anon,authenticated;grant execute on function auth.uid() to anon,authenticated;`);
 await db.exec(base);
 for(const table of ['vps_control_settings','vps_whitelist_revisions']){const m=v2.match(new RegExp('alter table public\\.'+table+'[\\s\\S]*?;','i'));assert.ok(m);await db.exec(m[0]);}
 for(const name of ['vps_contract_time_text','vps_members_sha256','vps_revision_payload_text','vps_revision_raw_contract_text','vps_guard_v2_revision_immutability'])await db.exec(fn(v2,name));
 await db.exec('alter function public.vps_members_sha256(text[]) set search_path=pg_catalog,extensions,public;create trigger vps_v2_revision_immutability before update on public.vps_whitelist_revisions for each row execute function public.vps_guard_v2_revision_immutability();');
 await db.exec(original);await db.exec(fn(stage3,'vps_get_whitelist_control_state'));
 await db.exec(`revoke all on function public.vps_submit_whitelist_revision(text[],text,bigint) from public,anon,service_role;grant execute on function public.vps_submit_whitelist_revision(text[],text,bigint) to authenticated;
 insert into public.vps_admins(user_id) values('${USER}');
 update public.vps_control_settings set required_snapshot_id='synthetic-local-snapshot',required_snapshot_sha256=repeat('a',64);
 insert into public.vps_whitelist_revisions(revision_no,status,requested_by,desired_symbol_count,target_device_id) values(1,'sync_pending','${USER}',22,'${TARGET}');`);
 await db.query('insert into public.vps_whitelist_revision_items(revision_id,symbol,sort_order) select r.id, x.symbol, x.n-1 from public.vps_whitelist_revisions r cross join unnest($1::text[]) with ordinality as x(symbol,n) where r.revision_no=1',[symbols.slice(0,22)]);
 const before=(await db.query('select pg_get_functiondef(oid) as def, proacl::text as acl,prosecdef,proconfig from pg_proc where oid=$1::regprocedure',['public.vps_submit_whitelist_revision(text[],text,bigint)'])).rows[0];
 const flag=process.argv.indexOf('--candidate');if(flag!==-1){assert.ok(process.argv[flag+1]);await db.exec(fs.readFileSync(process.argv[flag+1],'utf8'));}
 await db.query("select set_config('request.jwt.claim.sub',$1,false)",[USER]);await db.exec('set role authenticated');
 // RED tracer: on the exact deployed body, this raises 42702/status ambiguity.
 const result=await submit(symbols,1);check(Number(result.revision_no)===2,'revision increments');check(result.status==='submitted','not active');check(Number(result.base_revision_no)===1,'base binding');
 const state=(await db.query('select public.vps_get_whitelist_control_state() as s')).rows[0].s;
 check(state.desired_symbols.length===28,'exact count');assert.deepEqual(state.desired_symbols,symbols);checks++;check(state.active_revision_no===null,'no fake activation');check(state.mode==='DRY_RUN','no mode escalation');
 const old=(await db.query('select status,(select count(*) from public.vps_whitelist_revision_items i where i.revision_id=r.id) as item_count from public.vps_whitelist_revisions r where revision_no=1')).rows[0];check(old.status==='superseded','old pending is superseded');check(Number(old.item_count)===22,'old membership preserved');
 const frozen=(await db.query('select * from public.vps_whitelist_revisions where revision_no=2')).rows[0];check(frozen.contract_mode==='DRY_RUN','contract remains dryrun');check(frozen.required_snapshot_id==='synthetic-local-snapshot','snapshot bound');check(frozen.control_raw_contract.endsWith('\n'),'exact terminal newline');
 const sha256=s=>createHash('sha256').update(s).digest('hex');
 check(sha256(frozen.control_raw_contract)===frozen.control_raw_contract_sha256,'raw wire hash exact');
 check(sha256('vps-members-v2\n'+[...symbols].sort().join('\n')+'\n')===frozen.members_sha256,'membership wire hash exact');
 await reject('select * from public.vps_submit_whitelist_revision($1::text[],null,1)',[symbols],'P0001',/base revision changed/);check(await count()===2,'stale submit does not insert');
 for(const list of [[],[symbols[0],symbols[0]],['000001.BJ'],Array.from({length:51},(_,i)=>String(i+1).padStart(6,'0')+'.SZ')])await reject('select * from public.vps_submit_whitelist_revision($1::text[],null,2)',[list],'P0001');
 await reject('select * from public.vps_submit_whitelist_revision($1::text[],$2,2)',[symbols,'authorization'],'P0001',/note is invalid/);check(await count()===2,'validation refusals are atomic');
 await db.exec('reset role');const after=(await db.query('select proacl::text as acl,prosecdef,proconfig from pg_proc where oid=$1::regprocedure',['public.vps_submit_whitelist_revision(text[],text,bigint)'])).rows[0];assert.deepEqual(after,{acl:before.acl,prosecdef:before.prosecdef,proconfig:before.proconfig});checks++;
 await db.exec('set role anon');await reject('select * from public.vps_submit_whitelist_revision($1::text[],null,2)',[symbols],'42501');await db.exec('reset role;set role authenticated');await db.query("select set_config('request.jwt.claim.sub',$1,false)",[OTHER]);await reject('select * from public.vps_submit_whitelist_revision($1::text[],null,2)',[symbols],'42501');
 // Synthetic active-only base and a different device must remain isolated.
 await db.exec('reset role');await db.query("select set_config('request.jwt.claim.sub',$1,false)",[USER]);
 await db.exec("update public.vps_whitelist_revisions set status='active' where revision_no=2;insert into public.vps_whitelist_revisions(revision_no,status,desired_symbol_count,target_device_id) values(8,'sync_pending',1,'synthetic-other-device');set role authenticated");
 const next=await submit(symbols.slice(0,27),2);check(Number(next.revision_no)===9,'global revision allocation');check(Number(next.base_revision_no)===2,'active-only base accepted');
 check((await db.query('select status from public.vps_whitelist_revisions where revision_no=8')).rows[0].status==='sync_pending','other device not superseded');
 check((await db.query('select status from public.vps_whitelist_revisions where revision_no=2')).rows[0].status==='active','active history preserved');
 await db.exec('reset role');
 if(flag!==-1){
  const migration=fs.readFileSync(process.argv[flag+1],'utf8');
  const rowsBefore=(await db.query('select * from public.vps_whitelist_revisions order by revision_no')).rows;
  await db.exec(migration);
  assert.deepEqual((await db.query('select * from public.vps_whitelist_revisions order by revision_no')).rows,rowsBefore);checks++;
  const drift=fn(migration,'vps_submit_whitelist_revision').replace('as $$',()=> 'as $$\n-- synthetic unexpected function change');await db.exec(drift);
  let refused;try{await db.exec(migration);}catch(e){refused=e;}await db.exec('rollback');
  check(refused?.code==='P0001'&&/RPC source changed/.test(refused.message),'unknown source hash fails closed');
 }
 console.log(JSON.stringify({passed:true,checks,engine:'local PostgreSQL via PGlite 0.5.8',syntheticOnly:true,hostedUsed:false}));
 }catch(e){console.error(JSON.stringify({passed:false,checks,code:e.code||null,message:e.message,where:e.where||null}));process.exitCode=1;}finally{await db.close();}})();
