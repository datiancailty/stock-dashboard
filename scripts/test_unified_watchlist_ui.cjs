const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const ROOT = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(ROOT, 'assets/app.js'), 'utf8');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
async function app(t) {
  const dom = new JSDOM(html, {url:'https://synthetic.invalid/', runScripts:'outside-only', pretendToBeVisual:true});
  const w = dom.window, calls = [];
  w.fetch = async url => {assert.equal(url, 'data/stock-catalog.json'); return {ok:true,json:async()=>[]};};
  w.HTMLDialogElement.prototype.showModal = function(){this.open=true;};
  w.HTMLDialogElement.prototype.close = function(){this.open=false;};
  w.confirm = ()=>true;
  vm.runInContext(source, dom.getInternalVMContext());
  const run = code=>vm.runInContext(code,dom.getInternalVMContext());
  await new Promise(resolve=>setTimeout(resolve,0));
  w.rpc = async(name, args)=>{calls.push({name,args:copy(args)});return {data:{},error:null};};
  run(`authSession={user:{id:'synthetic'}}; supabaseClient={rpc}; personalLoadedParts=new Set(['holdings','positions','grid','calendar','news','strategy']);
    trackedStocks=[{code:'600000',name:'合成甲',shares:100,cost:9},{code:'000001',name:'合成乙'}];
    catalog=[{code:'600000',name:'合成甲',market:'SH'},{code:'000001',name:'合成乙',market:'SZ'},{code:'600001',name:'合成丙',market:'SH',initials:'hcb'}];
    market={stocks:[{code:'600000',name:'合成甲',price:10,weeklyBoll:{asOf:'2026-09-10',lower:8,middle:10,upper:12},positions:{asOf:'2026-09-10'},forwardBasis:{status:'missing',amount:null},confirmedBasis:{status:'ready',amount:1}},{code:'000001',name:'合成乙',price:20}],events:[],updatedAt:'2026-09-10T18:00:00+08:00'};
    marketLoaded=true;`);
  t.after(()=>w.close());
  return {w,run,calls,document:w.document};
}
test('BOLL readiness requires real lower/middle/upper and rejects mid-only',async t=>{
  const {run,document}=await app(t);
  assert.equal(run("watchlistDataStatus('600000',2).tone"),'good');
  run('renderHoldings();');
  assert.match(document.querySelector('[data-watchlist-code="600000"]').textContent,/BOLL已就绪/);
  run('market.stocks[0].weeklyBoll.mid=10;delete market.stocks[0].weeklyBoll.middle;');
  assert.equal(run("watchlistDataStatus('600000',2).tone"),'warning');
});

test('Part1 edits a compact draft without changing any saved Part or VPS',async t=>{
  const {run,document,calls}=await app(t);
  run('renderHoldings();');
  assert.equal(document.querySelector('#holdings h2').textContent,'自选股管理');
  assert.equal(document.querySelector('#summaryCards'),null);
  assert.equal(document.querySelector('#addShares'),null);
  assert.equal(document.querySelector('#addCost'),null);
  assert.equal(document.querySelectorAll('#watchlistBody tr').length,2);
  assert.doesNotMatch(document.querySelector('#holdingList').textContent,/当前持仓|成本|市值/);
  document.querySelector('#stockSearch').value='合成丙';
  await run('addWatchlistItem()');
  assert.equal(run('trackedStocks.length'),2,'draft does not replace saved membership');
  assert.equal(document.querySelectorAll('#watchlistBody tr').length,3);
  assert.match(document.querySelector('#watchlistBody').textContent,/保存后纳入/);
  await assert.rejects(run('addWatchlistItem()'),/选择|清单/);
  await run("deleteWatchlistItem('600000')");
  assert.match(document.querySelector('#watchlistBody').textContent,/待移除/);
  document.querySelector('#cancelChanges').click();
  assert.equal(document.querySelectorAll('#watchlistBody tr').length,2);
  assert.equal(calls.length,0,'draft controls never call a writer or provider');
  assert.equal(document.querySelector('#saveChanges').disabled,true);
});

