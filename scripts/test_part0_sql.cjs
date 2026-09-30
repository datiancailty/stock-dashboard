'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {PGlite}=require('@electric-sql/pglite');
const {pgcrypto}=require('@electric-sql/pglite/contrib/pgcrypto');
const ROOT=path.resolve(__dirname,'..'),SQL=path.join(ROOT,'supabase/staged');
const USER='00000000-0000-4000-8000-000000000001',OTHER='00000000-0000-4000-8000-000000000002';
const SECRET='synthetic_local_only_writer_capability_1234567890';
const db=new PGlite({extensions:{pgcrypto}});let checks=0;
const check=(value,label)=>{assert.ok(value,label);checks++;};
async function reject(sql,args=[]){await assert.rejects(db.query(sql,args));checks++;}
(async()=>{try{
 await db.exec(`create role anon;create role authenticated;create role service_role;create schema auth;create schema extensions;create extension pgcrypto with schema extensions;
 create table auth.users(id uuid primary key);insert into auth.users values('${USER}'),('${OTHER}');
 create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
 grant usage on schema auth to authenticated,anon;grant execute on function auth.uid() to authenticated,anon;
 create function public.personal_current_user_is_active() returns boolean language sql stable as $$select auth.uid() is not null$$;
 create table public.personal_part4_sync_writer_credentials(owner_user_id uuid primary key, secret_sha256 text not null);
 revoke all on public.personal_part4_sync_writer_credentials from public,anon,authenticated,service_role;
 create function public.personal_get_refresh_health() returns jsonb language sql as $$select '{}'::jsonb$$;`);
 await db.query("insert into public.personal_part4_sync_writer_credentials values($1,encode(extensions.digest($2,'sha256'),'hex'))",[USER,SECRET]);
 const pre=(await db.query(fs.readFileSync(path.join(SQL,'20260930010000_part0_monitor_preflight.sql'),'utf8'))).rows[0];
 check(Number(pre.check_count)===10&&Number(pre.failed_count)===0,'preflight passes on expected structure');
 if(process.argv.includes('--preflight-only')){console.log(JSON.stringify({passed:true,checks,preflightOnly:true}));return;}
 const migration=path.join(SQL,'20260930010000_part0_monitor.sql');
 check(fs.existsSync(migration),'Part0 migration not implemented');
 await db.exec(fs.readFileSync(migration,'utf8'));
 const now=new Date().toISOString(),old=new Date(Date.now()-60000).toISOString();
 const payload={schemaVersion:1,observedAt:now,account:{asOf:old,source:'vps_saved_account',totalAssets:10000,availableCash:8900,positionValue:1100,totalProfit:100,positions:[{symbol:'600000.SH',quantity:100,availableQuantity:0,averageCost:10,marketValue:1100,price:11,pnl:100,pnlPct:10}]},trades:[{symbol:'600000.SH',side:'buy',quantity:100,price:10,amount:1000,tradeDate:now.slice(0,10),confirmedAt:old,state:'reconciled'}],runtime:{authorization:{status:'expired',expiresAt:old,nextRunAt:null},strategy:{status:'ok',asOf:old},dashboard:{status:'ok',asOf:old,targetDate:now.slice(0,10)},activeSymbols:['600000.SH'],strategyCycleAt:old,quoteAsOf:old},events:[{at:old,kind:'strategy',status:'error',code:'cycle_failed'}]};
 await db.query("select set_config('request.jwt.claim.sub',$1,false)",[USER]);await db.exec('set role authenticated');
 const write=async p=>(await db.query('select public.personal_sync_part0_monitor($1::jsonb,$2) as x',[JSON.stringify(p),SECRET])).rows[0].x;
 await reject('select * from public.personal_part0_monitor');
 await reject('select public.personal_sync_part0_monitor($1::jsonb,$2)',[JSON.stringify(payload),'bad']);
 check((await write(payload)).stored===true,'trusted writer succeeds');
 const read=async()=>(await db.query('select public.personal_get_part0_monitor() as x')).rows[0].x;
 assert.deepEqual(await read(),payload);checks++;
 check((await write(payload)).idempotent===true,'same bytes are idempotent');
 for(const mutate of [p=>p.account.rawToken='not allowed',p=>p.account.positions[0].availableQuantity=101,p=>p.account.positions[0].pnl=999,p=>p.trades[0].brokerOrderId='private',p=>p.trades[0].price=0,p=>p.runtime.authorization.status='ARMED',p=>p.events[0].code='raw secret',p=>p.account.asOf='2039-01-01T00:00:00Z',p=>p.runtime.extra='bad',p=>p.account.source=null,p=>p.trades[0].side=null,p=>p.runtime.authorization.status=null,p=>p.runtime.strategy.status=null,p=>p.events[0].kind=null,p=>p.events[0].status=null,p=>p.events[0].code=null]){
  const p=structuredClone(payload);p.observedAt=new Date(Date.now()+1000).toISOString();mutate(p);await assert.rejects(write(p));checks++;
 }
 assert.deepEqual(await read(),payload);checks++;
 await db.query("select set_config('request.jwt.claim.sub',$1,false)",[OTHER]);check(await read()===null,'other owner cannot read');await assert.rejects(write(payload));checks++;
 await db.exec('reset role;set role anon');await reject('select public.personal_get_part0_monitor()');await reject('select public.personal_sync_part0_monitor($1::jsonb,$2)',[JSON.stringify(payload),SECRET]);
 await db.exec('reset role');
 const postfile=path.join(SQL,'20260930010000_part0_monitor_postflight.sql');check(fs.existsSync(postfile),'postflight exists');
 const post=(await db.query(fs.readFileSync(postfile,'utf8'))).rows[0];check(Number(post.failed_count)===0,'postflight passes');
 if(process.env.PART0_SQL_PRIVATE_CAPTURE){
  const real=JSON.parse(fs.readFileSync(process.env.PART0_SQL_PRIVATE_CAPTURE,'utf8')).payload;
  await db.exec('delete from public.personal_part0_monitor');await db.query("select set_config('request.jwt.claim.sub',$1,false)",[USER]);await db.exec('set role authenticated');
  check((await write(real)).stored===true,'actual saved read model satisfies local SQL contract');assert.deepEqual(await read(),real);checks++;await db.exec('reset role');
 }
 console.log(JSON.stringify({passed:true,checks,syntheticOnly:!process.env.PART0_SQL_PRIVATE_CAPTURE,privateCaptureChecked:!!process.env.PART0_SQL_PRIVATE_CAPTURE,hostedUsed:false}));
 }catch(e){console.error(JSON.stringify({passed:false,checks,message:e.message,code:e.code}));process.exitCode=1;}finally{await db.close();}})();
