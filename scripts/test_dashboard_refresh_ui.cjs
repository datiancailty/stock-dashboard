const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../assets/app.js'),'utf8');
function context(names,extra={}){
  const c=vm.createContext({console,...extra});
  for(const name of names){
    const start=source.search(new RegExp(`^(?:async )?function ${name}\\(`,'m'));assert.ok(start>=0,`missing ${name}`);
    const rest=source.slice(start);const stop=rest.slice(1).search(/^(?:async )?function |^(?:const|let) /m);
    vm.runInContext(stop<0?rest:rest.slice(0,stop+1),c);
  }return c;
}
test('private market getter includes technical overlays rather than price-only v2',()=>{
  const c=context(['personalRpcForPage']);
  assert.equal(c.personalRpcForPage('grid'),'personal_get_part4_v4');
  assert.equal(c.personalRpcForPage('calendar'),'personal_get_part4_v4');
});
test('forward grid uses complete latest annual plus interim, never old future alone',()=>{
 const c=context(['forwardGridDividend']);
 assert.equal(c.forwardGridDividend({futureDividend:.5,totalDividend:1.38,forwardBasis:{status:'ready',amount:1.38}}),1.38);
});
test('missing basis never falls back to historical sum or casts null to zero',()=>{
 const c=context(['forwardGridDividend']);
 for(const forwardBasis of [undefined,{status:'missing',amount:null},{status:'conflict',amount:2}])
  assert.equal(c.forwardGridDividend({futureDividend:.5,totalDividend:1.38,forwardBasis}),null);
 assert.equal(c.forwardGridDividend({forwardBasis:{status:'ready',amount:0}}),0);
});
test('missing or conflicting forward falls back only to verified current cash basis',()=>{
 const c=context(['forwardGridDividend','forwardGridSource']);
 const s={forwardBasis:{status:'conflict',amount:null},confirmedBasis:{status:'ready',amount:1.25,windowStart:'2025-09-10',windowEnd:'2026-09-10'}};
 assert.equal(c.forwardGridDividend(s),1.25);assert.match(c.forwardGridSource(s),/近12个月已实施/);assert.match(c.forwardGridSource(s),/冲突/);
 s.forwardBasis={status:'ready',amount:1.38};assert.equal(c.forwardGridDividend(s),1.38);
 s.forwardBasis.status='missing';s.confirmedBasis.amount=0;assert.equal(c.forwardGridDividend(s),0);
 s.confirmedBasis.status='missing';assert.equal(c.forwardGridDividend(s),null);
});
test('refresh health separates error, overdue receipt, success and signed out',()=>{
 const c=context(['refreshHealthView']);
 const now=new Date('2026-09-10T12:00:00+08:00');
 assert.equal(c.refreshHealthView(null,false,now).tone,'unknown');
 assert.equal(c.refreshHealthView({status:'ok',targetDate:'2026-09-09',lastSuccessAt:'2026-09-09T19:00:00+08:00',finishedAt:'2026-09-09T19:00:00+08:00'},true,now).tone,'ok');
 assert.equal(c.refreshHealthView({status:'ok',targetDate:'2026-09-04',finishedAt:'2026-09-04T19:00:00+08:00'},true,now).tone,'warning');
 assert.equal(c.refreshHealthView({status:'error',targetDate:'2026-09-09'},true,now).tone,'error');
 assert.equal(c.refreshHealthView({status:'running',startedAt:'2026-09-08T19:00:00+08:00'},true,now).tone,'warning');
});
test('manual forward override is explicit and can be zero',()=>{
 const c=context(['forwardGridDividend']);
 assert.equal(c.forwardGridDividend({forwardBasisOverride:1.1}),1.1);
 assert.equal(c.forwardGridDividend({forwardBasisOverride:0}),0);
 assert.equal(c.forwardGridDividend({forwardBasisOverride:-1}),null);
});
test('basis label distinguishes pending plan and incomplete source',()=>{
 const c=context(['forwardGridSource']);
 assert.match(c.forwardGridSource({forwardBasis:{status:'ready',components:{annual:{status:'implemented'},interim:{status:'announced'}}}}),/待实施/);
 assert.match(c.forwardGridSource({futureDividend:.5}),/待核实/);
});
test('special distributions have explicit non-recurring warning',()=>{
 const c=context(['forwardGridSource']);
 assert.match(c.forwardGridSource({forwardBasis:{status:'ready',components:{interim:{status:'announced',source:{field:{distributionNature:'special'}}}}}}),/含特别分红、非经常性/);
});
test('components retain exact precision and explain genuine unannounced states',()=>{
 const c=context(['formatForwardAmount','forwardGridComponents']);
 const s={forwardBasis:{components:{annual:{year:2025,amount:1.4354904,status:'implemented'},interim:{year:2026,amount:null,status:'missing',reason:'current_interim_no_quantified_plan'}}}};
 assert.match(c.forwardGridComponents(s),/1\.4354904/);
 assert.match(c.forwardGridComponents(s),/尚无明确金额/);
});
test('source links only allow matching issuer public evidence',()=>{
 const c=context(['forwardGridEvidence'],{escapeHtml:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')});
 const s={code:'600000',forwardBasis:{components:{annual:{source:{field:{sourceUrl:'https://data.eastmoney.com/yjfp/detail/600000.html'}}},interim:{source:{official_notice:{sourceUrl:'https://data.eastmoney.com/notices/detail/600000/AN2026082000000001.html',title:'<img src=x onerror=alert(1)>'}}}}}};
 const html=c.forwardGridEvidence(s);assert.match(html,/noopener noreferrer/);assert.ok(!html.includes('<img'));
 s.forwardBasis.components.interim.source.official_notice.sourceUrl='javascript:alert(1)';assert.ok(!c.forwardGridEvidence(s).includes('javascript:'));
 s.forwardBasis.components.annual.source.field.sourceUrl='https://data.eastmoney.com/yjfp/detail/000001.html';assert.equal(c.forwardGridEvidence(s),'');
});
test('structured actual cash links disclose both amount and payment-date evidence',()=>{
 const c=context(['forwardGridEvidence'],{escapeHtml:String});
 const html=c.forwardGridEvidence({code:'600000',forwardBasis:{status:'missing'},confirmedBasis:{status:'ready',components:[{sourceUrl:'https://data.eastmoney.com/yjfp/detail/600000.html',plan:'公开分红表＋F10派息日核对'}]}});
 assert.match(html,/yjfp\/detail\/600000/);assert.match(html,/F10派息日依据/);assert.match(html,/code=SH600000/);
});
test('confirmed zero total is not called an unannounced dividend',()=>{
 const c=context(['forwardGridDividend','gridTargetCell']);
 assert.match(c.gridTargetCell({price:10,forwardBasis:{status:'ready',amount:0}},5),/无现金分红/);
});
test('basis timestamps explicitly use Beijing time independent of device zone',()=>{
 const c=context(['formatBasisTime']);
 assert.match(c.formatBasisTime('2026-09-09T10:00:00+08:00'),/10:00:00.*北京时间/);
 assert.match(c.formatBasisTime('2026-09-09 02:00:00+00'),/10:00:00.*北京时间/);
 assert.equal(c.formatBasisTime('2026-09-09T10:00:00'),'—');
});
test('strategy freshness is based on analysis date, not historic success flag',()=>{
 const c=context(['strategyAnalysisIsCurrent'],{strategyAnalysis:{status:'success',updatedAt:'2026-09-01T10:00:00+08:00'}});
 assert.equal(c.strategyAnalysisIsCurrent(new Date('2026-09-09T10:00:00+08:00')),false);
 c.strategyAnalysis.updatedAt='2026-09-09T09:59:00+08:00';
 assert.equal(c.strategyAnalysisIsCurrent(new Date('2026-09-09T10:00:00+08:00')),true);
 c.strategyAnalysis.updatedAt='2026-09-10T09:59:00+08:00';
 assert.equal(c.strategyAnalysisIsCurrent(new Date('2026-09-09T10:00:00+08:00')),false);
});
test('missing AI output never fabricates fresh buy command or feedback target',()=>{
 const nodes={};const buttons=[{disabled:false,classList:{toggle(){}},setAttribute(){}}];
 const c=context(['renderBriefCommand'],{strategyAnalysis:{},strategyFeedback:[],feedbackPending:false,
  $:s=>nodes[s]??=( {textContent:'',innerHTML:'',className:''}),document:{querySelectorAll:()=>buttons}});
 c.renderBriefCommand([{kind:'buy',holding:{code:'000001',name:'合成样例'},confidence:80}]);
 assert.match(nodes['#briefCommandTitle'].textContent,/等待/);
 assert.equal(buttons[0].disabled,true);
});
test('historical brief command distinguishes unknown confirmed yield from genuine zero',()=>{
 const nodes={};
 const row={code:'600000',name:'合成甲',price:10,annualDividend:1,interimDividend:0,confirmedBasis:{status:'missing',amount:null}};
 const c=context(['stock','renderBriefCommand'],{market:{stocks:[row]},overrides:{},holdings:[],part2Config:{},
  strategyAnalysis:{briefCommand:{id:'synthetic-history',code:'600000',action:'继续观察',reason:'合成历史建议'}},
  strategyFeedback:[],feedbackPending:false,strategyAnalysisIsCurrent:()=>false,
  researchMatchPercent:()=>null,researchMatchBadge:()=>'',money:n=>Number(n).toFixed(2),
  $:s=>nodes[s]??={textContent:'',innerHTML:'',className:''},document:{querySelectorAll:()=>[]}});
 assert.equal(c.stock('600000').totalDividend,null);
 c.renderBriefCommand([]);
 assert.match(nodes['#briefCommandTitle'].textContent,/历史建议/);
 assert.match(nodes['#briefCommandFacts'].innerHTML,/正式股息率 <b>—<\/b>/);
 row.confirmedBasis={status:'ready',amount:0};c.renderBriefCommand([]);
 assert.match(nodes['#briefCommandFacts'].innerHTML,/正式股息率 <b>0\.00%<\/b>/);
 row.confirmedBasis.amount=1;c.renderBriefCommand([]);
 assert.match(nodes['#briefCommandFacts'].innerHTML,/正式股息率 <b>10\.00%<\/b>/);
 row.price=0;c.renderBriefCommand([]);
 assert.match(nodes['#briefCommandFacts'].innerHTML,/正式股息率 <b>—<\/b>/);
});
test('strategy card keeps unknown confirmed yield distinct from genuine zero',()=>{
 const node={innerHTML:''};
 const s={price:10,totalDividend:null};
 const advice=[{s,y:0,holding:{code:'600000',name:'合成甲',shares:0},kind:'wait',action:'等待正式数据',day:'',week:'',month:'',why:''}];
 const c=vm.createContext({advice,$:()=>node,escapeHtml:String});
 const render=source.split('\n').find(line=>line.startsWith("  $('#strategyAdvice').innerHTML="));
 assert.ok(render);
 vm.runInContext(render,c);
 assert.match(node.innerHTML,/当前正式股息率<\/small><b>—<\/b>/);
 s.totalDividend=0;vm.runInContext(render,c);
 assert.match(node.innerHTML,/当前正式股息率<\/small><b>0\.000%<\/b>/);
 s.price=0;vm.runInContext(render,c);
 assert.match(node.innerHTML,/当前正式股息率<\/small><b>—<\/b>/);
});
test('new record snapshot preserves unknown dividend and omits unknown yield',async()=>{
 const nodes={};let saved;
 const s={price:10,totalDividend:null};
 const c=vm.createContext({stock:()=>s,personalStockUniverse:()=>[{code:'600000',name:'合成甲'}],
  $:id=>nodes[id]??={value:'',close(){}},crypto:{randomUUID:()=> 'synthetic'},
  mutateTradeRecords:async update=>{saved=update([])[0];},renderStrategy(){},showMessage(){}});
 const handler=source.split('\n').find(line=>line.startsWith("$('#tradeRecordForm').onsubmit="));
 assert.ok(handler);vm.runInContext(handler,c);
 for(const [id,value] of Object.entries({'#tradeStock':'600000','#tradePrice':'10','#tradeShares':'100','#tradeAction':'买入','#tradeDate':'2026-09-10'}))c.$(id).value=value;
 await nodes['#tradeRecordForm'].onsubmit({preventDefault(){}});
 assert.equal(saved.dividendPerShare,null);
 assert.equal(saved.context.totalDividend,null);
 assert.equal(Object.hasOwn(saved.context,'yield'),false);
 s.totalDividend=0;await nodes['#tradeRecordForm'].onsubmit({preventDefault(){}});
 assert.equal(saved.dividendPerShare,0);assert.equal(saved.context.yield,0);
 s.totalDividend=1;await nodes['#tradeRecordForm'].onsubmit({preventDefault(){}});
 assert.equal(saved.dividendPerShare,1);assert.equal(saved.context.yield,10);
});
test('new CSV record fallback preserves unknown cash without changing supplied history',()=>{
 const s={totalDividend:null};
 const c=context(['parseCsv','csvPick','csvStableId','validateTradeCsv'],{stock:()=>s,catalog:[],market:{stocks:[]},holdings:[]});
 const csv='日期,股票代码,操作,成交价格,成交股数,正式每股分红,历史股息率\n2026-09-10,600000,买入,10,100,';
 assert.equal(c.validateTradeCsv(csv+',')[0].dividendPerShare,null);
 s.totalDividend=0;assert.equal(c.validateTradeCsv(csv+',')[0].dividendPerShare,0);
 s.totalDividend=null;
 const supplied=c.validateTradeCsv(csv+'1.25,12.5')[0];
 assert.equal(supplied.dividendPerShare,1.25);assert.equal(supplied.context.yield,12.5);
});
test('Part2 unknown forward yield stays null, never displays 0 percent',()=>{
 const c=context(['forwardGridDividend','bollYield']);
 assert.equal(c.bollYield({price:10,forwardBasis:{status:'missing',amount:null}}),null);
 assert.equal(c.bollYield({price:10,forwardBasis:{status:'ready',amount:0}}),0);
 assert.equal(c.bollYield({price:0,forwardBasis:{status:'ready',amount:1}}),null);
});
test('Part2 missing basis row renders dash without crashing',()=>{
 const c=context(['forwardGridDividend','forwardGridSource','bollYield','bollRow'],{bollLevels:()=>({sell:[],buy:[]}),bollTrack:()=>'',bollVisual:()=>'',escapeHtml:String,money:n=>Number(n).toFixed(2)});
 const html=c.bollRow({code:'600000',name:'合成甲',price:10,weeklyBoll:{asOf:'2026-08-28'},forwardBasis:{status:'missing',amount:null}});
 assert.match(html,/指标数据：2026-08-28/);
 assert.match(html,/boll-yield"><strong>—<\/strong>/);
});
test('Part5 missing estimates are not numeric zero',()=>{
 const c=context(['newsDividendAmount']);
 for(const value of [null,undefined,'',' ',false,-1,'n/a'])assert.equal(c.newsDividendAmount(value),null);
 assert.equal(c.newsDividendAmount(0),0);
 assert.equal(c.newsDividendAmount('0.50'),0.5);
});
test('Part2 new watchlist symbols share one other group without mutating saved config',()=>{
 const saved={groups:[{name:'合成板块',codes:['600000']}]};const before=JSON.stringify(saved);
 const c=context(['normalizedPart2Groups'],{part2Config:saved,trackedStocks:[{code:'600000'},{code:'600001'},{code:'600002'}]});
 const groups=c.normalizedPart2Groups();
 assert.equal(groups.filter(g=>g.name==='其他').length,1);
 assert.equal(JSON.stringify(saved),before);
 assert.equal(JSON.stringify(c.normalizedPart2Groups()),JSON.stringify(groups));
});
// Offline lifecycle harness: real app functions, controlled RPC promises/timers,
// synthetic sessions only; no browser storage, network, or account credentials.
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
async function flushMicrotasks(){for(let i=0;i<30;i++)await Promise.resolve();}
function lifecycle(rpcOverride){
 const timers=[],intervals=new Map(),events={},calls=[],applied=[];let authCallback,nextTimer=0;
 const client={auth:{onAuthStateChange:callback=>{authCallback=callback;}}};
 const setTimeout=(fn,ms=0)=>{timers.push({fn,ms});return ++nextTimer;};
 const document={visibilityState:'visible',querySelector:()=>({id:'today'}),addEventListener:(name,fn)=>{events[name]=fn;}};
 let c;
 const names=['initSupabaseClient','stopPrivateAutoRefresh','startPrivateAutoRefresh','clearPersonalData','personalRpcForPage','personalCount','loadPrivateDashboard'];
 if(source.includes('function loadPrivateDashboardOnce('))names.push('loadPrivateDashboardOnce');
 c=context(names,{PART0_LOCAL_ONLY_PREVIEW:false,SUPABASE_URL:'https://synthetic.invalid',SUPABASE_ANON_KEY:'synthetic-public-placeholder',
  supabaseClient:client,authSession:{user:{id:'synthetic-owner'},generation:1},privateLoadInFlight:null,privateRefreshTimer:null,
  privateLoadState:'not_loaded',privateLoadError:'',personalMigrationState:null,refreshHealth:null,refreshHealthReadFailed:false,
  currentUsername:'',vpsAdmin:false,privatePortfolio:null,runtimeDisplay:null,whitelistControl:null,holdings:[],
  PRIVATE_DASHBOARD_REFRESH_MS:vm.runInNewContext(source.match(/^const PRIVATE_DASHBOARD_REFRESH_MS=(.+);$/m)[1]),
  setTimeout,queueMicrotask,clearInterval:id=>intervals.delete(id),document,
  window:{setTimeout,setInterval:(fn,ms)=>{const id=++nextTimer;intervals.set(id,{fn,ms});return id;},supabase:{createClient:()=>client}},
  render(){},updateAuthUI(){},loadPersonalPart:async()=>{},
  applyPrivatePortfolio:value=>{applied.push(value);c.privatePortfolio=value;},
  supabaseRpc:async name=>{const session=c.authSession;calls.push({name,session});
   if(rpcOverride){const value=rpcOverride(name,session,calls);if(value!==undefined)return value;}
   if(name==='vps_private_get_portfolio')return {generation:session.generation};
   if(name==='vps_is_admin')return false;
   if(name==='app_get_current_username')return 'synthetic';
   if(name==='personal_get_migration_state')return {source_files:1};
   return {generation:session.generation};
  }});
 c.initSupabaseClient();
 return {c,calls,applied,timers,intervals,events,auth:(event,session)=>authCallback(event,session),
  async runTimers(){const batch=timers.splice(0);for(const timer of batch)timer.fn();await flushMicrotasks();}};
}
test('session object replacement discards old batch and reads latest without waiting for polling',async()=>{
 const gate=deferred();let firstPortfolio=true;
 const h=lifecycle(name=>{if(name==='vps_private_get_portfolio'&&firstPortfolio){firstPortfolio=false;return gate.promise;}});
 const first=h.c.loadPrivateDashboard();
 h.c.authSession={user:{id:'synthetic-owner'},generation:2};
 h.c.loadPrivateDashboard();
 gate.resolve({generation:1});await first;await flushMicrotasks();
 assert.equal(h.applied.length,0,'old session response must be discarded even for the same UID');
 await h.runTimers();
 assert.equal(h.calls.filter(x=>x.name==='vps_private_get_portfolio').length,2,'latest session must receive a catch-up read');
 assert.deepEqual(h.applied,[{generation:2}]);
 assert.equal(h.timers.length,0,'catch-up must be bounded, not another polling loop');
});
test('same session concurrent calls reuse one promise through health and the current Part',async()=>{
 const health=deferred(),part=deferred();const h=lifecycle(name=>name==='personal_get_refresh_health'?health.promise:undefined);
 h.c.document.querySelector=()=>({id:'strategy'});let partReads=0;
 h.c.loadPersonalPart=async(page,force)=>{assert.equal(page,'strategy');assert.equal(force,true);partReads++;await part.promise;};
 const first=h.c.loadPrivateDashboard(),second=h.c.loadPrivateDashboard();
 assert.equal(second,first,'same-session callers share completion, not a resolved busy return');
 await flushMicrotasks();let completed=false;first.then(()=>{completed=true;});
 assert.equal(h.c.loadPrivateDashboard(),first,'health must still own the flight');
 assert.equal(h.calls.filter(x=>x.name==='vps_private_get_portfolio').length,1);
 assert.equal(completed,false);assert.equal(partReads,0);
 health.resolve({status:'ok'});await flushMicrotasks();
 assert.equal(partReads,1);assert.equal(h.c.loadPrivateDashboard(),first,'current Part must still own the flight');
 assert.equal(completed,false);part.resolve();await first;
 assert.equal(completed,true);assert.equal(h.c.privateLoadInFlight,null);assert.equal(h.timers.length,0);
});
test('default holdings read also remains inside the shared flight',async()=>{
 const part=deferred();const h=lifecycle();let partReads=0;
 h.c.loadPersonalPart=async page=>{assert.equal(page,'holdings');partReads++;await part.promise;};
 const first=h.c.loadPrivateDashboard();await flushMicrotasks();
 assert.equal(partReads,1);assert.equal(h.c.loadPrivateDashboard(),first);
 part.resolve();await first;assert.equal(h.c.privateLoadInFlight,null);
});
test('sign out during health clears private state and does not retry or start a Part read',async()=>{
 const health=deferred();const h=lifecycle(name=>name==='personal_get_refresh_health'?health.promise:undefined);let partReads=0;
 h.c.loadPersonalPart=async()=>{partReads++;};h.c.startPrivateAutoRefresh();
 const first=h.c.loadPrivateDashboard();await flushMicrotasks();
 h.auth('SIGNED_OUT',null);health.resolve({status:'ok'});await first;await h.runTimers();
 assert.equal(h.c.authSession,null);assert.equal(h.c.privatePortfolio,null);assert.equal(h.c.personalMigrationState,null);
 assert.equal(h.c.refreshHealth,null);assert.equal(h.c.privateLoadState,'not_loaded');assert.equal(partReads,0);
 assert.equal(h.intervals.size,0);assert.equal(h.timers.length,0);
 assert.equal(h.calls.filter(x=>x.name==='vps_private_get_portfolio').length,1);
});
test('sign out then same UID sign in never accepts the previous session response',async()=>{
 const gate=deferred();const h=lifecycle((name,session)=>name==='vps_private_get_portfolio'&&session.generation===1?gate.promise:undefined);
 const first=h.c.loadPrivateDashboard();h.auth('SIGNED_OUT',null);
 h.auth('SIGNED_IN',{user:{id:'synthetic-owner'},generation:2});await h.runTimers();
 gate.resolve({generation:1});await first;assert.equal(h.applied.length,0);await h.runTimers();
 assert.deepEqual(h.applied,[{generation:2}]);assert.equal(h.c.refreshHealth.generation,2);
});
test('replacement during migration skips stale health and catches up for latest session',async()=>{
 const gate=deferred();const h=lifecycle((name,session)=>name==='personal_get_migration_state'&&session.generation===1?gate.promise:undefined);
 const first=h.c.loadPrivateDashboard();await flushMicrotasks();
 h.c.authSession={user:{id:'synthetic-other-owner'},generation:2};h.c.loadPrivateDashboard();
 gate.resolve({source_files:99});await first;
 assert.equal(h.c.personalMigrationState,null);
 assert.equal(h.calls.filter(x=>x.name==='personal_get_refresh_health').length,0);
 await h.runTimers();assert.equal(h.c.personalMigrationState.source_files,1);assert.equal(h.c.refreshHealth.generation,2);
});
test('hidden session replacement waits for visibility without adding a polling loop',async()=>{
 const gate=deferred();const h=lifecycle((name,session)=>name==='vps_private_get_portfolio'&&session.generation===1?gate.promise:undefined);
 h.c.startPrivateAutoRefresh();const first=h.c.loadPrivateDashboard();
 h.c.document.visibilityState='hidden';h.c.authSession={user:{id:'synthetic-owner'},generation:2};h.c.loadPrivateDashboard();
 gate.resolve({generation:1});await first;
 assert.equal(h.timers.length,0);assert.equal(h.intervals.size,1);
 for(const timer of h.intervals.values())timer.fn();h.events.visibilitychange();await flushMicrotasks();
 assert.equal(h.calls.filter(x=>x.name==='vps_private_get_portfolio').length,1);
 h.c.document.visibilityState='visible';h.events.visibilitychange();await flushMicrotasks();
 assert.deepEqual(h.applied,[{generation:2}]);assert.equal(h.intervals.size,1);
});
test('auth callback schedules reads as a macrotask and retains one visible-only 15-minute poll',async()=>{
 for(const event of ['SIGNED_IN','TOKEN_REFRESHED']){
  const h=lifecycle();const session={user:{id:'synthetic-owner'},generation:2};
  assert.equal(h.auth(event,session),undefined,'callback must not return an awaited RPC promise');
  assert.equal(h.calls.length,0);await flushMicrotasks();
  assert.equal(h.calls.length,0,'RPC must not run in callback or its microtask checkpoint');
  assert.equal(h.timers.length,1);assert.equal(h.timers[0].ms,0);await h.runTimers();
  assert.ok(h.calls.length>0);h.c.startPrivateAutoRefresh();assert.equal(h.intervals.size,1);
  const poll=[...h.intervals.values()][0];assert.equal(poll.ms,15*60*1000);
  const before=h.calls.length;h.c.document.visibilityState='hidden';poll.fn();h.events.visibilitychange();await flushMicrotasks();
  assert.equal(h.calls.length,before);assert.equal(h.intervals.size,1);assert.equal(h.timers.length,0);
  h.c.document.visibilityState='visible';poll.fn();await flushMicrotasks();assert.ok(h.calls.length>before);
 }
});
test('market read and recommendations use new private contracts',()=>{
 assert.ok(/supabaseRpc\('personal_get_part4_v4'\)/.test(source));
 assert.ok(/strategyRecommendationMeta\.performance/.test(source));
 assert.ok(!/strategyAnalysis\.status==='success'\?`\$\{stage\} · 分析已更新`/.test(source));
});