test('confirmed save writes one personal list, preserves metadata and refreshes Parts 2–6',async t=>{
  const {w,run,document,calls}=await app(t);
  let saved=copy(run('trackedStocks'));
  const snapshot=copy(run('market'));
  w.rpc=async(name,args={})=>{
    calls.push({name,args:copy(args)});
    let data={};
    if(name==='personal_get_part1')data={watchlist:copy(saved)};
    if(name==='personal_replace_watchlist'){saved=copy(args.p_items);data={count:saved.length};}
    if(name==='personal_get_part4_v4')data=snapshot;
    if(name==='personal_get_part2')data={groups:[],extraStocks:[]};
    if(name==='personal_get_part5')data={items:[]};
    return {data,error:null};
  };
  run('supabaseClient.rpc=rpc;personalMigrationState={source_files:1,watchlist_count:2};renderHoldings();');
  document.querySelector('#stockSearch').value='合成丙';await run('addWatchlistItem()');
  document.querySelector('#saveChanges').click();
  assert.ok(document.querySelector('#watchlistSaveDialog')?.open,'saving must require a scoped confirmation');
  assert.equal(calls.length,0);
  await document.querySelector('#confirmWatchlistSave').onclick();
  assert.equal(saved.length,3);
  assert.equal(saved.find(item=>item.code==='600000').shares,100);
  assert.equal(saved.find(item=>item.code==='600000').cost,9);
  assert.equal(calls.filter(item=>item.name==='personal_replace_watchlist').length,1);
  assert.ok(calls.every(item=>!item.name.startsWith('vps_')),'no VPS submission or activation');
  assert.equal(document.querySelector('#saveChanges').disabled,true);
  assert.equal(document.querySelectorAll('#gridBody tr').length,3);
  assert.equal(document.querySelectorAll('#newsStockFilter option').length,4);
  assert.equal(document.querySelectorAll('#tradeStock option').length,3);
  assert.match(document.querySelector('#changeTitle').textContent,/已保存/);
  assert.equal(run('personalMigrationState.watchlist_count'),3);
  assert.doesNotMatch(document.querySelector('#selectedStock').textContent,/已加入草稿/);
  assert.match(document.querySelector('#privateHistoryStrip').textContent,/自选 3/);
});

test('Part0 is monitoring-only and never infers a self-check or indicators from one cycle',async t=>{
  const {run,document}=await app(t);
  run(`privateLoadState='ready';vpsAdmin=true;whitelistControl={desired_revision_no:1,desired_symbols:['600000.SH'],active_revision_no:null,active_symbols:[]};runtimeDisplay={runtime:{health_status:'ok',generated_at:'2026-08-01T15:00:00+08:00',last_strategy_cycle_at:'2026-08-01T15:00:00+08:00',mode:'DRY_RUN'},events:[]};renderTodayBoard();`);
  assert.equal(document.querySelector('#manageWhitelist'),null,'Part0 has no second editor');
  assert.equal(document.querySelector('#whitelistDialog'),null);
  assert.match(document.querySelector('#openPart1FromPart0').textContent,/管理自选股/);
  assert.doesNotMatch(document.querySelector('#part0RuntimeGrid').textContent,/09:00 自检/);
  assert.match(document.querySelector('#part0RuntimeGrid').textContent,/指标计算.*未接入/);
  assert.match(document.querySelector('#part0Health').textContent,/较旧|过期/);
  assert.doesNotMatch(document.querySelector('#part0Health').textContent,/数据链路.*正常/);
  document.querySelector('#openPart1FromPart0').click();
  assert.equal(document.querySelector('.page.active').id,'holdings');
});

