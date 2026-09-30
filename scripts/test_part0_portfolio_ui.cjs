const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {JSDOM}=require('jsdom');const ROOT=path.resolve(__dirname,'..');
async function app(t){
 const dom=new JSDOM(fs.readFileSync(path.join(ROOT,'index.html'),'utf8'),{url:'https://synthetic.invalid',runScripts:'outside-only',pretendToBeVisual:true});
 dom.window.fetch=async url=>{assert.equal(url,'data/stock-catalog.json');return {ok:true,json:async()=>[]};};
 dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;};
 vm.runInContext(fs.readFileSync(path.join(ROOT,'assets/app.js'),'utf8'),dom.getInternalVMContext());
 await new Promise(r=>setTimeout(r,0));t.after(()=>dom.window.close());
 return {w:dom.window,doc:dom.window.document,run:s=>vm.runInContext(s,dom.getInternalVMContext())};
}
const {fixture}=require('./part0_test_fixture.cjs');
test('A layout renders real projection, sorted positions and distinct expiry/success',async t=>{
 const {w,run,doc}=await app(t);assert.ok(doc.querySelector('#part0Account'),'A holdings-first layout missing');
 w.fixture=fixture();run("authSession={user:{id:'synthetic'}};part0Monitor=fixture;part0MonitorState='ready';part0TradeDate='2026-09-30';privateLoadState='ready';refreshHealth={status:'running',startedAt:new Date().toISOString(),targetDate:part0Day()};catalog=[{code:'600000',name:'合成甲'},{code:'600001',name:'合成乙'}];renderTodayBoard();");
 assert.equal(doc.querySelectorAll('#part0PositionsBody tr').length,2);
 assert.match(doc.querySelector('#part0PositionsBody tr').textContent,/合成乙/);
 assert.doesNotMatch(doc.querySelector('#part0PositionsBody tr').textContent,/0\.00%/);
 assert.match(doc.querySelector('#part0Health').textContent,/到期停用/);
 assert.match(doc.querySelector('#part0RuntimeGrid').textContent,/最近一次成功/);
 assert.match(doc.querySelector('#part0RuntimeGrid').textContent,/正在更新/);
 assert.match(doc.querySelector('#part0ErrorHistory').textContent,/历史失败/);
 assert.equal(doc.querySelectorAll('.tab').length,7);
 assert.equal(doc.querySelectorAll('[data-part0-fill]').length,1);
 doc.querySelector('[data-part0-filter=sell]').click();assert.match(doc.querySelector('#part0TradeRows').textContent,/无卖出成交/);
 assert.match(doc.querySelector('#part0Account').textContent,/15:00:00/,'account timestamp not monitor timestamp');
 run('authSession=null;clearPersonalData();renderTodayBoard()');assert.doesNotMatch(doc.querySelector('#today').textContent,/合成甲|合成乙|10,000/);
 assert.equal(run('part0Monitor'),null);
});
test('monitor rejects malformed executable strings, keeps stale time and isolates failed RPC',async t=>{
 const {w,run,doc}=await app(t);w.fixture=fixture();
 run("authSession={user:{id:'synthetic'}};part0Monitor=fixture;part0MonitorState='ready';part0Monitor.observedAt='2000-01-01T00:00:00Z';renderTodayBoard()");
 assert.match(doc.querySelector('#part0Health').textContent,/较旧/);
 w.bad=fixture();w.bad.trades[0].side='buy\" onclick=\"alert(1)';
 assert.equal(run('validPart0Monitor(bad)'),false,'unsafe enum must be rejected before HTML');
 run("supabaseClient={rpc:async()=>({data:null,error:{code:'PGRST202'}})}");
 await run('loadPart0Monitor(authSession)');assert.equal(run('part0Monitor'),null);assert.equal(run('part0MonitorState'),'error');
 run('renderTodayBoard()');assert.match(doc.querySelector('#part0Health').textContent,/读取失败/);
});
test('late Part0 response cannot repopulate signed-out private DOM',async t=>{
 const {w,run,doc}=await app(t);let finish;
 w.rpc=()=>new Promise(r=>{finish=r;});
 run("authSession={user:{id:'synthetic'}};supabaseClient={rpc};");
 const pending=run('loadPart0Monitor(authSession)');
 run('authSession=null;clearPersonalData();renderTodayBoard()');finish({data:fixture(),error:null});await pending;
 assert.equal(run('part0Monitor'),null);assert.doesNotMatch(doc.querySelector('#part0Account').textContent,/10,000/);
});
test('three daily monitor slots preserve expected gaps but flag missed slots',async t=>{
 const {w,run}=await app(t);w.fixture=fixture();
 run("authSession={user:{id:'synthetic'}};part0Monitor=fixture;part0MonitorState='ready';");
 const cases=[
  ['2026-09-30T09:40:01+08:00','2026-09-30T11:00:00+08:00',false],
  ['2026-09-30T09:40:01+08:00','2026-09-30T11:34:59+08:00',false],
  ['2026-09-30T09:40:01+08:00','2026-09-30T11:35:00+08:00',true],
  ['2026-09-30T11:30:01+08:00','2026-09-30T15:14:59+08:00',false],
  ['2026-09-30T11:30:01+08:00','2026-09-30T15:15:00+08:00',true],
  ['2026-09-30T15:10:01+08:00','2026-10-01T09:44:59+08:00',false],
  ['2026-09-30T15:10:01+08:00','2026-10-01T09:45:00+08:00',true],
  ['2026-10-02T15:10:01+08:00','2026-10-04T20:00:00+08:00',false],
  ['2026-10-02T15:10:01+08:00','2026-10-05T09:44:59+08:00',false],
  ['2026-10-02T15:10:01+08:00','2026-10-05T09:45:00+08:00',true],
  ['2026-12-31T15:10:01+08:00','2027-01-01T09:44:59+08:00',false],
 ];
 for(const [observedAt,now,late] of cases){
  w.observedAt=observedAt;w.now=now;
  const view=run('part0Monitor.observedAt=observedAt;part0ReceiptHealth(new Date(now))');
  assert.equal(view.tone==='warning',late,`${observedAt} -> ${now}`);
 }
 w.now='2026-09-30T11:00:00+08:00';w.observedAt='2026-09-30T11:02:00+08:00';
 assert.match(run('part0Monitor.observedAt=observedAt;part0ReceiptHealth(new Date(now)).label'),/时间异常/);
 const timer=fs.readFileSync(path.join(ROOT,'ops/systemd/stock-dashboard-part0.timer'),'utf8');
 assert.deepEqual(timer.split('\n').filter(s=>s.startsWith('OnCalendar=')),[
  'OnCalendar=Mon..Fri *-*-* 09:40:00 Asia/Shanghai',
  'OnCalendar=Mon..Fri *-*-* 11:30:00 Asia/Shanghai',
  'OnCalendar=Mon..Fri *-*-* 15:10:00 Asia/Shanghai']);
 assert.match(timer,/Persistent=false/);
 assert.match(timer,/Unit=stock-dashboard-part0-collect.service/);
 assert.doesNotMatch(timer,/OnBootSec|OnUnitActiveSec|00\/15/);
});
test('Part0 daily card uses independent health, never stale monitor success',async t=>{
 const {w,run,doc}=await app(t);w.fixture=fixture();
 run("authSession={user:{id:'synthetic'}};part0Monitor=fixture;part0MonitorState='ready';part0Monitor.runtime.dashboard={status:'error',asOf:'2026-09-29T18:10:00+08:00'};");
 run("refreshHealth={status:'ok',targetDate:part0Day(),lastSuccessAt:new Date().toISOString()};renderTodayBoard();renderRefreshHealth();");
 const daily=()=>Array.from(doc.querySelectorAll('#part0RuntimeGrid article')).find(n=>n.querySelector('small').textContent==='Dashboard日更');
 assert.match(daily().textContent,/最近一轮全部完成/);
 assert.doesNotMatch(daily().textContent,/最近一次失败/);
 assert.doesNotMatch(doc.querySelector('#dashboardRefreshDetails').textContent,/本机|不唤醒电脑/);
 run("part0Monitor.runtime.dashboard.status='ok';refreshHealth.status='error';renderTodayBoard();");
 assert.match(daily().textContent,/更新失败/);assert.doesNotMatch(daily().textContent,/成功|全部完成/);
 run("refreshHealthReadFailed=true;renderTodayBoard();");assert.match(daily().textContent,/读取失败/);
 run("refreshHealthReadFailed=false;refreshHealth=null;renderTodayBoard();");assert.match(daily().textContent,/尚无运行回执/);
 run("authSession=null;renderTodayBoard();");assert.match(daily().textContent,/登录后查看/);
});
module.exports={fixture};
