'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {JSDOM}=require('jsdom');const ROOT=path.resolve(__dirname,'..');
async function setup(t,deferredNames=['personal_append_trade_records']){
 const dom=new JSDOM(fs.readFileSync(path.join(ROOT,'index.html'),'utf8'),{url:'https://synthetic.invalid',runScripts:'outside-only',pretendToBeVisual:true});
 t.after(()=>dom.window.close());const w=dom.window;let callback;const pending=[],calls=[];
 w.fetch=async()=>({ok:true,json:async()=>[]});w.confirm=()=>true;
 w.STOCK_DASHBOARD_SUPABASE_CONFIG={url:'https://synthetic.invalid',anonKey:'synthetic'};
 w.supabase={createClient:()=>({auth:{onAuthStateChange:f=>{callback=f;},getSession:async()=>({data:{session:null}})},rpc:(name,args)=>{
  calls.push(name);if(deferredNames.includes(name))return new Promise((resolve,reject)=>pending.push({name,resolve,reject,args}));
  if(name==='personal_get_part6')return Promise.resolve({data:{trades:{records:[]},feedback:{records:[]},analysis:{status:'waiting'},recommendations:{records:[]}},error:null});
  return Promise.resolve({data:null,error:null});
 }})};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 const run=s=>vm.runInContext(s,dom.getInternalVMContext());await run(fs.readFileSync(path.join(ROOT,'assets/app.js'),'utf8'));
 const enter=(user,name)=>{w.uid=user;w.label=name;run("authSession={user:{id:uid}};trackedStocks=[{code:'600000',name:label}];market={stocks:[{code:'600000',price:10,confirmedBasis:{status:'ready',amount:1}}],events:[]};personalLoadedParts=new Set(['holdings','strategy']);render();");const d=w.document;d.querySelector('#tradePrice').value='12.345';d.querySelector('#tradeShares').value='789';d.querySelector('#tradeDate').value='2026-09-30';d.querySelector('#tradeRecordDialog').showModal();};
 return {w,doc:w.document,run,enter,pending,calls,auth:(s)=>callback(s?'SIGNED_IN':'SIGNED_OUT',s),submit:()=>w.document.querySelector('#tradeRecordForm').onsubmit({preventDefault(){}}),tick:async()=>{for(let i=0;i<20;i++)await Promise.resolve();}};
}
for(const replacement of [false,true])for(const failed of [false,true])test(`old trade completion cannot alter replacement DOM: replacement=${replacement},failure=${failed}`,async t=>{
 const a=await setup(t);a.enter('A','SYNTH_A_PRIVATE');const work=a.submit();await a.tick();assert.equal(a.pending.length,1);
 a.auth(replacement?{user:{id:'B'}}:null);
 assert.equal(a.doc.querySelector('#tradeRecordDialog').open,false);
 a.pending[0].resolve(failed?{data:null,error:{code:'synthetic_failure'}}:{data:{inserted:1},error:null});await work;
 assert.equal(a.doc.querySelector('#messageDialog').open,false,'late success/error must not open a dialog');
 assert.ok(!a.doc.body.textContent.includes('SYNTH_A_PRIVATE'));
 assert.equal(a.calls.filter(n=>n==='personal_append_trade_records').length,1,'never replay a writer');
});
test('old finally cannot unlock a newer account save',async t=>{
 const a=await setup(t);a.enter('A','SYNTH_A_PRIVATE');const first=a.submit();await a.tick();a.auth({user:{id:'B'}});
 a.enter('B','SYNTH_B_PRIVATE');const second=a.submit();await a.tick();assert.equal(a.pending.length,2);
 const button=a.doc.querySelector('#tradeRecordForm button[type=submit]');assert.equal(button.disabled,true);
 a.pending[0].resolve({data:{inserted:1},error:null});await first;
 assert.equal(button.disabled,true,'old finally must not reset the new operation button');assert.ok(!a.doc.body.textContent.includes('SYNTH_A_PRIVATE'));
 a.pending[1].resolve({data:{inserted:1},error:null});await second;
 assert.equal(button.disabled,false);assert.match(a.doc.querySelector('#messageText').textContent,/SYNTH_B_PRIVATE/);
});
test('CSV started by old account cannot submit after file read crosses account change',async t=>{
 const a=await setup(t);a.enter('A','SYNTH_A_PRIVATE');let finish;
 a.w.file={size:100,text:()=>new Promise(resolve=>{finish=resolve;})};
 const work=a.run('importTradeCsvFile(file)').catch(()=>{});await a.tick();a.auth({user:{id:'B'}});
 finish('日期,股票代码,操作,成交价格,成交股数\n2026-09-30,600000,买入,10,100');await a.tick();
 // If the old implementation wrongly reached a writer, finish it to avoid a hanging test.
 for(const p of a.pending)p.resolve({data:{inserted:1},error:null});await work;
 assert.equal(a.pending.length,0,'old CSV must not enter replacement owner writer');assert.equal(a.doc.querySelector('#messageDialog').open,false);
});
test('late CSV change handler neither opens old error nor clears a new file',async t=>{
 const a=await setup(t);a.enter('A','SYNTH_A_PRIVATE');let fail;
 const file={size:100,text:()=>new Promise((resolve,reject)=>{fail=reject;})},input=a.doc.querySelector('#tradeCsvFile');
 Object.defineProperty(input,'files',{value:[file]});Object.defineProperty(input,'value',{value:'A-private.csv',writable:true});
 const work=input.onchange({target:input});await a.tick();a.auth({user:{id:'B'}});input.value='B-private.csv';
 fail(new Error('SYNTH_A_PRIVATE_FILE'));await work;
 assert.equal(a.doc.querySelector('#messageDialog').open,false);assert.equal(input.value,'B-private.csv');
});
for(const kind of ['delete','refresh','open','feedback'])test(`old ${kind} completion does not act on replacement DOM`,async t=>{
 const method={delete:'personal_delete_trade_record',refresh:'personal_get_part6',open:'personal_get_part1',feedback:'personal_append_strategy_feedback_v2'}[kind];
 const a=await setup(t,[method]);a.enter('A','SYNTH_A_PRIVATE');a.doc.querySelector('#tradeRecordDialog').close();
 let work;
 if(kind==='delete'){a.run("tradeRecords=[{id:'synthetic-old',name:'SYNTH_A_PRIVATE',code:'600000',date:'2026-09-30'}]");work=a.run("deleteTradeRecord('synthetic-old')");}
 if(kind==='refresh')work=a.doc.querySelector('#refreshStrategy').onclick();
 if(kind==='open'){a.run("personalLoadedParts.delete('holdings')");work=a.run('openTradeRecord()');}
 if(kind==='feedback')work=a.run("submitStrategyFeedback('executed',{id:'SYNTH_A_CMD',code:'600000',name:'SYNTH_A_PRIVATE',action:'观察'})");
 await a.tick();assert.equal(a.pending.length,1);a.auth({user:{id:'B'}});
 a.pending[0].resolve({data:kind==='open'?{watchlist:[]}:{trades:{records:[]},feedback:{records:[]},analysis:{},recommendations:{records:[]}},error:null});await work;
 assert.equal(a.doc.querySelector('#messageDialog').open,false);assert.equal(a.doc.querySelector('#tradeRecordDialog').open,false);
});
test('same UID token replacement preserves a valid save, but logout-relogin invalidates it',async t=>{
 const a=await setup(t);a.enter('A','SYNTH_A_PRIVATE');const good=a.submit();await a.tick();a.auth({user:{id:'A'},refreshed:true});
 a.pending[0].resolve({data:{inserted:1},error:null});await good;
 assert.match(a.doc.querySelector('#messageText').textContent,/SYNTH_A_PRIVATE/);assert.equal(a.doc.querySelector('#tradeRecordForm button[type=submit]').disabled,false);
 a.enter('A','SYNTH_A_PRIVATE');a.doc.querySelector('#messageDialog').close();const stale=a.submit();await a.tick();a.auth(null);a.auth({user:{id:'A'}});
 a.pending[1].resolve({data:{inserted:1},error:null});await stale;
 assert.equal(a.doc.querySelector('#messageDialog').open,false);
});