test('new symbols remain named in Parts2–6 even before data, with account holdings kept separate',async t=>{
  const {run,document}=await app(t);
  run(`trackedStocks.push({code:'600001',name:'合成丙'});holdings=[{code:'600000',name:'合成甲',shares:100},{code:'600099',name:'账户外股',shares:100}];render();`);
  assert.match(document.querySelector('#gridBody').textContent,/合成丙/);
  const pendingBoll=[...document.querySelectorAll('#bollGroups tbody tr')].find(row=>row.textContent.includes('600001'));
  assert.ok(pendingBoll);
  assert.deepEqual({zeroPrice:pendingBoll.textContent.includes('¥0.00'),hasHoldingBadge:!!pendingBoll.querySelector('.boll-holding')},{zeroPrice:false,hasHoldingBadge:false},'new membership is neither a zero price nor an account holding');
  assert.equal(document.querySelectorAll('#strategyAdvice .strategy-card').length,3);
  assert.match(document.querySelector('#strategyAdvice').textContent,/合成丙/);
  assert.doesNotMatch(document.querySelector('#strategyAdvice').textContent,/账户外股/);
  assert.equal(document.querySelectorAll('#tradeStock option').length,3);
});

test('Part1 follows new cloud membership when clean but preserves an unsaved draft',async t=>{
  const {run,document}=await app(t);
  run('renderHoldings();');
  run(`applyPersonalPart('holdings',{watchlist:[{code:'600000',name:'合成甲'},{code:'600001',name:'合成丙'}]});renderHoldings();`);
  assert.match(document.querySelector('#watchlistBody').textContent,/合成丙/);
  assert.doesNotMatch(document.querySelector('#watchlistBody').textContent,/合成乙/);
  await run("deleteWatchlistItem('600001')");
  run(`applyPersonalPart('holdings',{watchlist:[{code:'600000',name:'合成甲'},{code:'000001',name:'合成乙'}]});renderHoldings();`);
  assert.match(document.querySelector('#watchlistBody').textContent,/待移除/);
  assert.equal(run('watchlistChanges().removed[0].code'),'600001');
});

test('sign-out clears pending draft labels, dialogs and search text',async t=>{
  const {run,document}=await app(t);
  run('renderHoldings();');document.querySelector('#stockSearch').value='合成丙';await run('addWatchlistItem()');
  document.querySelector('#saveChanges').click();document.querySelector('#stockSearch').value='合成私密搜索';
  run('authSession=null;clearPersonalData();render();');
  assert.equal(document.querySelector('#watchlistSaveDialog').open,false);
  assert.equal(document.querySelector('#watchlistSaveDetail').textContent,'');
  assert.equal(document.querySelector('#stockSearch').value,'');
  assert.doesNotMatch(document.querySelector('#holdingList').textContent,/合成甲|合成乙|合成丙/);
});

test('stale cloud list blocks writes, keeps draft and can be safely reread',async t=>{
  const {w,run,document,calls}=await app(t);
  run('renderHoldings();');document.querySelector('#stockSearch').value='合成丙';await run('addWatchlistItem()');
  w.rpc=async(name,args={})=>{calls.push({name,args});return {data:{watchlist:[{code:'600000',name:'他端修改'}]},error:null};};
  run('supabaseClient.rpc=rpc;');document.querySelector('#saveChanges').click();await document.querySelector('#confirmWatchlistSave').onclick();
  assert.equal(calls.filter(x=>x.name==='personal_replace_watchlist').length,0);
  assert.match(document.querySelector('#changeDetail').textContent,/云端清单已变化/);
  assert.equal(run('watchlistChanges().added.length'),1);
  await document.querySelector('#refreshWatchlist').onclick();
  assert.match(document.querySelector('#watchlistBody').textContent,/他端修改/);
  assert.equal(run('watchlistChanges().added.length'),0);
});

test('ambiguous writer response forbids duplicate save and retains a read-only recovery path',async t=>{
  const {w,run,document,calls}=await app(t);let saved=copy(run('trackedStocks'));
  run('renderHoldings();');document.querySelector('#stockSearch').value='合成丙';await run('addWatchlistItem()');
  w.rpc=async(name,args={})=>{calls.push({name,args});if(name==='personal_replace_watchlist'){saved=copy(args.p_items);throw new Error('synthetic response lost');}return {data:{watchlist:saved},error:null};};
  run('supabaseClient.rpc=rpc;');document.querySelector('#saveChanges').click();await document.querySelector('#confirmWatchlistSave').onclick();
  assert.equal(document.querySelector('#saveChanges').disabled,true);
  await run('saveWatchlistDraft()');
  assert.equal(calls.filter(x=>x.name==='personal_replace_watchlist').length,1);
  await document.querySelector('#refreshWatchlist').onclick();
  assert.equal(run('trackedStocks.length'),3);
  assert.equal(run('watchlistUncertain'),false);
});

const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));

test('old-session save finally cannot unlock a new-session pending save',async t=>{
  const {w,run,document,calls}=await app(t);
  const oldWrite=deferred(),newWrite=deferred();let saved=copy(run('trackedStocks')),writes=0;
  w.rpc=async(name,args={})=>{
    calls.push({name,args:copy(args)});
    if(name==='personal_get_part1')return {data:{watchlist:copy(saved)},error:null};
    if(name==='personal_replace_watchlist'){
      const gate=++writes===1?oldWrite:newWrite;await gate.promise;
      if(gate===newWrite)saved=copy(args.p_items);
      return {data:{count:args.p_items.length},error:null};
    }
    return {data:{stocks:[],events:[]},error:null};
  };
  run('supabaseClient.rpc=rpc;renderHoldings();');
  document.querySelector('#stockSearch').value='合成丙';await run('addWatchlistItem()');
  const oldSave=run('saveWatchlistDraft()');await tick();assert.equal(writes,1);
  run("authSession=null;clearPersonalData();authSession={user:{id:'new-synthetic'}};personalLoadedParts=new Set(['holdings']);");
  w.savedRows=copy(saved);run('trackedStocks=savedRows;renderHoldings();');
  document.querySelector('#stockSearch').value='合成丙';await run('addWatchlistItem()');
  const newSave=run('saveWatchlistDraft()');await tick();assert.equal(writes,2);
  oldWrite.resolve();await oldSave;
  assert.equal(run('watchlistSaving'),true,'old finally must not clear the new save lock');
  assert.equal(document.querySelector('#confirmWatchlistSave').disabled,true);
  assert.equal(document.querySelector('#stockSearch').disabled,true);
  newWrite.resolve();await newSave;
  assert.equal(run('watchlistSaving'),false);
  assert.equal(run('trackedStocks.length'),3);
  assert.equal(writes,2);
});

test('refresh serializes pending holdings reads and blocks draft mutations and saves',async t=>{
  const {w,run,document,calls}=await app(t);
  const prior=deferred(),fresh=deferred();let reads=0;
  w.rpc=async(name,args={})=>{
    calls.push({name,args:copy(args)});
    if(name==='personal_get_part1'){reads++;return {data:await (reads===1?prior.promise:fresh.promise),error:null};}
    return {data:{stocks:[],events:[]},error:null};
  };
  run('supabaseClient.rpc=rpc;renderHoldings();');
  document.querySelector('#stockSearch').value='合成丙';await run('addWatchlistItem()');
  const draft=copy(run('watchlistDraft'));
  const loading=run("loadPersonalPart('holdings',true)");
  const refreshing=run('refreshWatchlist()');await tick();
  assert.equal(reads,1,'refresh must await the existing holdings read');
  for(const id of ['stockSearch','showAdd','cancelChanges','saveChanges','confirmWatchlistSave','refreshWatchlist'])assert.equal(document.querySelector('#'+id).disabled,true,id+' locked during read');
  assert.ok([...document.querySelectorAll('[data-delete-watchlist]')].every(b=>b.disabled));
  await run("deleteWatchlistItem('600000')");document.querySelector('#cancelChanges').onclick();
  await assert.rejects(run('addWatchlistItem()'),/等待/);
  run('openWatchlistSave()');await run('saveWatchlistDraft()');await run('refreshWatchlist()');
  assert.equal(document.querySelector('#watchlistSaveDialog').open,false);
  assert.deepEqual(copy(run('watchlistDraft')),draft);
  assert.equal(reads,1);
  prior.resolve({watchlist:[{code:'600000',name:'先前读取'}]});await loading;await tick();
  assert.equal(reads,2);
  await run("loadPersonalPart('holdings',true)");assert.equal(reads,2,'no background read races the refresh');
  fresh.resolve({watchlist:[{code:'600000',name:'最新读取',payload:{audit:'synthetic'}}]});await refreshing;
  assert.equal(run('trackedStocks[0].name'),'最新读取');
  assert.equal(run('watchlistDraft[0].audit'),'synthetic');
  assert.equal(document.querySelector('#stockSearch').disabled,false);
  assert.equal(calls.filter(c=>c.name==='personal_replace_watchlist').length,0);
});

test('save waiting for holdings never starts RPC under a replacement session',async t=>{
  const {w,run,document,calls}=await app(t);const pending=deferred();
  run('renderHoldings();');document.querySelector('#stockSearch').value='合成丙';await run('addWatchlistItem()');
  w.pendingRead=pending.promise;run("personalPartLoads.set('holdings',pendingRead);");
  const saving=run('saveWatchlistDraft()');await tick();
  run("authSession=null;clearPersonalData();authSession={user:{id:'replacement'}};renderHoldings();");
  pending.resolve();await saving;
  assert.equal(calls.length,0,'old operation cannot issue reads or writes using new credentials');
});

test('old holdings completion cannot erase a new-session holdings flight',async t=>{
  const {w,run}=await app(t);const old=deferred(),fresh=deferred();let reads=0;
  w.rpc=async name=>({data:name==='personal_get_part1'?await (++reads===1?old.promise:fresh.promise):{stocks:[],events:[]},error:null});
  run('supabaseClient.rpc=rpc;');const first=run("loadPersonalPart('holdings',true)");
  run("authSession=null;clearPersonalData();authSession={user:{id:'replacement'}};");
  const second=run("loadPersonalPart('holdings',true)");old.resolve({watchlist:[]});await first;
  assert.equal(run("personalPartLoads.has('holdings')"),true,'new read must stay discoverable to refresh/save');
  fresh.resolve({watchlist:[{code:'600000',name:'新会话'}]});await second;
  assert.equal(run('trackedStocks[0].name'),'新会话');
});

test('quarantined forward evidence uses an explicit verified-cash label',async t=>{
  const {run,document}=await app(t);
  assert.match(run("forwardGridSource({forwardBasis:{status:'missing',reason:'public_report_identity_quarantined'},confirmedBasis:{status:'ready',amount:1}})"),/近12个月已实施.*身份待核对/);
  run("refreshHealth={status:'ok',stages:{forward:{status:'ok',category:'fallback_used',published:true,source:'eastmoney_public'}}};renderRefreshHealth();");
  assert.match(document.querySelector('#dashboardRefreshDetails').textContent,/前瞻待核实.*已实施口径/);
});

test('Part0 accepts the actual object-shaped portfolio, not only legacy arrays',async t=>{
  const {run}=await app(t);
  run(`applyPrivatePortfolio({scope_key:'primary',projection_sequence:3,source_generated_at:'2026-09-10T15:00:00+08:00',positions:[{symbol:'600000.SH',held_quantity:100,display_name:'合成甲',market_value:1000}]});`);
  assert.equal(run('privatePortfolio?.projection_sequence'),3);
  assert.equal(run('holdings.length'),1);
  assert.equal(run('holdings[0].code'),'600000');
  run(`applyPrivatePortfolio({scope_key:'primary',projection_sequence:null,source_generated_at:null,positions:[]});`);
  assert.equal(run('privatePortfolio'),null,'an initialized empty envelope is not an observed account');
});
