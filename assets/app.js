const PART0_LOCAL_ONLY_PREVIEW=new URLSearchParams(location.search).get('part0-local-preview')==='1';
const SUPABASE_CONFIG=window.STOCK_DASHBOARD_SUPABASE_CONFIG||{};
const SUPABASE_URL=String(SUPABASE_CONFIG.url||'').replace(/\/$/,'');
const SUPABASE_ANON_KEY=String(SUPABASE_CONFIG.anonKey||'');
const SUPABASE_FUNCTIONS_BASE=SUPABASE_URL?`${SUPABASE_URL}/functions/v1`:'';
let supabaseClient=null,authSession=null,currentUsername='',vpsAdmin=false,privatePortfolio=null,runtimeDisplay=null,whitelistControl=null,privateLoadState='not_loaded',privateLoadError='',privateRefreshTimer=null,privateLoadInFlight=null;
let personalMigrationState=null,personalLoadedParts=new Set(),personalPartLoads=new Map(),personalPartErrors=new Map();
const PRIVATE_DASHBOARD_REFRESH_MS=15*60*1000;
let market={stocks:[],events:[],updatedAt:null},marketLoaded=false;
let refreshHealth=null,refreshHealthReadFailed=false;
let newsMemory={items:[],updatedAt:null,lastScanAt:null};
let catalog=[],trackedStocks=[];
let watchlistDraft=null,watchlistBase=null,watchlistSaving=false,watchlistMessage='',watchlistUncertain=false,watchlistOperation=null;
let part2Config={version:1,groups:[],extraStocks:[]};
let bollSettings={buyStep:.5,buyCount:4,sellStep:.5,sellCount:4,lowDev:.5,highDev:.5,yieldDev:.25};
let bollFilter='全部',bollSort='yield',bollCollapsed=new Set();
let holdings=[],overrides={},selectedCandidate=null;
let viewMonth=new Date(),selectedDate=new Date().toISOString().slice(0,10);
let tradeRecords=[],strategyRecommendations=[],strategyAnalysis={status:'waiting',learnedRules:[],advice:[]},strategyProfile={schemaVersion:1,externalLearnedRules:[],personalBehaviorEvidence:{},fixedGuardrails:[],nonExecutablePositionSuggestions:[],conflictsAndGaps:[]},focusedStudy=null,focusedStudyNew=null,strategyApiHealth={status:'unknown',reason:'私有策略记录尚未接入'};
let strategyFeedback=[];
let strategyRecommendationMeta={},strategyDataTimes={};
let feedbackPending=false;
let tradeFilter='all';
const $=s=>document.querySelector(s);
const money=n=>Number.isFinite(+n)?`¥${(+n).toFixed(2)}`:'—';
const costMoney=n=>Number.isFinite(+n)?`¥${(+n).toFixed(3)}`:'—';
const escapeHtml=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const formatTime=v=>v?new Date(String(v).replace(' ','T')).toLocaleString('zh-CN',{hour12:false}):'—';

/*
 * Part 0 renders authenticated, RLS-scoped projections only. No browser-to-VPS,
 * broker, provider or target-submission path exists. The explicitly marked local
 * preview exits before auth, network and storage initialization.
 */
const PART0_PREVIEW_LAYOUT=Object.freeze(Array.from({length:22},(_,index)=>{
  const number=String(index+1).padStart(2,'0');
  const palette=['sample-green','sample-orange','sample-blue'];
  return Object.freeze({displayName:`示例标的 ${number}`,symbol:`DEMO-${number}`,previewState:palette[index%palette.length]});
}));
const PART0_STATUS_PRESENTATION=Object.freeze({
  'sample-green':Object.freeze({label:'样式示例·绿',tone:'hold'}),
  'sample-orange':Object.freeze({label:'样式示例·橙',tone:'watch'}),
  'sample-blue':Object.freeze({label:'样式示例·蓝',tone:'candidate'}),
  pending:Object.freeze({label:'待接入',tone:'pending'})
});
const PART0_LOCAL_PREVIEW=Object.freeze({
  source:'local-ui-preview',
  isLocalPreview:true,
  desiredRevision:Object.freeze({label:'固定布局示例',symbolCount:PART0_PREVIEW_LAYOUT.length,status:'未提交，仅用于本地布局'}),
  activeRevision:Object.freeze({label:'未连接',generation:null,status:'本地预览不读取 VPS 回执'}),
  runtime:Object.freeze({mode:'DRY_RUN',healthStatus:'unknown'}),
  whitelist:PART0_PREVIEW_LAYOUT,
  simPositions:Object.freeze([]),
  events:Object.freeze([
    Object.freeze({time:'本地',message:'Part 0 已载入 22 条固定布局示例',note:'非实际白名单/非当日信号'}),
    Object.freeze({time:'本地',message:'目标白名单与 VPS 激活版本分开显示',note:'不代表策略已生效'}),
    Object.freeze({time:'本地',message:'模拟盘只保留获准摘要边界',note:'未调用账户接口'}),
    Object.freeze({time:'本地',message:'DRY_RUN 仅为固定展示边界',note:'无下单/撤单调用'})
  ])
});
const PART0_SAFE_PUBLIC=Object.freeze({
  source:'safe-public',
  isLocalPreview:false,
  desiredRevision:Object.freeze({label:'私有数据未公开',symbolCount:'—',status:'等待管理员授权'}),
  activeRevision:Object.freeze({label:'私有数据未公开',generation:null,status:'等待受控同步'}),
  runtime:Object.freeze({mode:'DRY_RUN',healthStatus:'unknown'}),
  whitelist:Object.freeze([]),
  simPositions:Object.freeze([]),
  events:Object.freeze([
    Object.freeze({time:'公开',message:'今日看板处于安全空状态',note:'未展示真实 VPS 白名单'}),
    Object.freeze({time:'公开',message:'目标与激活版本等待私有授权后投影',note:'未读取 VPS 状态'}),
    Object.freeze({time:'公开',message:'模拟盘持仓摘要未公开',note:'未读取账户接口'}),
    Object.freeze({time:'公开',message:'DRY_RUN 固定，不展示订单能力',note:'无订单接口'})
  ])
});
let part0PreviewRenderedAt=null;
const part0PreviewRenderedLabel=()=>part0PreviewRenderedAt?part0PreviewRenderedAt.toLocaleString('zh-CN',{hour12:false}):'首次本地渲染';
const part0StateTag=state=>{const view=PART0_STATUS_PRESENTATION[state]||PART0_STATUS_PRESENTATION.pending;return `<span class="state-tag ${view.tone}" title="固定布局的状态样式预览，不是 VPS 当日策略信号"><span>${escapeHtml(view.label)}</span><small>示例</small></span>`;};
function isPrivatePart0Model(model){return model?.source==='private-authenticated';}
function symbolDisplay(symbol,fallback=''){const code=String(symbol||'').slice(0,6),found=catalog.find(item=>item.code===code);return found?.name||fallback||code||'—';}
function privateStatusTag(item){const labels={active:'已激活',continue_hold:'继续观察',buy_candidate:'候选买入',exit_candidate:'退出候选',watch:'观察',data_preparing:'数据准备中',quote_unavailable:'行情待更新',cold_removed:'已移出策略池'};const label=labels[item?.status_key]||'状态待接收';return `<span class="state-tag pending" title="DRY_RUN运行状态投影，不代表已成交或自动下单"><span>${escapeHtml(label)}</span><small>运行态</small></span>`;}
function part0RuntimeState(runtime,now=new Date()){
  const generated=Date.parse(runtime?.generated_at||'');
  if(!Number.isFinite(generated))return {tone:'unknown',label:'等待VPS运行回执',note:'尚未收到有效投影，不代表空仓或服务停止'};
  const age=now.getTime()-generated;
  if(age < -60000)return {tone:'warning',label:'回执时间待核对',note:'投影时间晚于当前时间，不能判为正常'};
  if(age>30*60*1000)return {tone:'warning',label:'VPS回执较旧',note:'投影距今超过30分钟；当前服务状态待核对'};
  if(runtime?.health_status==='ok')return {tone:'good',label:'VPS回执报告正常',note:'仅表示最近投影状态，不代替交易或未来周期验收'};
  if(['error','failed','degraded'].includes(runtime?.health_status))return {tone:'error',label:'VPS回执报告异常',note:'以最近脱敏运行记录为准'};
  return {tone:'unknown',label:'VPS健康状态待核对',note:'已收到投影，但健康状态未确认'};
}
function renderPart0RevisionCards(model){
  const local=model.isLocalPreview,logged=isPrivatePart0Model(model),control=whitelistControl;
  const saved=logged&&personalLoadedParts.has('holdings');
  const desired=logged&&vpsAdmin&&control?control.desired_revision_no:null;
  const active=logged&&vpsAdmin&&control?control.active_revision_no:null;
  const rows=local?[['Part 1 自选清单','本地预览'],['VPS 目标名单','未提交示例'],['VPS 激活回执','未连接']]:[
    ['Part 1 自选清单',saved?`已保存 · ${trackedStocks.length}只`:logged?'等待读取':'登录后查看'],
    ['VPS 旧目标名单',desired?`版本 ${desired} · ${(control.desired_symbols||[]).length}只`:'暂无目标回执'],
    ['VPS 激活回执',active?`版本 ${active} · ${(control.active_symbols||[]).length}只`:'尚未确认激活']
  ];
  $('#part0RevisionCards').innerHTML=rows.map(([label,value])=>`<div><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b></div>`).join('');
}
function renderPart0SimPositions(positions, model){
  const privateModel=isPrivatePart0Model(model);
  if(!positions.length){
    const copy=privateModel
      ?(privatePortfolio?'最近一次私有投影未包含持仓行；以同步时间为准。':'当前登录账户没有可读取的 primary 持仓投影，可能尚未授予 scope membership。')
      :(model.isLocalPreview?'本地布局不读取模拟盘账户，因此这里不是“空仓”结论。未来仅显示获准的股票、持有数量、状态和同步时间。':'公开页面不展示模拟盘账户摘要；获准字段将在私有授权后显示。');
    return `<div class="sim-body"><div class="empty-icon">○</div><div class="sim-empty-title">${privateModel&&privatePortfolio?'当前投影未包含持仓':'尚未接收持仓摘要'}</div><div class="sim-empty-text">${escapeHtml(copy)}</div><div class="sim-detail-empty"><b>当前显示边界</b>${privateModel?'仅显示认证 RPC 允许的股数、成本、现价、市值和浮动盈亏；金额由数据库计算。':model.isLocalPreview?'本地预览不载入受限账户和交易字段或 Provider 原始响应。':'真实账户、订单和 Provider 原始响应不会进入公开页面。'}</div></div>`;
  }
  const valueLabel=(value,kind='money')=>value===null||value===undefined||value===''?'暂无':kind==='cost'?costMoney(value):money(value);
  const rows=positions.map(item=>{
    const symbol=item.symbol||item.code||'';
    const name=item.displayName||item.display_name||symbolDisplay(symbol);
    const held=item.heldQuantity??item.held_quantity;
    const available=item.availableQuantity??item.available_quantity;
    const cost=item.averageCostPerShare??item.average_cost_per_share;
    const price=item.currentUnadjustedPrice??item.current_unadjusted_price;
    const marketValue=item.marketValue??item.market_value;
    const pnl=item.unrealizedPnl??item.unrealized_pnl;
    const pnlPct=item.unrealizedPnlPct??item.unrealized_pnl_pct;
    const state=item.positionState||item.position_state||'—';
    const sync=item.sourceGeneratedAt||item.source_generated_at||model.runtime?.generated_at||'—';
    const status=item.dataStatus||item.data_status||'—';
    const cold=state==='removed_cold';
    const pnlText=`${valueLabel(pnl)}${pnlPct===null||pnlPct===undefined?'':` · ${Number(pnlPct).toFixed(2)}%`}`;
    return `<article class="sim-position-row ${cold?'cold':''}"><div class="sim-position-head"><b>${escapeHtml(name)} · ${escapeHtml(symbol)}</b><span class="position-state ${cold?'cold':''}">${escapeHtml(cold?'已不在策略白名单':state)}</span></div><div class="sim-position-metrics"><span>持有 <b>${Number(held)||0}股</b></span><span>可用 <b>${Number(available)||0}股</b></span><span>成本 <b>${valueLabel(cost,'cost')}</b></span><span>现价 <b>${valueLabel(price)}</b></span><span>市值 <b>${valueLabel(marketValue)}</b></span><span>浮动盈亏 <b>${pnlText}</b></span></div><small>数据状态：${escapeHtml(status)} · 最近同步 ${escapeHtml(formatTime(sync))}</small>${cold?'<em>移出白名单不等于卖出或删除持仓</em>':''}</article>`;
  }).join('');
  return `<div class="sim-body"><div class="sim-position-list">${rows}</div></div>`;
}
function renderPart0Monitor(model){
  const local=model.isLocalPreview,logged=isPrivatePart0Model(model);
  const rows=model.whitelist.map(item=>{
    const data=local?{label:'数据样式示例',tone:'neutral'}:watchlistDataStatus(String(item.symbol).slice(0,6),2);
    return `<tr><td><b>${escapeHtml(item.displayName||symbolDisplay(item.symbol))}</b><small>${escapeHtml(item.symbol)}</small></td><td><span class="unified-pill ${data.tone}">${escapeHtml(data.label)}</span></td><td><span class="unified-pill neutral">${local?'运行态示例':'待逐股运行回执'}</span></td></tr>`;
  }).join('');
  const empty=!logged?'<b>登录后查看今日监控</b><p>真实名单与账户数据不公开。</p>':privateLoadState==='error'?'<b>运行回执读取失败</b><p>请重新读取；当前不能判断实际VPS状态。</p>':'<b>尚未收到已激活名单回执</b><p>Part 1 已保存清单不等于 VPS 已生效。旧目标提交与实际接入在 VPS 修复时核对。</p>';
  $('#part0MonitorGrid').innerHTML=`<article class="unified-monitor-card"><div class="unified-card-head"><div><h3>今日监控</h3><p>以VPS激活回执为准；自选统一在 Part 1 维护</p></div><span class="unified-pill neutral">${local?'本地示例':'只读监控'}</span></div>${rows?`<div class="unified-monitor-table-wrap"><table class="unified-monitor-table"><thead><tr><th>股票 / 代码</th><th>Dashboard 数据</th><th>VPS 运行状态</th></tr></thead><tbody>${rows}</tbody></table></div>`:`<div class="unified-monitor-empty">${empty}</div>`}<div class="unified-card-foot">${logged&&model.activeRevisionNo?`已激活版本 ${escapeHtml(model.activeRevisionNo)}；逐股信号缺少回执时不推断买卖。`:'等待真实激活证据，不用目标名单代替。'}</div></article><article class="unified-monitor-card"><div class="unified-card-head"><div><h3>模拟盘持仓</h3><p>独立账户投影，不与自选清单混同</p></div><span class="unified-pill neutral">只读</span></div>${renderPart0SimPositions(model.simPositions||[],model)}<div class="unified-card-foot"><b>移除自选 ≠ 卖出持仓</b><span>${logged&&privatePortfolio?`最近投影：${formatBasisTime(privatePortfolio.source_generated_at)}`:'未读取不代表空仓；本页面不调用账户或交易接口。'}</span></div></article>`;
}
function currentPart0Model(){
  if(PART0_LOCAL_ONLY_PREVIEW)return PART0_LOCAL_PREVIEW;
  if(!authSession)return PART0_SAFE_PUBLIC;
  const runtime=runtimeDisplay?.runtime||{};
  const activeSymbols=whitelistControl?.active_revision_no&&Array.isArray(whitelistControl.active_symbols)?whitelistControl.active_symbols:[];
  const events=Array.isArray(runtimeDisplay?.events)?runtimeDisplay.events.slice(0,4).map(item=>({time:item.occurred_at||'',message:item.message||'已记录脱敏事件',note:item.event_code||'运行回执',severity:item.severity})):[];
  return {source:'private-authenticated',isLocalPreview:false,isPrivate:true,desiredRevisionNo:whitelistControl?.desired_revision_no||null,activeRevisionNo:whitelistControl?.active_revision_no||null,whitelist:activeSymbols.map(symbol=>({symbol,displayName:symbolDisplay(symbol)})),simPositions:privatePortfolio?.positions||[],runtime,events};
}
function renderPart0Runtime(model){
  const observed=isPrivatePart0Model(model),r=observed?model.runtime||{}:{},health=part0RuntimeState(r);
  const quote=Number.isFinite(Date.parse(r.last_quote_snapshot_at||''));
  const received=Number.isFinite(Date.parse(r.generated_at||''));
  const cards=[
    ['脚本进程','未接入','当前投影不含systemd进程状态'],
    ['行情采集',quote?'收到快照回执':'未接入',quote?formatBasisTime(r.last_quote_snapshot_at):'等待行情采集回执'],
    ['指标计算','未接入','不能用策略周期时间推断指标通过'],
    ['结果回传',received?(health.tone==='warning'?'回执待核对':'已收到'):'未接入',received?formatBasisTime(r.generated_at):'等待有效运行投影'],
    ['执行模式',received&&['DRY_RUN','ARMED','DISABLED'].includes(r.mode)?r.mode:'未确认',received?'最近回执报告的模式':'配置模式不等于实际运行']
  ];
  $('#part0RuntimeGrid').innerHTML=cards.map(([label,value,note])=>`<article class="unified-runtime-card"><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b><span>${escapeHtml(note)}</span></article>`).join('');
}
function renderPart0Log(model){
  const events=Array.isArray(model.events)&&model.source!=='safe-public'?model.events.slice(0,4):[];
  $('#part0RunLog').innerHTML=events.length?events.map(item=>`<div class="unified-log-row ${item.severity==='error'?'error':item.severity==='warn'?'warning':''}"><i></i><time>${escapeHtml(Number.isFinite(Date.parse(item.time))?formatBasisTime(item.time):item.time||'未标注时间')}</time><span>${escapeHtml(String(item.message).slice(0,180))}</span><small>${escapeHtml(String(item.note).slice(0,60))}</small></div>`).join(''):'<p class="muted">尚无可显示的运行回执。没有记录，不代表脚本已停止或正常运行。</p>';
}
function renderTodayBoard(){
  if(!$('#today'))return;
  const model=currentPart0Model(),local=model.isLocalPreview,logged=isPrivatePart0Model(model);
  let state=logged?part0RuntimeState(model.runtime):{tone:'unknown',label:local?'本地监控布局预览':'登录后查看运行回执',note:local?'示例数据，不连接真实系统':'公开链接不显示真实名单或账户'};
  if(logged&&privateLoadState==='error')state={tone:'error',label:'私有回执读取失败',note:'当前不能判断VPS实际状态，请重新读取'};
  if(logged&&privateLoadState==='loading')state={tone:'unknown',label:'正在读取运行回执',note:'只重新读取 Supabase 私有投影'};
  $('#today').dataset.part0Mode=model.source;
  $('#part0Health').dataset.tone=state.tone;
  $('#part0Health').innerHTML=`<i class="unified-health-dot"></i><div><b>${escapeHtml(state.label)}</b><small>${escapeHtml(state.note)}</small></div>`;
  $('#part0PreviewBanner').hidden=!local;
  $('#part0PreviewBanner').textContent=local?'本地 UI 预览：固定合成名单，不代表实时数据，不访问任何私有接口。':'';
  renderPart0RevisionCards(model);renderPart0Monitor(model);renderPart0Runtime(model);renderPart0Log(model);
}
function save(){
  // Private positions and costs are never persisted by the page. They are
  // derived from the authenticated Supabase projection on each load.
}
function importPortfolioFromHash(){
  if(location.hash.startsWith('#portfolio='))history.replaceState(null,'',location.pathname+location.search);
}
function syncTradeRecordsToHoldings(){return;}
function stock(code){const auto=market.stocks.find(s=>s.code===code)||{};const o=overrides[code]||{};const annual=o.annual??auto.annualDividend??0,interim=o.interim??auto.interimDividend??0,future=o.future??auto.futureDividend??0,price=o.price??auto.price??0;return {...auto,...o,code,name:auto.name||trackedStocks.find(h=>h.code===code)?.name||holdings.find(h=>h.code===code)?.name||(part2Config.extraStocks||[]).find(h=>h.code===code)?.name||code,annualDividend:+annual,interimDividend:+interim,futureDividend:+future,price:+price,totalDividend:(o.annual!==undefined||o.interim!==undefined)?(+annual+ +interim):(auto.confirmedBasis?.status==='ready'&&typeof auto.confirmedBasis.amount==='number'&&Number.isFinite(auto.confirmedBasis.amount)?auto.confirmedBasis.amount:null),forwardBasisOverride:o.future??null,manual:!!overrides[code]};}
function forwardGridDividend(s){
  const manual=s?.forwardBasisOverride;
  if(manual!==null&&manual!==undefined)return typeof manual==='number'&&Number.isFinite(manual)&&manual>=0?manual:null;
  const basis=s?.forwardBasis;
  if(basis?.status==='ready'&&typeof basis.amount==='number'&&Number.isFinite(basis.amount)&&basis.amount>=0)return basis.amount;
  const actual=s?.confirmedBasis;
  return actual?.status==='ready'&&typeof actual.amount==='number'&&Number.isFinite(actual.amount)&&actual.amount>=0?actual.amount:null;
}
function forwardGridSource(s){
  if(s?.forwardBasisOverride!==null&&s?.forwardBasisOverride!==undefined)return '手动覆盖 · 前瞻总额';
  const b=s?.forwardBasis;if(b?.status!=='ready'){
    const actual=s?.confirmedBasis;
    if(actual?.status==='ready'&&typeof actual.amount==='number'&&Number.isFinite(actual.amount)&&actual.amount>=0)return `近12个月已实施 · ${b?.reason==='public_report_identity_quarantined'?'前瞻报告身份待核对，未采用':b?.reason==='current_interim_cny_unconfirmed'?'最新中期人民币金额待确认':b?.status==='conflict'?'前瞻证据冲突，未采用':'尚无可靠前瞻'}`;
    return '分红口径待核实 · 前瞻与已确认分红均不可用';
  }
  const special=Object.values(b.components||{}).some(c=>c?.source?.field?.distributionNature==='special');
  const stage=Object.values(b.components||{}).some(c=>c?.status==='announced')?'含已公告待实施':'已实施／明确不分配';
  return `最新年度＋最新中期 · ${stage}${special?' · 含特别分红、非经常性':''}`;
}
function refreshHealthView(health,logged,now=new Date()){
  if(!logged)return {tone:'unknown',title:'登录后查看',note:'运行状态仅向当前账号开放。'};
  if(!health||health.status==='missing')return {tone:'warning',title:'尚无运行回执',note:'采集器尚未上报；不能视为自动更新正常。'};
  if(health.status==='error'||health.status==='partial')return {tone:'error',title:health.status==='partial'?'部分更新失败':'更新失败',note:'已保留各阶段最近成功数据，展开查看失败环节。'};
  if(health.status==='running')return (now-new Date(health.startedAt)>2*60*60*1000)?{tone:'warning',title:'任务回执超时',note:'上次任务没有完成回执；可能进程中断或网络异常。'}:{tone:'running',title:'正在更新',note:'六个阶段分别执行；页面按钮只读取状态，不触发采集。'};
  const local=new Date(now.getTime()+8*60*60*1000);
  if(local.getUTCHours()*60+local.getUTCMinutes()<18*60+5)local.setUTCDate(local.getUTCDate()-1);
  while(local.getUTCDay()===0||local.getUTCDay()===6)local.setUTCDate(local.getUTCDate()-1);
  const expected=local.toISOString().slice(0,10);
  if(typeof health.targetDate!=='string'||health.targetDate<expected)return {tone:'warning',title:'等待新的运行回执',note:'已到下一更新周期。电脑未开启、离线或上报失败都可能造成未收到回执，仅网页不能进一步区分。'};
  if(health.status==='ok'&&health.lastSuccessAt)return {tone:'ok',title:'最近一轮全部完成',note:'工作日北京时间18:05；开机或错过计划后补采一次最新数据，不唤醒电脑。'};
  return {tone:'warning',title:'状态待核实',note:'未取得完整成功回执。'};
}
function renderRefreshHealth(){
  const box=$('#dashboardRefreshHealth');if(!box)return;
  const view=refreshHealthReadFailed&&authSession?{tone:'error',title:'运行状态读取失败',note:'当前浏览器未能读取 Supabase；不能据此断言本机采集器已停止。'}:refreshHealthView(refreshHealth,!!authSession);
  box.dataset.state=view.tone;$('#dashboardRefreshSummary').textContent=`Part 1—6 自动更新 · ${view.title}`;
  const detail=$('#dashboardRefreshDetails');if(!authSession){detail.textContent=view.note;return;}
  const labels={notices:'分红日历',quotes:'行情价格',technical:'BOLL／位置指标',news:'公告增量',forward:'前瞻／实际分红',recommendations:'历史建议观察'};
  const states={pending:'等待执行',running:'运行中',ok:'已完成',error:'失败',skipped:'跳过'};
  const reasons={pending:'尚未开始',running:'处理中',complete:'已写入并核对',fallback_used:'主源失败，备用源已完成',rate_limited:'供应商限流／额度限制',timeout:'请求超时',empty_response:'供应商返回空响应',auth_failed:'凭证或登录失效',provider_failed:'供应商接口／网络异常',invalid_data:'数据缺失、过期或校验不通过',missing_contract:'数据库接口未就绪',write_failed:'私有数据写入未确认',readback_failed:'写入后的读回不一致',unknown_error:'任务异常，需查看本机脱敏日志',dependency_failed:'依赖阶段未通过'};
  const sources={hithink_snapshot:'HiThink',eastmoney_snapshot:'公开行情备用源',hithink_daily:'HiThink日线',eastmoney_public:'公开表／正式公告',legacy_public_daily:'公开历史日线',public_company_notice_index:'公司公告索引'};
  const rows=Object.entries(labels).map(([key,label])=>{const item=refreshHealth?.stages?.[key];return `<div class="refresh-stage" data-status="${escapeHtml(states[item?.status]?item.status:'pending')}"><b>${label}</b><strong>${states[item?.status]||'未取得回执'}</strong><small>${key==='forward'&&item?.category==='fallback_used'?'前瞻待核实，采用已核实的已实施口径':reasons[item?.category]||'状态未上报'}${sources[item?.source]?` · ${sources[item.source]}`:''}</small></div>`;}).join('');
  detail.innerHTML=`<p>${escapeHtml(view.note)}</p><div class="refresh-times"><span>最近完整成功：<b>${escapeHtml(formatBasisTime(refreshHealth?.lastSuccessAt))}</b></span><span>最近尝试：${escapeHtml(formatBasisTime(refreshHealth?.startedAt))}</span><span>执行版本：${escapeHtml(refreshHealth?.sourceRelease||'未上报')}</span></div><div class="refresh-stages">${rows}</div><p class="refresh-boundary">此处为本机数据更新，不是 Part 0 的 VPS 交易状态。AI 画像／策略学习暂停，不影响上述普通采集。页面可见时每15分钟读回；关闭页面不轮询。离线期间无法上报新的具体错误。</p>`;
}
function formatBasisTime(value){
  const raw=String(value||'').replace(' ','T').replace(/([+-]\d{2})$/,'$1:00');
  if(!/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(raw))return '—';
  const d=new Date(raw);
  return Number.isFinite(d.getTime())?d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})+' 北京时间':'—';
}
function formatForwardAmount(value){
  return typeof value==='number'&&Number.isFinite(value)?value.toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:12}):'—';
}
function forwardGridComponents(s){
  const b=s?.forwardBasis;
  if(b?.status!=='ready'&&s?.confirmedBasis?.status==='ready'){const actual=s.confirmedBasis;return `实际派息窗口：${actual.windowStart||'—'}（不含）至 ${actual.windowEnd||'—'} · 人民币税前 ${formatForwardAmount(actual.amount)}元/股`;}
  if(!b)return '待采集最新年度与中期分红';
  return ['annual','interim'].map(kind=>{const c=b.components?.[kind],label=kind==='annual'?'年度':'中期';
    if(!c||c.amount===null||c.amount===undefined)return `${c?.year??''}${label}：${/no_quantified|not_quantified|no_.*plan/.test(c?.reason||'')?'尚无明确金额':'待核实'}`;
    return `${c.year??'—'}${label} ${formatForwardAmount(c.amount)}（${c.status==='announced'?'已公告待实施':c.status==='implemented'?'已实施':c.status==='no_distribution'?'明确不分配':'待核实'}）`;
  }).join(' + ');
}
function forwardGridEvidence(s){
  if(!/^\d{6}$/.test(s?.code||''))return '';
  const allowed=new RegExp(`^https://data\\.eastmoney\\.com/(?:yjfp/detail/${s.code}\\.html|notices/detail/${s.code}/AN\\d{12,32}\\.html)$`);
  const links=[];const seen=new Set();
  for(const [kind,c] of Object.entries(s?.forwardBasis?.components||{})){
    const src=c?.source,notice=src?.official_notice,label=kind==='annual'?'年度':'中期';
    for(const [url,title] of [[src?.field?.sourceUrl,`${label}分红表`],[notice?.sourceUrl,`${label}公告依据`],[notice?.relatedNotice?.sourceUrl,'特别分红公告']]){
      if(typeof url==='string'&&allowed.test(url)&&!seen.has(url)){seen.add(url);links.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>`);}
    }
  }
  if(s?.forwardBasis?.status!=='ready')for(const c of s?.confirmedBasis?.components||[]){const url=c?.sourceUrl;if(typeof url==='string'&&allowed.test(url)&&!seen.has(url)){seen.add(url);links.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">已实施分红依据</a>`);}}
  if(s?.forwardBasis?.status!=='ready'&&(s?.confirmedBasis?.components||[]).some(c=>String(c.plan||'').includes('F10派息日核对'))){const exchange=s.code.startsWith('6')?'SH':/^[03]/.test(s.code)?'SZ':'BJ';const url=`https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?type=web&code=${exchange}${s.code}`;links.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">F10派息日依据</a>`);}
  return links.length?`<div class="basis-evidence">${links.join(' · ')}</div>`:'';
}

function sortedHoldingsByMarketValue(){return [...holdings].sort((a,b)=>{const aValue=Number(a.marketValue??a.privatePosition?.market_value??0),bValue=Number(b.marketValue??b.privatePosition?.market_value??0);return bValue-aValue||a.code.localeCompare(b.code);});}
function showMessage(title,text){$('#messageTitle').textContent=title;$('#messageText').textContent=text;$('#messageDialog').showModal();}
function switchTab(id){const personalPart=!!personalRpcForPage(id);document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===id));document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id===id));if(id==='today')renderTodayBoard();if(id==='holdings')renderHoldings();if(id==='positions')renderPositions();if(id==='grid')renderGrid();if(id==='calendar')renderCalendar();if(id==='news')renderNews();if(id==='strategy')renderStrategy();if(personalPart){if(authSession)void loadPersonalPart(id);else openLogin();}}
function guardPart0PreviewControls(){if(!PART0_LOCAL_ONLY_PREVIEW)return;const allowed=new Set(['reloadPart0Preview','openPart1FromPart0']);document.querySelectorAll('button,input,select').forEach(control=>{if(control.classList.contains('tab')||allowed.has(control.id)||control.closest('#messageDialog'))return;control.disabled=true;control.title='严格本地预览不会执行网页数据操作';});}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
$('#reloadPart0Preview').onclick=()=>{if(!PART0_LOCAL_ONLY_PREVIEW)return;part0PreviewRenderedAt=new Date();renderTodayBoard();showMessage('本地预览已重新载入','本次只重新渲染浏览器内存中的 Part 0 界面，没有连接 Supabase、VPS、行情、模拟盘或订单接口。');};
$('#openPart1FromPart0').onclick=()=>{if(PART0_LOCAL_ONLY_PREVIEW){showMessage('严格本地预览模式','本地模式只验证 Part 0，不载入 Part 1 数据；返回独立预览地址即可继续验证今日看板。');return;}switchTab('holdings');};

function render(){renderRefreshHealth();renderTodayBoard();renderHoldings();renderPositions();renderGrid();renderCalendar();renderNews();renderStrategy();const d=market.updatedAt?new Date(market.updatedAt):null;$('#updateText').textContent=d?`私有行情快照采集 · ${d.toLocaleString('zh-CN',{hour12:false})}`:'私有快照 · 登录后读取';}
function watchlistPayload(items){
  return items.map(item=>({...((item.payload&&typeof item.payload==='object')?item.payload:{}),...item,code:String(item.code),name:String(item.name||item.code)}));
}
function watchlistChanges(){
  const base=watchlistBase||[],draft=watchlistDraft||[];
  return {added:draft.filter(item=>!base.some(saved=>saved.code===item.code)),removed:base.filter(item=>!draft.some(row=>row.code===item.code))};
}
function resetWatchlistDraft(){
  watchlistBase=watchlistPayload(trackedStocks);
  watchlistDraft=watchlistPayload(trackedStocks);
  const hint=$('#selectedStock');if(hint)hint.textContent='支持中文、代码、拼音；选择后加入草稿';
  watchlistMessage='';
  selectedCandidate=null;
}
function watchlistVpsStatus(code){
  if(!authSession)return {label:'登录后查看',tone:'neutral'};
  if(privateLoadState==='error')return {label:'回执读取失败',tone:'warning'};
  if(!vpsAdmin||!whitelistControl)return {label:'待VPS接入',tone:'neutral'};
  if(whitelistControl.active_revision_no&&(whitelistControl.active_symbols||[]).some(symbol=>symbol.slice(0,6)===code))return {label:'回执已激活',tone:'good'};
  if((whitelistControl.desired_symbols||[]).some(symbol=>symbol.slice(0,6)===code))return {label:'旧目标未激活',tone:'warning'};
  return {label:'待VPS接入',tone:'neutral'};
}
function watchlistDataStatus(code,part){
  const item=market.stocks.find(row=>row.code===code);
  if(!item)return {label:'待数据更新',tone:'warning'};
  if(part===2){
    const b=item.weeklyBoll;
    return b&&b.asOf&&['lower','middle','upper'].every(key=>Number.isFinite(b[key]))?{label:'BOLL已就绪',tone:'good',note:b.asOf}:{label:'待BOLL更新',tone:'warning'};
  }
  const amount=forwardGridDividend(stock(code));
  return amount!==null?{label:forwardGridSource(stock(code)).includes('近12个月')?'已实施口径':'前瞻口径',tone:'good',note:amount===0?'已确认无现金分红':'依据见 Part 3'}:{label:'分红待核实',tone:'warning'};
}
function renderHoldings(){
  const historyState=privateHistoryStatusHtml('holdings','Part 1 自选股');
  const ready=!!authSession&&personalLoadedParts.has('holdings');
  const busy=!!watchlistOperation;
  $('#stockSearch').disabled=!ready||busy;
  $('#showAdd').disabled=!ready||busy;
  $('#refreshWatchlist').disabled=!authSession||busy;
  $('#confirmWatchlistSave').disabled=!ready||busy||watchlistUncertain;
  if(busy)$('#suggestions').classList.add('hidden');
  $('#cancelChanges').disabled=true;
  $('#saveChanges').disabled=true;
  if(!ready){
    $('#holdingList').innerHTML=historyState||'<p class="muted">正在读取当前账号的自选清单…</p>';
    $('#changeTitle').textContent=authSession?'自选清单等待读取':'登录后管理自选股';
    $('#changeDetail').textContent='草稿仅保存在当前页面；未经确认不写入云端。';
    $('#watchlistSync').textContent='Part 2—6 按已保存清单显示；VPS 接入独立验收。';
    return;
  }
  if(watchlistDraft===null||watchlistBase===null)resetWatchlistDraft();
  const {added,removed}=watchlistChanges(),dirty=!!(added.length||removed.length);
  const rowItems=[...watchlistBase,...added].sort((a,b)=>a.code.localeCompare(b.code));
  const pill=(state,part)=>`<span class="unified-pill ${state.tone}">${escapeHtml(state.label)}</span>${state.note?`<small class="unified-note">${escapeHtml(state.note)}</small>`:''}`;
  const rows=rowItems.map(item=>{
    const isAdded=added.some(row=>row.code===item.code),isRemoved=removed.some(row=>row.code===item.code);
    const staged=isAdded?{label:'保存后纳入',tone:'warning'}:isRemoved?{label:'保存后移除',tone:'neutral'}:null;
    const vps=watchlistVpsStatus(item.code),marketCode=catalog.find(row=>row.code===item.code)?.market;
    return `<tr data-watchlist-code="${escapeHtml(item.code)}" class="${isAdded?'added':isRemoved?'removed':''}"><td><div class="unified-stock"><span class="unified-avatar">${escapeHtml(item.name.slice(0,1))}</span><span><b>${escapeHtml(item.name)}${isAdded?'<em>新增</em>':isRemoved?'<em>待移除</em>':''}</b><small>${escapeHtml(item.code)}${marketCode?`.${escapeHtml(marketCode)}`:''}</small></span></div></td><td data-label="Part 2">${pill(staged||watchlistDataStatus(item.code,2))}</td><td data-label="Part 3">${pill(staged||watchlistDataStatus(item.code,3))}</td><td data-label="VPS">${pill(vps)}</td><td><button type="button" class="unified-row-op" data-delete-watchlist="${escapeHtml(item.code)}" ${busy?'disabled':''}>${isRemoved?'撤销':'移除'}</button></td></tr>`;
  }).join('');
  $('#holdingList').innerHTML=`<div class="unified-table-wrap"><table class="unified-table"><thead><tr><th>自选股票</th><th>Part 2 · 周BOLL</th><th>Part 3 · 股息网格</th><th>VPS 白名单</th><th>操作</th></tr></thead><tbody id="watchlistBody">${rows||'<tr><td colspan="5">当前清单为空，请搜索添加股票。</td></tr>'}</tbody></table></div>`;
  $('#changeTitle').textContent=watchlistSaving?'正在保存并核对…':busy?'正在读取清单…':dirty?'有未保存变更':'当前清单已保存';
  $('#changeDetail').textContent=watchlistMessage||[added.length?'新增：'+added.map(x=>x.name).join('、'):'',removed.length?'移除：'+removed.map(x=>x.name).join('、'):''].filter(Boolean).join('；')||'可继续搜索添加，或移除不再关注的股票。';
  $('#cancelChanges').disabled=!dirty||busy;
  $('#saveChanges').disabled=!dirty||busy||watchlistUncertain;
  $('#watchlistSync').innerHTML='<div><b>自选清单 · 私有保存</b><small>Part 1 是 Dashboard 唯一名单入口</small></div><div><b>Part 2—6 · 按已保存名单关联</b><small>名单关联不代表新数据已就绪</small></div><div><b>VPS · 待独立接入</b><small>本次不提交目标，不改变实际激活名单</small></div>';
  document.querySelectorAll('[data-delete-watchlist]').forEach(button=>button.onclick=()=>deleteWatchlistItem(button.dataset.deleteWatchlist));
}
function openHoldingEdit(){showMessage('持仓为只读','持仓、成本、现价和盈亏只从 VPS→Supabase 私有投影读取，网页不提供本地覆盖或交易写入。');}
$('#holdingForm').onsubmit=e=>{e.preventDefault();openHoldingEdit();};
$('#cancelHolding').onclick=()=>$('#holdingDialog').close();
function zoneClass(zone){return zone==='上部'?'high':zone==='中部'?'mid':zone==='下部'?'low':'';}
function positionCell(item){if(!item)return '<span class="muted">待行情更新</span>';const cls=zoneClass(item.zone);return `<div class="position-zone" title="区间 ${money(item.low)} ～ ${money(item.high)}"><span class="position-meter ${cls}"><i></i><i></i><i></i></span><span class="position-copy"><b class="${cls}">${escapeHtml(item.zone)}</b><small>位置 ${Number(item.percent).toFixed(1)}%</small></span></div>`;}
function overallPosition(positions){const items=['day','week','month'].map(k=>positions?.[k]).filter(Boolean);if(!items.length)return null;const percent=items.reduce((sum,item)=>sum+Number(item.percent||0),0)/items.length;return percent<100/3?'下部':percent<200/3?'中部':'上部';}
function positionObservation(positions){if(!positions?.day||!positions?.week||!positions?.month)return ['待更新','等待周期行情数据'];const d=positions.day.zone,w=positions.week.zone,m=positions.month.zone;if(d===w&&w===m)return [`三周期${d}`,`日、周、月均处于${d}`];if(w===m)return [`周月共振${w}`,`日线${d}，中长期${w}`];if(d==='上部'&&m==='下部')return ['短强长弱','日线上部，月线仍偏低'];if(d==='下部'&&m==='上部')return ['短弱长强','日线回落，月线仍偏高'];return [`日${d[0]} · 周${w[0]} · 月${m[0]}`,'三个周期位置存在差异'];}
function renderPositions(){renderBollGrid();}
function gridTargetCell(s,rate){const dividend=forwardGridDividend(s),price=Number(s.price);if(dividend===0)return '<td class="grid-target"><span class="muted">已确认无现金分红</span></td>';if(!(dividend>0)&&!(price>0))return '<td class="grid-target"><span class="muted">待分红数据</span></td>';if(!(dividend>0))return '<td class="grid-target"><span class="muted">待公告分红</span></td>';const target=dividend/(rate/100),delta=price>0?(target/price-1)*100:null;const note=delta===null?'当前价待更新':Math.abs(delta)<0.01?'已达':`${delta>=0?'需涨':'需跌'} ${Math.abs(delta).toFixed(1)}%`;return `<td class="grid-target"><strong>${money(target)}</strong><small>${escapeHtml(note)}</small></td>`;}
function renderGrid(){
  const historyState=privateHistoryStatusHtml('grid','Part 3 股息率价格网格');
  if(historyState){$('#gridSummary').innerHTML='';$('#gridBody').innerHTML=`<tr><td colspan="9">${historyState}</td></tr>`;return;}
  const universe=personalStockUniverse();
  const items=universe.map(item=>{const s=stock(item.code);return {...item,...s};});
  const validItems=items.filter(item=>Number(item.price)>0);
  const basisReady=items.filter(item=>forwardGridDividend(item)!==null);
  $('#gridSummary').innerHTML=[['自选股票',`${items.length}只`],['行情覆盖',`${validItems.length}/${items.length}只`],['可计算分红覆盖',`${basisReady.length}/${items.length}只`],['行情采集时间',market.updatedAt?formatBasisTime(market.updatedAt):'待读取']].map(([title,value])=>`<div class="summary-card"><small>${escapeHtml(title)}</small><strong>${escapeHtml(value)}</strong></div>`).join('');
  $('#gridBody').innerHTML=items.length?items.map(s=>{const formalParts=[];if(Number(s.annualDividend)>0)formalParts.push(`年报 ${Number(s.annualDividend).toFixed(4)}`);if(Number(s.interimDividend)>0)formalParts.push(`中报 ${Number(s.interimDividend).toFixed(4)}`);const gridDividend=forwardGridDividend(s),currentYield=Number(s.price)>0&&gridDividend>0?gridDividend/Number(s.price)*100:null;return `<tr><td><b>${escapeHtml(s.name||s.code)}</b><div class="stock-code">${escapeHtml(s.code)}</div></td><td><span>${escapeHtml(forwardGridComponents(s))}</span><small class="source-pill">${escapeHtml(forwardGridSource(s))}</small>${Object.values(s.forwardBasis?.components||{}).some(c=>c?.source?.field?.distributionNature==='special')?'<strong class="basis-risk-badge">含特别分红、非经常性</strong>':''}${forwardGridEvidence(s)}<div class="stock-code">${escapeHtml(s.forwardBasis?.asOf?`口径采集 ${formatBasisTime(s.forwardBasis.asOf)}`:'未完成口径采集')}</div></td><td><b>${gridDividend!==null?formatForwardAmount(gridDividend):'—'}</b><div class="stock-code">元/股 · 计算口径见左侧</div></td><td><b>${Number(s.price)>0?money(s.price):'—'}</b><div class="current-yield">${currentYield===null?'—':`${currentYield.toFixed(3)}%`}</div></td>${[5,5.5,6,6.5,7].map(rate=>gridTargetCell(s,rate)).join('')}</tr>`;}).join(''):'<tr><td colspan="9" class="muted">当前账号没有私有自选股票。</td></tr>';
}
function normalizedPart2Groups(){
  const groups=(Array.isArray(part2Config.groups)?part2Config.groups:[]).map(g=>({...g,codes:[...(g.codes||[])]}));
  const assigned=new Set(groups.flatMap(g=>g.codes));let other=groups.find(g=>g.name==='其他');
  for(const h of trackedStocks)if(!assigned.has(h.code)){
    if(!other){other={name:'其他',hydropower:false,codes:[]};groups.push(other);}
    other.codes.push(h.code);assigned.add(h.code);
  }
  return groups;
}
function bollItems(){const heldCodes=new Set(holdings.filter(h=>Number(h.shares)>0).map(h=>h.code)),all=new Map();for(const item of trackedStocks)all.set(item.code,{code:item.code,name:item.name,watchlist:true});const groupBy=new Map();for(const group of normalizedPart2Groups())for(const code of group.codes||[])if(!groupBy.has(code))groupBy.set(code,group);return [...all.values()].map(item=>{const s=stock(item.code),group=groupBy.get(item.code)||{name:'其他',hydropower:false};return {...item,...s,group:group.name,hydropower:!!group.hydropower,holding:heldCodes.has(item.code)};});}
function bollYield(s){const dividend=forwardGridDividend(s),price=Number(s.price);return dividend!==null&&Number.isFinite(price)&&price>0?dividend/price*100:null;}
function bollLevels(s){const buyStart=s.hydropower?4:5,sellStart=s.hydropower?3:4;return {sell:Array.from({length:bollSettings.sellCount},(_,i)=>sellStart-i*bollSettings.sellStep).filter(x=>x>0).reverse(),buy:Array.from({length:bollSettings.buyCount},(_,i)=>buyStart+i*bollSettings.buyStep)};}
function bollSignal(s,type){const y=bollYield(s),b=s.weeklyBoll,buy=(s.hydropower?4:5)-bollSettings.yieldDev,sell=(s.hydropower?3:4)+bollSettings.yieldDev;if(type==='持仓')return s.holding;if(type==='买点下轨')return !!b&&y>=buy&&s.price<=b.lower*(1+bollSettings.lowDev/100);if(type==='卖点上轨')return forwardGridDividend(s)!==null&&!!b&&y<=sell&&s.price>=b.upper*(1-bollSettings.highDev/100);if(type==='近下轨')return !!b&&Math.abs((s.price-b.lower)/b.lower*100)<=bollSettings.lowDev;if(type==='近上轨')return !!b&&Math.abs((s.price-b.upper)/b.upper*100)<=bollSettings.highDev;return type==='全部'||s.group===type;}
function bollTrack(value,current,label){if(!value)return '<span class="muted">待更新</span>';const delta=(value/current-1)*100;return `<strong>${money(value)}</strong><small class="${delta>=0?'boll-up':'boll-down'}">${delta>=0?'+':''}${delta.toFixed(2)}%</small>`;}
function bollTargetCell(s,y,type,index){const dividend=forwardGridDividend(s);if(dividend===0)return `<td class="boll-target ${type}"><div class="boll-target-box"><strong>—</strong><small>已确认无现金分红</small></div></td>`;if(!dividend||!s.price)return `<td class="boll-target ${type}"><div class="boll-target-box"><strong>—</strong><small>待公告分红</small></div></td>`;const target=dividend/(y/100),currentY=bollYield(s),reached=type==='buy'?currentY>=y:currentY<=y,delta=(target/s.price-1)*100,level=Math.min(index,3);return `<td class="boll-target ${type} ${reached?'reached':''} level-${level}"><div class="boll-target-box"><strong>${money(target)}</strong><small>${reached?'已达':`${delta>=0?'需涨':'需跌'} ${Math.abs(delta).toFixed(1)}%`}</small></div></td>`;}
function bollVisual(s){
  const b=s.weeklyBoll||{};
  const price=Number(s.price),lower=Number(b.lower),middle=Number(b.middle),upper=Number(b.upper);
  if(!price||!lower||!middle||!upper||upper<=lower)return '<td class="boll-visual-cell"><span class="muted">待BOLL更新</span></td>';
  const clamp=n=>Math.max(0,Math.min(100,n));
  const position=clamp((price-lower)/(upper-lower)*100);
  const middlePosition=clamp((middle-lower)/(upper-lower)*100);
  const markerX=Math.max(6,Math.min(230,position/100*236));
  const middleX=middlePosition/100*236;
  const anchor=markerX<34?'start':markerX>202?'end':'middle';
  const status=price<lower?'下轨下方':price<middle?'下中轨之间':price<=upper?'中上轨之间':'上轨上方';
  const distance=(price-middle)/middle*100;
  const tone=price<middle?'low':'high';
  const date=escapeHtml(String(b.asOf||'').slice(0,10));
  return `<td class="boll-visual-cell"><div class="boll-visual" title="前复权周BOLL · ${date}"><div class="boll-visual-top"><b>${escapeHtml(status)}</b><span class="${tone}">距中轨 ${distance>=0?'+':''}${distance.toFixed(2)}%</span></div><svg class="boll-visual-svg" viewBox="0 0 236 62" role="img" aria-label="现价${price.toFixed(2)}，下轨${lower.toFixed(2)}，中轨${middle.toFixed(2)}，上轨${upper.toFixed(2)}"><line class="lower-rail" x1="0" y1="25" x2="${middleX}" y2="25"/><line class="upper-rail" x1="${middleX}" y1="25" x2="236" y2="25"/><g class="ticks"><line x1="0" y1="17" x2="0" y2="34"/><line x1="${middleX}" y1="17" x2="${middleX}" y2="34"/><line x1="236" y1="17" x2="236" y2="34"/></g><text class="current-price" x="${markerX}" y="10" text-anchor="${anchor}">${price.toFixed(2)}</text><circle class="current-ring" cx="${markerX}" cy="25" r="7"/><circle class="current-dot" cx="${markerX}" cy="25" r="3"/><g class="boll-svg-labels"><text x="0" y="47" text-anchor="start">${lower.toFixed(2)}</text><text x="0" y="59" text-anchor="start" class="sub">下轨</text><text x="${middleX}" y="47" text-anchor="middle">${middle.toFixed(2)}</text><text x="${middleX}" y="59" text-anchor="middle" class="sub">中轨</text><text x="236" y="47" text-anchor="end">${upper.toFixed(2)}</text><text x="236" y="59" text-anchor="end" class="sub">上轨</text></g></svg></div></td>`;
}
function bollRow(s){const b=s.weeklyBoll||{},levels=bollLevels(s),yieldRate=bollYield(s),gridDividend=forwardGridDividend(s),cells=[...levels.sell.map((y,i)=>bollTargetCell(s,y,'sell',i)),...levels.buy.map((y,i)=>bollTargetCell(s,y,'buy',i))].join('');return `<tr><td><b>${escapeHtml(s.name)}</b>${s.holding?'<span class="boll-holding">持仓</span>':''}<div class="stock-code">${escapeHtml(s.code)}</div><span class="source-pill">${b.asOf?`指标数据：${escapeHtml(b.asOf)}`:'BOLL 数据缺失'}</span></td><td><strong>${Number.isFinite(s.price)&&s.price>0?money(s.price):'—'}</strong></td><td class="boll-track boll-track-up">${bollTrack(b.upper,s.price,'上轨')}</td><td class="boll-track boll-track-mid">${bollTrack(b.middle,s.price,'中轨')}</td><td class="boll-track boll-track-down">${bollTrack(b.lower,s.price,'下轨')}</td>${bollVisual(s)}<td class="boll-yield"><strong>${yieldRate===null?'—':`${yieldRate.toFixed(2)}%`}</strong></td><td><strong>${gridDividend===null?'—':gridDividend.toFixed(3)}</strong><span class="source-pill">${escapeHtml(forwardGridSource(s))}</span>${s.manual?'<span class="source-pill">手动</span>':''}</td>${cells}</tr>`;}
function renderBollChips(){const names=normalizedPart2Groups().map(g=>g.name);const labels=['全部','★ 持仓','买点下轨','卖点上轨','近下轨','近上轨',...names,'✎ 编辑'];$('#bollChips').innerHTML=labels.map(label=>`<button class="boll-chip ${bollFilter===label.replace('★ ','')?'active':''}" data-boll-filter="${escapeHtml(label)}">${escapeHtml(label)}</button>`).join('');document.querySelectorAll('[data-boll-filter]').forEach(button=>button.onclick=()=>{const value=button.dataset.bollFilter;if(value==='✎ 编辑')return openBollManage();bollFilter=value.replace('★ ','');renderBollGrid();});}
function renderBollGrid(){if(!$('#bollGroups'))return;const historyState=privateHistoryStatusHtml('positions','Part 2 周 BOLL 历史网格');if(historyState){$('#bollChips').innerHTML='';$('#bollGroups').innerHTML=historyState;return;}renderBollChips();let items=bollItems().filter(item=>bollSignal(item,bollFilter));items.sort((a,b)=>bollSort==='yield'?bollYield(b)-bollYield(a):a.price-b.price);const groups=normalizedPart2Groups().filter(group=>items.some(item=>item.group===group.name));$('#bollGroups').innerHTML=groups.map(group=>{const list=items.filter(item=>item.group===group.name),levels=bollLevels(list[0]),heads=[...levels.sell.map(y=>`<th class="boll-sell-head">${y}%</th>`),...levels.buy.map((y,i)=>`<th class="boll-buy-head ${i===0?'split':''}">${y}%</th>`)].join('');return `<section class="boll-group ${bollCollapsed.has(group.name)?'collapsed':''}" data-boll-group="${escapeHtml(group.name)}"><button class="boll-group-head"><span>⌄</span>${escapeHtml(group.name)}${group.hydropower?'<em>水电阈值</em>':''}<i>${list.length}</i></button><div class="boll-table-wrap"><table class="boll-table"><thead><tr><th>股票</th><th>现价</th><th class="boll-track-head boll-track-head-up">上轨</th><th class="boll-track-head boll-track-head-mid">中轨</th><th class="boll-track-head boll-track-head-down">下轨</th><th class="boll-visual-head">当前位置</th><th>计算股息率</th><th>计算股息</th>${heads}</tr></thead><tbody>${list.map(bollRow).join('')}</tbody></table><div class="boll-formula">周BOLL：前复权周K · BOLL(20,2) · 样本标准差。橙色买入网格，绿色卖出网格；颜色越深信号越强，“已达”表示现价已触及该档。</div></div></section>`;}).join('')||'<p class="muted empty">当前筛选没有匹配标的。</p>';document.querySelectorAll('.boll-group-head').forEach(button=>button.onclick=()=>{const name=button.parentElement.dataset.bollGroup;bollCollapsed.has(name)?bollCollapsed.delete(name):bollCollapsed.add(name);renderBollGrid();});}
function fillBollSettings(){$('#bollBuyStep').value=String(bollSettings.buyStep);$('#bollBuyCount').value=String(bollSettings.buyCount);$('#bollSellStep').value=String(bollSettings.sellStep);$('#bollSellCount').value=String(bollSettings.sellCount);$('#bollLowDev').value=bollSettings.lowDev;$('#bollHighDev').value=bollSettings.highDev;$('#bollYieldDev').value=bollSettings.yieldDev;}
function fillBollManage(){const groups=normalizedPart2Groups();$('#bollGroupSelect').innerHTML=groups.map(g=>`<option>${escapeHtml(g.name)}</option>`).join('');$('#bollStockOptions').innerHTML=catalog.slice(0,5875).map(s=>`<option value="${s.code} ${escapeHtml(s.name)}"></option>`).join('');$('#bollManageList').innerHTML=groups.map(g=>`<div><b>${escapeHtml(g.name)}</b><span>${(g.codes||[]).length}个标的${g.hydropower?' · 水电阈值':''}</span></div>`).join('');}
function openBollManage(){fillBollManage();$('#bollManageDialog').showModal();}
$('#openBollSettings').onclick=()=>{fillBollSettings();$('#bollSettingsDialog').showModal();};
$('#bollSettingsForm').onsubmit=e=>{e.preventDefault();$('#bollSettingsDialog').close();showMessage('设置暂未保存','验收阶段不会把策略设置写入浏览器本地存储或公开 GitHub；待私有写入 RPC 审核后再开放。');};
document.querySelectorAll('[data-close-dialog]').forEach(button=>button.onclick=()=>$('#'+button.dataset.closeDialog).close());
document.querySelectorAll('[data-boll-sort]').forEach(button=>button.onclick=()=>{bollSort=button.dataset.bollSort;document.querySelectorAll('[data-boll-sort]').forEach(x=>x.classList.toggle('active',x===button));renderBollGrid();});
$('#collapseBollAll').onclick=()=>{const groups=normalizedPart2Groups(),all=groups.length&&groups.every(g=>bollCollapsed.has(g.name));bollCollapsed=all?new Set():new Set(groups.map(g=>g.name));$('#collapseBollAll').textContent=all?'全部折叠':'全部展开';renderBollGrid();};
$('#bollAddGroup').onclick=async()=>{const name=$('#bollNewGroup').value.trim();if(!name)return;if(normalizedPart2Groups().some(g=>g.name===name))return showMessage('板块已存在','请换一个板块名称。');try{await requireAuth();part2Config.groups.push({name,hydropower:$('#bollNewHydro').checked,codes:[]});await mutatePart2Config(()=>part2Config);$('#bollNewGroup').value='';$('#bollNewHydro').checked=false;fillBollManage();renderBollGrid();}catch(error){showMessage('添加失败',error.message);}};
$('#bollAddStock').onclick=async()=>{const raw=$('#bollStockSearch').value.trim(),code=(raw.match(/\d{6}/)||[])[0],candidate=catalog.find(s=>s.code===code)||catalog.find(s=>s.name===raw),groupName=$('#bollGroupSelect').value;if(!candidate)return showMessage('未找到股票','请从搜索建议中选择股票。');try{await requireAuth();const holding=holdings.some(h=>h.code===candidate.code);if(!holding&&!part2Config.extraStocks.some(s=>s.code===candidate.code))part2Config.extraStocks.push({code:candidate.code,name:candidate.name});for(const group of part2Config.groups)group.codes=(group.codes||[]).filter(c=>c!==candidate.code);part2Config.groups.find(g=>g.name===groupName).codes.push(candidate.code);await mutatePart2Config(()=>part2Config);$('#bollStockSearch').value='';fillBollManage();renderBollGrid();showMessage('Part 2标的已添加',`${candidate.name}已加入${groupName}，不会自动进入Part 1。行情任务会自动补充数据。`);}catch(error){showMessage('添加失败',error.message);}};
function personalStockUniverse(){return trackedStocks.map(item=>({code:item.code,name:item.name||item.code}));}
function renderCalendar(){const historyState=privateHistoryStatusHtml('calendar','Part 4 分红日历');if(historyState){$('#calendarGrid').innerHTML=historyState;$('#eventTitle').textContent='私有历史等待读取';$('#eventList').innerHTML='';return;}const universe=personalStockUniverse(),codes=new Set(universe.map(item=>item.code)),y=viewMonth.getFullYear(),m=viewMonth.getMonth();$('#monthTitle').textContent=`${y}年${m+1}月`;const first=new Date(y,m,1),start=new Date(y,m,1-((first.getDay()+6)%7));let html=['一','二','三','四','五','六','日'].map(x=>`<div class="day-name">${x}</div>`).join('');for(let i=0;i<42;i++){const d=new Date(start);d.setDate(start.getDate()+i);const key=[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');const has=market.events.some(e=>e.date===key&&codes.has(e.code));html+=`<button class="day ${d.getMonth()!==m?'other':''} ${has?'has-event':''} ${key===selectedDate?'selected':''}" data-day="${key}">${d.getDate()}</button>`;}$('#calendarGrid').innerHTML=html;document.querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>{selectedDate=b.dataset.day;renderCalendar();});const events=market.events.filter(e=>e.date===selectedDate&&codes.has(e.code));$('#eventTitle').textContent=`${selectedDate} 分红事件`;$('#eventList').innerHTML=events.length?events.map(e=>{const source=String(e.source||''),url=String(e.sourceUrl||''),sourceText=source?` · ${escapeHtml(source)}`:'',sourceLink=/^https:\/\/data\.eastmoney\.com\/notices\/detail\/\d{6}\/AN\d{12,32}\.html$/.test(url)?` <a href="${escapeHtml(url)}" target="_blank" rel="noopener">原公告 ↗</a>`:'';return `<div class="event"><div class="event-head"><b>${escapeHtml(e.name)} · ${escapeHtml(e.type)}</b><span class="amount">${e.amount?money(e.amount):''}</span></div><small>${e.code} · ${escapeHtml(e.description||'')}${sourceText}${sourceLink}</small></div>`;}).join(''):'<p class="muted">当天没有私有自选股分红事件。</p>';}
$('#prevMonth').onclick=()=>{viewMonth=new Date(viewMonth.getFullYear(),viewMonth.getMonth()-1,1);renderCalendar();};$('#nextMonth').onclick=()=>{viewMonth=new Date(viewMonth.getFullYear(),viewMonth.getMonth()+1,1);renderCalendar();};

function newsDividendAmount(value){if(!['number','string'].includes(typeof value)||(typeof value==='string'&&!value.trim()))return null;const amount=Number(value);return Number.isFinite(amount)&&amount>=0?amount:null;}
function renderNews(){const historyState=privateHistoryStatusHtml('news','Part 5 公告历史');if(historyState){$('#newsSummary').innerHTML=historyState;$('#newsUpdateText').textContent='私有历史等待读取';$('#newsList').innerHTML='';return;}const universe=personalStockUniverse(),codes=new Set(universe.map(item=>item.code)),requestedFilter=$('#newsStockFilter').value,filter=codes.has(requestedFilter)?requestedFilter:'';const relevant=newsMemory.items.filter(item=>codes.has(item.code)&&(!filter||item.code===filter));const estimates=relevant.filter(x=>newsDividendAmount(x.estimatedDividendPerShare)!==null).length;$('#newsSummary').innerHTML=[['已记住公告/新闻',`${relevant.length}条`],['可计算每股分红',`${estimates}条`],['处理方式','私有历史迁移'],['正式值保护','预估不覆盖']].map(x=>`<div class="summary-card"><small>${x[0]}</small><strong>${x[1]}</strong></div>`).join('');$('#newsUpdateText').textContent=`私有记录更新：${formatTime(newsMemory.lastScanAt)}`;const expectedOptions=['',...universe.map(h=>h.code)],currentOptions=[...$('#newsStockFilter').options].map(o=>o.value);if(JSON.stringify(currentOptions)!==JSON.stringify(expectedOptions)){$('#newsStockFilter').innerHTML='<option value="">全部私有自选股</option>'+universe.map(h=>`<option value="${h.code}">${escapeHtml(h.name)} ${h.code}</option>`).join('');}$('#newsStockFilter').value=filter;
$('#newsList').innerHTML=relevant.length?relevant.map(item=>{const value=newsDividendAmount(item.estimatedDividendPerShare)!==null?money(newsDividendAmount(item.estimatedDividendPerShare)):'暂不可计算';const link=item.url?`<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">查看原始来源 ↗</a>`:'<span class="muted">来源链接未返回</span>';return `<article class="news-card"><div class="news-card-head"><div><span class="status status-${item.status==='已实施'?'done':item.status==='正式预案'?'proposal':'forecast'}">${escapeHtml(item.status)}</span><b>${escapeHtml(item.name)} · ${item.code}</b></div><time>${escapeHtml(item.publishedAt?.slice(0,10)||'')}</time></div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.summary)}</p><div class="estimate-box"><div><small>每股分红结果</small><strong>${value}</strong></div><div><small>可信度</small><strong>${escapeHtml(item.confidence)}</strong></div><div class="calculation"><small>计算依据</small><span>${escapeHtml(item.calculation)}</span></div></div><div class="news-foot"><span>${escapeHtml(item.source||'东方财富资讯')}</span>${link}</div></article>`}).join(''):'<p class="muted empty">暂无已保存的相关公告。采集由本机任务完成，请以记录采集时间为准。</p>';}
$('#newsStockFilter').onchange=renderNews;

function normalizeTradePayload(value){if(Array.isArray(value))return {version:1,updatedAt:null,records:value};return {version:1,updatedAt:value?.updatedAt||null,records:Array.isArray(value?.records)?value.records:[]};}
async function loadCloudTradeRecords(){return normalizeTradePayload({records:tradeRecords});}
async function mutateTradeRecords(mutator){await requireAuth();const before=new Set(tradeRecords.map(record=>String(record.id)));const next=mutator([...tradeRecords]);const additions=next.filter(record=>record&&record.id&&!before.has(String(record.id)));if(additions.length)await supabaseRpc('personal_append_trade_records',{p_records:additions});await loadPersonalPart('strategy',true);return {records:tradeRecords,inserted:additions.length};}
async function loadStrategyFeedback(){return normalizeTradePayload({records:strategyFeedback});}
async function mutateStrategyFeedback(record){await requireAuth();await supabaseRpc('personal_append_strategy_feedback_v2',{p_record:record});await loadPersonalPart('strategy',true);return record;}
function watchlistVersion(items){
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
  return JSON.stringify(canonical(watchlistPayload(items).sort((a,b)=>a.code.localeCompare(b.code))));
}
function openWatchlistSave(){
  if(!authSession)return openLogin();
  if(watchlistOperation||watchlistUncertain)return;
  const {added,removed}=watchlistChanges();
  if(!added.length&&!removed.length)return;
  $('#watchlistSaveDetail').textContent=[added.length?'新增：'+added.map(x=>x.name).join('、'):'',removed.length?'移除：'+removed.map(x=>x.name).join('、'):''].filter(Boolean).join('；');
  $('#watchlistSaveDialog').showModal();
}
async function replacePrivateWatchlist(items,operation){
  const session=operation.session;
  await requireAuth();
  if(authSession!==session||watchlistOperation!==operation)throw new Error('登录会话已变化，请重新读取清单后再保存');
  if(!Array.isArray(items)||items.length<1||items.length>50||new Set(items.map(x=>x.code)).size!==items.length)throw new Error('清单须包含1—50只不同股票');
  if(items.some(x=>!/^\d{6}$/.test(x.code)||!x.name||x.name.length>80))throw new Error('股票代码或名称无效');
  const clean=watchlistPayload(items),expected=watchlistVersion(watchlistBase||trackedStocks);
  const pending=personalPartLoads.get('holdings');if(pending)await pending;
  if(authSession!==session||watchlistOperation!==operation)throw new Error('登录会话已变化，请重新读取清单后再保存');
  const current=await supabaseRpc('personal_get_part1');
  if(authSession!==session||watchlistOperation!==operation)throw new Error('登录会话已变化，请重新读取清单后再保存');
  if(!Array.isArray(current?.watchlist)||watchlistVersion(current.watchlist)!==expected)throw new Error('云端清单已变化，请重新读取清单后再编辑，避免覆盖其他设备的修改');
  // One owner-scoped write. No VPS writer, trading, collector or AI call.
  watchlistUncertain=true;
  const receipt=await supabaseRpc('personal_replace_watchlist',{p_items:clean});
  if(authSession!==session||watchlistOperation!==operation)throw new Error('会话已变化；保存结果需重新登录读取确认');
  const after=await supabaseRpc('personal_get_part1');
  if(authSession!==session||watchlistOperation!==operation||receipt?.count!==clean.length||!Array.isArray(after?.watchlist)||watchlistVersion(after.watchlist)!==watchlistVersion(clean))throw new Error('保存请求已发送，但读回尚未确认；请重新读取名单核对，不要重复提交');
  applyPersonalPart('holdings',after);personalLoadedParts.add('holdings');
  watchlistUncertain=false;resetWatchlistDraft();
  render();
  const pages=['positions','news','strategy'];
  await Promise.all([loadPersonalMarket(true),...pages.map(page=>loadPersonalPart(page,true))]);
  if(authSession!==session||watchlistOperation!==operation)return;
  if(marketLoaded){personalLoadedParts.add('grid');personalLoadedParts.add('calendar');}
  const incomplete=pages.some(page=>personalPartErrors.has(page))||!marketLoaded;
  watchlistMessage=incomplete?'名单已保存并读回；部分关联数据暂未读取成功，可进入对应栏目重读。':'名单已保存并读回；Part 2—6 已按新清单关联，新增数据以各栏时间与状态为准。VPS 未提交。';
  render();
}
async function saveWatchlistDraft(){
  if(!authSession)return openLogin();
  if(watchlistOperation||watchlistUncertain)return;
  const operation={session:authSession,kind:'save'};watchlistOperation=operation;
  watchlistSaving=true;$('#confirmWatchlistSave').disabled=true;
  const session=authSession,items=watchlistPayload(watchlistDraft||[]);
  $('#watchlistSaveDialog').close();renderHoldings();
  try{await replacePrivateWatchlist(items,operation);}
  catch(error){
    if(authSession===session&&watchlistOperation===operation){
      watchlistMessage=watchlistUncertain?'保存结果待核对：请点“重新读取清单”，确认云端结果后再编辑。':error?.message||'清单未保存，草稿仍保留。';
      showMessage(watchlistUncertain?'保存结果待核对':'清单未保存',watchlistMessage);
    }
  }finally{
    if(watchlistOperation===operation){
      watchlistOperation=null;watchlistSaving=false;
      if(authSession===session){$('#confirmWatchlistSave').disabled=false;renderHoldings();}
    }
  }
}
async function refreshWatchlist(){
  if(!authSession)return openLogin();
  if(watchlistOperation)return;
  const changed=watchlistChanges();
  if(!watchlistUncertain&&(changed.added.length||changed.removed.length)&&!confirm('重新读取会放弃当前草稿，按云端清单重新开始。确定继续吗？'))return;
  const session=authSession,operation={session,kind:'read'};watchlistOperation=operation;
  $('#watchlistSaveDialog').close();renderHoldings();
  try{
    const pending=personalPartLoads.get('holdings');if(pending)await pending;
    if(authSession!==session||watchlistOperation!==operation)return;
    const data=await supabaseRpc('personal_get_part1');
    if(authSession!==session||watchlistOperation!==operation)return;
    if(!Array.isArray(data?.watchlist))throw new Error('读取失败');
    applyPersonalPart('holdings',data);personalLoadedParts.add('holdings');watchlistUncertain=false;resetWatchlistDraft();
    watchlistMessage='已重新读取云端清单。没有提交 VPS 目标，也没有触发数据采集。';render();
  }catch(error){if(authSession===session&&watchlistOperation===operation)showMessage('清单读取失败','保留当前草稿；本次没有发送保存请求。');}
  finally{if(watchlistOperation===operation){watchlistOperation=null;if(authSession===session)renderHoldings();}}
}
async function addWatchlistItem(){
  await requireAuth();
  if(watchlistOperation||!personalLoadedParts.has('holdings'))throw new Error('请等待清单读取或保存完成');
  if(watchlistDraft===null)resetWatchlistDraft();
  const raw=String($('#stockSearch').value||'').trim(),code=(raw.match(/\d{6}/)||[])[0];
  const candidate=catalog.find(item=>item.code===code||item.name===raw)||(selectedCandidate&&catalog.find(item=>item.code===selectedCandidate.code));
  if(!candidate)throw new Error('请先从搜索结果中选择股票');
  if(watchlistDraft.some(item=>item.code===candidate.code))throw new Error('这只股票已经在清单中');
  if(watchlistDraft.length>=50)throw new Error('最多维护50只股票');
  const original=watchlistBase.find(item=>item.code===candidate.code);
  watchlistDraft.push(original?{...original}:{code:String(candidate.code),name:String(candidate.name)});
  $('#stockSearch').value='';selectedCandidate=null;$('#suggestions').classList.add('hidden');
  $('#selectedStock').textContent='已加入草稿，确认保存后才会更新云端。';
  renderHoldings();
}
async function deleteWatchlistItem(code){
  if(!authSession)return openLogin();
  if(watchlistOperation||!personalLoadedParts.has('holdings'))return;
  if(watchlistDraft===null)resetWatchlistDraft();
  if(watchlistDraft.some(item=>item.code===code)){
    if(watchlistDraft.length<=1)return showMessage('至少保留一只','统一清单不能提交为空；持仓和历史记录不会随名单删除。');
    watchlistDraft=watchlistDraft.filter(item=>item.code!==code);
  }else{
    const old=watchlistBase.find(item=>item.code===code);
    if(old&&watchlistDraft.length<50)watchlistDraft.push({...old});
  }
  renderHoldings();
}
async function refreshStrategyCloud(){await requireAuth();await loadPersonalPart('strategy',true);renderStrategy();}
function compactPositions(positions={}){return {asOf:positions.asOf||null,day:positions.day?{zone:positions.day.zone,percent:Number(positions.day.percent)}:null,week:positions.week?{zone:positions.week.zone,percent:Number(positions.week.percent)}:null,month:positions.month?{zone:positions.month.zone,percent:Number(positions.month.percent)}:null};}
function tradeLearning(record){const y=Number(record.context?.yield||0),day=record.context?.positions?.day?.zone||'',action=record.action;if(action==='做T卖出'&&day==='上部')return '日线上部做T卖出：强化高位减仓习惯';if(action==='做T买入'&&day==='下部')return '日线下部做T买入：强化回落接回习惯';if(action.includes('买入')&&y>=7)return '7%以上仍买入：强化高股息率高性价比偏好';if(action.includes('买入')&&y>=5)return day==='下部'?'5%以上且日线下部买入：强化回落加仓偏好':'5%以上买入：强化分批建仓规则';if(action.includes('卖出')&&y>0&&y<=4.5)return '4%～4.5%卖出：强化清仓底线';return '已纳入策略画像，等待更多相似操作形成稳定规律';}
function tradeZonePill(period,item){if(!item)return `<span class="trade-zone">${period}待更新</span>`;return `<span class="trade-zone ${zoneClass(item.zone)}">${period}${escapeHtml(item.zone[0])}</span>`;}
function learnedProfile(){const buys=tradeRecords.filter(r=>r.action.includes('买入')),sells=tradeRecords.filter(r=>r.action.includes('卖出')),tRecords=tradeRecords.filter(r=>r.action.startsWith('做T'));const avg=list=>list.length?list.reduce((sum,r)=>sum+Number(r.context?.yield||0),0)/list.length:0;const commonDay=list=>{const counts={};list.forEach(r=>{const z=r.context?.positions?.day?.zone;if(z)counts[z]=(counts[z]||0)+1;});return Object.entries(counts).sort((a,b)=>b[1]-a[1])[0]?.[0]||'待积累';};return {buys,sells,tRecords,buyYield:avg(buys),sellYield:avg(sells),buyDay:commonDay(buys),tDay:commonDay(tRecords)};}
function strategyAdviceFor(holding){const s=stock(holding.code),y=s.price?s.totalDividend/s.price*100:0,p=s.positions||{},day=p.day?.zone||'待更新',week=p.week?.zone||'待更新',month=p.month?.zone||'待更新',hasHolding=Number(holding.shares)>0;let action='继续观察',kind='wait',why='等待股息率进入你的明确买卖区间，并继续观察日、周、月位置。',priority=1;if(!s.price||!s.totalDividend){action='等待正式数据';why='当前价格或正式年度分红尚未完整，暂不生成买卖方向。';priority=0;}else if(y<=4.5){action=hasHolding?'卖出区提醒':'暂不追入';kind='sell';why=hasHolding?'股息率已进入你设定的4%～4.5%全部卖出区，优先检查是否需要清仓。':'股息率处于你的低性价比区，当前没有持仓时不建议追入。';priority=5;}else if(y>=7){action='高性价比分批买';kind='buy';why=day==='上部'?'股息率达到7%高性价比区，但日线偏上，适合分批而不是一次买满。':'股息率达到7%高性价比区，且日线没有处在上部，符合你的积极分批条件。';priority=6;}else if(y>=5){if(day==='下部'){action='可分批买入';kind='buy';why='股息率达到5%起买线，同时日线处于下部，符合你回落分批买入的框架。';priority=4;}else if(day==='上部'&&hasHolding){action='做T卖出观察';kind='wait';why='股息率仍在5%以上，但日线已经到上部；已有底仓时可观察是否先T出一部分。';priority=3;}else{action='小仓分批/等待';kind='wait';why=`股息率已达到5%起买线，但日线位于${day}，更符合先小仓或等待回落。`;priority=2;}}else if(y<5){action='等待接近5%';why='尚未达到你的5%起买线，也没有进入4%～4.5%的持仓卖出底线。';priority=1;}const similar=tradeRecords.filter(r=>r.code===holding.code||Math.abs(Number(r.context?.yield||0)-y)<.35).length,confidence=Math.min(92,55+Math.min(tradeRecords.length,10)*2+Math.min(similar,5)*3),ai=(strategyAnalysis.advice||[]).find(item=>String(item.code)===holding.code);return {holding,s,y,day,week,month,action,kind,why,priority,similar,confidence,ai};}
const RESEARCH_MATCH_SCALE='research_match_percent_0_to_100',RESEARCH_MATCH_SCHEMA_VERSION=3,RESEARCH_MATCH_MEANING='研究匹配度，不是涨跌概率、收益概率或自动下单依据';
function researchMatchPercent(value,analysis=strategyAnalysis){if(typeof value!=='number'||!Number.isFinite(value)||!Number.isInteger(value)||Number(analysis?.schemaVersion)!==RESEARCH_MATCH_SCHEMA_VERSION||analysis?.confidenceScale!==RESEARCH_MATCH_SCALE||analysis?.confidenceMeaning!==RESEARCH_MATCH_MEANING||value<0||value>100)return null;return value;}
function researchMatchBadge(value,analysis=strategyAnalysis){const percent=researchMatchPercent(value,analysis);return percent===null?'研究匹配度待刷新':`研究匹配度 ${percent}%`;}
function renderBriefCommand(advice){let command=strategyAnalysis.briefCommand,analysisForCommand=strategyAnalysis;if(!command?.action){
  $('#briefCommandTitle').textContent='等待新的私有分析';$('#briefCommandBadge').textContent='尚无分析';$('#briefCommandReason').textContent='尚未收到模型结果，不会用本地规则伪造新建议。';$('#briefCommandFacts').innerHTML='';
  document.querySelectorAll('[data-strategy-feedback]').forEach(button=>{button.disabled=true;button.onclick=null;});const status=$('#briefFeedbackStatus');if(status)status.textContent='没有可绑定的真实建议，不能提交反馈。';return;
}
  const code=String(command.code||''),name=command.name||stock(code).name||'',stockData=code?stock(code):{},yieldRate=Number.isFinite(stockData.totalDividend)&&Number.isFinite(stockData.price)&&stockData.price>0?stockData.totalDividend/stockData.price*100:null,currentFeedback=strategyFeedback.find(x=>x.recommendationId===command.id),action=String(command.action||'当前不买'),researchMatch=researchMatchPercent(command.confidence,analysisForCommand);$('#briefCommandTitle').textContent=(strategyAnalysisIsCurrent()?'':'历史建议 · ')+(code?`${action}：${name}（${code}）`:action);$('#briefCommandBadge').textContent=researchMatchBadge(command.confidence,analysisForCommand);$('#briefCommandBadge').title=researchMatch===null?'旧版结果没有可验证的研究匹配度刻度；页面不会猜测 0.72、1 等数值代表的百分比，等待 v3 升级或新的本机 Worker 结果。':'研究匹配度表示当前建议与已确认规则、当前数据和有限样本的一致性；不是涨跌概率、收益概率或自动下单依据。';$('#briefCommandBadge').className=/买入/.test(action)?'buy':/卖出/.test(action)?'sell':'wait';$('#briefCommandReason').textContent=[command.reason,command.condition].filter(Boolean).map(text=>String(text).replace(/[；;。]+$/,'')).join('；')+'。';$('#briefCommandFacts').innerHTML=code?`<span>现价 <b>${money(stockData.price)}</b></span><span>正式股息率 <b>${yieldRate===null?'—':`${yieldRate.toFixed(2)}%`}</b></span><span>反馈样本 <b>${Number(strategyFeedback.length||strategyAnalysis.feedbackStats?.count||0)}次</b></span>`:'<span>当前没有合格买点</span>';document.querySelectorAll('[data-strategy-feedback]').forEach(button=>{const selected=currentFeedback?.status===button.dataset.strategyFeedback;button.classList.toggle('selected',selected);button.disabled=!!currentFeedback||feedbackPending;button.title=feedbackPending?'正在保存反馈…':currentFeedback?'该建议的反馈已保存，为避免重复记账不可再次提交。':'提交一次真实反馈';button.setAttribute('aria-label',button.title);});const feedbackStatus=$('#briefFeedbackStatus');if(feedbackStatus)feedbackStatus.textContent=feedbackPending?'正在保存反馈…':currentFeedback?`已记录“${({executed:'已执行',not_executed:'没买 / 没执行',deferred:'暂缓观察'})[currentFeedback.status]||'反馈'}”；为避免同一建议重复记账，本条反馈已锁定。`:'';document.querySelectorAll('[data-strategy-feedback]').forEach(button=>{button.onclick=()=>submitStrategyFeedback(button.dataset.strategyFeedback,command);});}
async function submitStrategyFeedback(status,command){if(feedbackPending)return;feedbackPending=true;document.querySelectorAll('[data-strategy-feedback]').forEach(button=>{button.disabled=true;});const labels={executed:'已执行',not_executed:'没买 / 没执行',deferred:'暂缓观察'};try{await requireAuth();await mutateStrategyFeedback({id:crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`,recommendationId:String(command.id),status,code:String(command.code||''),name:String(command.name||''),action:String(command.action||''),reason:String(command.reason||''),condition:String(command.condition||''),recommendedAt:strategyAnalysis.updatedAt||new Date().toISOString(),createdAt:new Date().toISOString()});renderStrategy();showMessage('反馈已保存',`已记录“${labels[status]}”。画像演进暂缓；保存反馈不会立即生成新分析。`);}catch(error){if(!/请先/.test(error.message))showMessage('反馈未保存',error.message);}finally{feedbackPending=false;renderStrategy();}}
function renderStrategyPerformance(){
  const box=$('#strategyPerformance');
  if(!box)return;
  const p=strategyRecommendationMeta.performance||{};
  const has=value=>value!==null&&value!==undefined&&Number.isFinite(Number(value));
  const rate=has(p.successRate)?`${Number(p.successRate).toFixed(1)}%`:'待积累';
  const time=has(p.avgTradingDaysToHit)?`${Number(p.avgTradingDaysToHit).toFixed(1)}个交易日`:'待积累';
  box.innerHTML=`<div><small>策略命中率</small><strong>${rate}</strong><span>${Number(p.successes||0)}次命中 / ${Number(p.resolved||0)}次已结算</span></div><div><small>平均达标时间</small><strong>${time}</strong><span>${Number(p.pending||0)}条买入指令观察中</span></div><p>独立结果跟踪 ${escapeHtml(strategyRecommendationMeta.asOf?formatTime(strategyRecommendationMeta.asOf):'尚未同步')}；不代表 AI 画像已更新，也不保证未来收益。</p>`;
}
function renderStrategyAudit(){
  const profile=strategyProfile||{};
  const rules=Array.isArray(profile.externalLearnedRules)?profile.externalLearnedRules:[];
  const evidence=Array.isArray(profile.personalBehaviorEvidence?.evidence)?profile.personalBehaviorEvidence.evidence:[];
  const suggestions=Array.isArray(profile.nonExecutablePositionSuggestions)?profile.nonExecutablePositionSuggestions:[];
  const gaps=Array.isArray(profile.conflictsAndGaps)?profile.conflictsAndGaps:[];
  if($('#profileAuditStatus'))$('#profileAuditStatus').textContent=profile.auditMeta?.provider==='official-openai'?'官方复核 · 建议隔离':'私有记录待接入';
  if($('#externalRules'))$('#externalRules').innerHTML=rules.map(rule=>`<article class="audit-item"><b>${escapeHtml(rule.normalizedRule||rule.rule||'未命名规则')}</b><small>${escapeHtml(rule.sourceImage||'来源待补充')} · ${escapeHtml(rule.sourceLocator||'定位待补充')} · ${escapeHtml(rule.status||'待核对')}</small></article>`).join('')||'<p class="muted">暂无外部规则</p>';
  if($('#behaviorEvidence'))$('#behaviorEvidence').innerHTML=evidence.map(item=>`<article class="audit-item"><b>${escapeHtml(item.criterion||'行为证据')}</b><small>样本 ${escapeHtml(item.sampleSize??'—')} · 置信度 ${escapeHtml(item.confidence||'待核对')} · ${escapeHtml(item.limitations||'边界待补充')}</small></article>`).join('')||'<p class="muted">暂无私有行为证据</p>';
  if($('#nonExecutableSuggestions'))$('#nonExecutableSuggestions').innerHTML=[...suggestions.map(item=>typeof item==='string'?item:item.text||''),...gaps].filter(Boolean).map(item=>`<article class="audit-item"><b>${escapeHtml(item)}</b><small>状态：暂不可执行 / 需人工确认</small></article>`).join('')||'<p class="muted">暂无缺口</p>';
}
function renderStrategyEvolution(){
  const e=strategyProfile?.strategyEvolution||{};
  if(!$('#evolutionHeadline'))return;
  $('#evolutionHeadline').textContent=e.headline||'私有操作记录接入后形成综合进化结论';
  $('#evolutionOverall').textContent=e.overallView||'当前不读取公开操作记录，也不会把空样本包装成学习结论。';
  const thoughts=Array.isArray(e.currentThoughts)?e.currentThoughts:[];
  $('#evolutionThoughts').innerHTML=thoughts.map((item,index)=>`<article><span>想法 ${index+1}</span><b>${escapeHtml(typeof item==='string'?item:item.text||'')}</b></article>`).join('')||'<p class="muted">暂无综合想法</p>';
  $('#evolutionEvidence').innerHTML=(e.evidenceFindings||[]).map(item=>`<article class="evolution-item"><b>${escapeHtml(item.title||'证据')}</b><p>${escapeHtml(item.fact||'')}</p><small>判断：${escapeHtml(item.interpretation||'待补充')}<br>边界：${escapeHtml(item.boundary||'待补充')}</small></article>`).join('')||'<p class="muted">暂无证据结论</p>';
  $('#evolvedRules').innerHTML=(e.evolvedRules||[]).map(item=>`<article class="evolution-item"><b>${escapeHtml(item.rule||'规则')}</b><p>${escapeHtml(item.why||'')}</p><small>${escapeHtml(item.status||'待观察')} · 验证：${escapeHtml(item.verificationMetric||'待定义')}</small></article>`).join('')||'<p class="muted">暂无进化规则</p>';
  $('#nextObservations').innerHTML=(e.nextObservations||[]).map(item=>`<article class="evolution-item"><b>${escapeHtml(typeof item==='string'?item:item.metric||item.title||'持续观察')}</b>${typeof item==='object'&&item.why?`<small>${escapeHtml(item.why)}</small>`:''}</article>`).join('')||'<p class="muted">暂无观察项</p>';
  $('#evolutionDisclaimer').textContent=e.disclaimer||'这些内容用于复盘与人工确认，不构成收益保证，不会自动下单。';
}
function strategyAnalysisIsCurrent(now=new Date()){
  const stamp=Date.parse(strategyAnalysis?.updatedAt||'');
  if(strategyAnalysis?.status!=='success'||!Number.isFinite(stamp)||stamp>now.getTime())return false;
  const day=value=>new Date(value).toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
  return day(stamp)===day(now);
}
function renderStrategyFreshness(){
  const el=$('#strategyFreshness');if(!el)return;
  const rows=[['AI 画像','按要求暂缓演进'],['AI 分析',strategyAnalysis.updatedAt?`${formatTime(strategyAnalysis.updatedAt)} · ${strategyAnalysisIsCurrent()?'当日结果':'历史结果，未收到当日分析'}`:'尚无结果'],['建议结果跟踪',strategyRecommendationMeta.asOf?formatTime(strategyRecommendationMeta.asOf):'尚未接通独立跟踪'],['操作记录',strategyDataTimes.trades?formatTime(strategyDataTimes.trades):'没有更新时间'],['真实反馈',strategyDataTimes.feedback?formatTime(strategyDataTimes.feedback):'没有更新时间']];
  el.innerHTML=rows.map(([label,value])=>`<div><small>${escapeHtml(label)}</small><span>${escapeHtml(value)}</span></div>`).join('');
}
function renderStrategyApiHealth(){
  const el=$('#strategyApiHealth');
  if(!el)return;
  const health=strategyApiHealth||{};
  const legacyPlatformMissing=health.provider==='official-openai'&&!health.authMode&&health.reason==='未配置 OPENAI_API_KEY';
  const status=legacyPlatformMissing?'unknown':health.status==='ok'?'ok':health.status==='failed'?'failed':'unknown';
  const plusWorker=health.authMode==='chatgpt_subscription';
  const label=status==='ok'?'通过':legacyPlatformMissing?'待切换':status==='failed'?'本次失败':'尚未完成';
  const reason=legacyPlatformMissing
    ?'旧版 GitHub Actions Platform API 未启用，等待 ChatGPT Plus/Codex 本机 Worker 首次运行'
    :(health.reason||(status==='unknown'?'私有策略健康记录尚无结论':'未返回原因'));
  const channel=plusWorker?'ChatGPT Plus/Codex 本机 Worker':health.provider==='official-openai'?'旧版 GitHub Actions/OpenAI Platform':'私有策略 Worker';
  const checked=health.checkedAt?` · 最后检查 ${formatTime(health.checkedAt)}`:'';
  el.className=`strategy-api-health ${status}`;
  el.textContent=`AI 历史检查：${label}（${channel} · ${reason}${checked}）；此记录不代表当前 Worker 正在运行。`;
}
function focusedVariantDetails(stock){
  const variants=stock?.variantStudy?.variants||[];
  if(!variants.length)return '';
  return `<details class="focused-variant-details"><summary>查看组合结果</summary><div class="focused-variant-list">${variants.map(item=>`<div class="focused-variant-row"><span>${escapeHtml(item.signal||'组合')}</span><b>${item.episodes??0}窗</b></div>`).join('')}</div></details>`;
}
function renderFocusedStudy(){
  const box=$('#focusedStudyGrid');
  if(!box)return;
  const items=[...(focusedStudy?.stocks||[]),...(focusedStudyNew?.stocks||[])];
  $('#focusedStudyMeta').textContent=items.length?`${items.length}只 · 仅展示已保存研究结果`:'暂无专项回测';
  box.innerHTML=items.map(stockItem=>`<article class="focused-study-card"><div class="focused-study-row"><div class="focused-study-main"><div class="focused-study-title"><b>${escapeHtml(stockItem.name||stockItem.code||'未命名')}</b><span>${escapeHtml(stockItem.code||'')} · ${escapeHtml(stockItem.episodes??'—')}个观察窗口</span></div><div class="focused-study-badge">研究数据</div></div><div class="focused-study-copy"><small>${escapeHtml(stockItem.learnedClassification?.rule||'结果仅供人工复核。')}</small></div></div>${focusedVariantDetails(stockItem)}</article>`).join('')||'<p class="muted">专项回测数据尚未加载。</p>';
}
function renderStrategy(){
  if(!$('#strategy'))return;
  const historyState=privateHistoryStatusHtml('strategy','Part 6 操作与策略历史');
  const historyStatusBox=$('#strategyPrivateStatus');
  if(historyStatusBox){historyStatusBox.innerHTML=historyState;historyStatusBox.hidden=!historyState;}
  renderStrategyApiHealth();
  renderStrategyFreshness();
  renderStrategyAudit();
  renderStrategyEvolution();
  renderFocusedStudy();
  const profile=learnedProfile();
  const count=tradeRecords.length;
  const progress=Math.min(100,count*8);
  const stage='画像演进暂缓 · 保留历史记录';
  $('#strategyStage').textContent=`${stage} · ${strategyAnalysisIsCurrent()?'当日分析已保存':'当前未收到当日分析'}`;
  $('#strategyProgress').style.width=`${progress}%`;
  $('#strategyStats').innerHTML=[['私有操作',`${count}笔`],['做T记录',`${profile.tRecords.length}笔`],['画像置信度',count<5?'较低':count<12?'中等':'较高']].map(([title,value])=>`<div class="strategy-stat"><small>${escapeHtml(title)}</small><strong>${escapeHtml(value)}</strong></div>`).join('');
  renderStrategyPerformance();
  const rules=[];
  if(strategyAnalysis.status&&strategyAnalysis.profileSummary)rules.push(`${strategyAnalysisIsCurrent()?'当日':'历史'}分析摘要：${strategyAnalysis.profileSummary}`);
  for(const rule of strategyAnalysis.learnedRules||[])if(rule&&!rules.includes(rule))rules.push(rule);
  if(profile.buys.length)rules.push(`买入记录平均股息率为 ${profile.buyYield.toFixed(2)}%，常见日线位置是${profile.buyDay}。`);
  if(profile.sells.length)rules.push(`卖出记录平均股息率为 ${profile.sellYield.toFixed(2)}%，继续对照4%～4.5%底线。`);
  if(profile.tRecords.length)rules.push(`已识别 ${profile.tRecords.length} 笔做T操作，常见日线位置是${profile.tDay}。`);
  if(!rules.length)rules.push(personalLoadedParts.has('strategy')?'当前私有操作记录中尚未形成可归纳规则。':'正在从当前登录账号读取私有操作记录；公开 GitHub 历史不会作为回退数据。');
  $('#learnedRules').innerHTML=rules.map((rule,i)=>`<div><b>${i+1}</b><span>${escapeHtml(rule)}</span></div>`).join('');
  const adviceHoldings=trackedStocks.map(item=>holdings.find(held=>held.code===item.code)||{code:item.code,name:item.name||item.code,shares:Number(item.payload?.shares??item.shares)||0});
  const advice=adviceHoldings.map(strategyAdviceFor).sort((a,b)=>b.priority-a.priority||b.y-a.y);
  renderBriefCommand(advice);
  $('#strategyAdvice').innerHTML=advice.length?advice.map(a=>`<article class="strategy-card ${escapeHtml(a.kind)}"><span class="strategy-action">${escapeHtml(a.action)}</span><h4>${escapeHtml(a.holding.name)}</h4><div class="stock-code">${escapeHtml(a.holding.code)}</div><div class="strategy-card-metrics"><div><small>当前正式股息率</small><b>${Number.isFinite(a.s.totalDividend)&&Number.isFinite(a.s.price)&&a.s.price>0?`${a.y.toFixed(3)}%`:'—'}</b></div><div><small>当前持仓</small><b>${Number(a.holding.shares)||0}股</b></div><div><small>日 / 周 / 月位置</small><b>${escapeHtml(a.day)} / ${escapeHtml(a.week)} / ${escapeHtml(a.month)}</b></div></div><p>${escapeHtml(a.why)}</p>${a.ai?.reason?`<small>${strategyAnalysisIsCurrent()?'当日':'历史'}分析补充：${escapeHtml(a.ai.reason)}</small>`:''}</article>`).join(''):'<p class="muted">当前没有可供分析的私有自选股票。</p>';
  const loaded=personalLoadedParts.has('strategy');
  $('#exportTradesCsv').disabled=!loaded||!count;
  $('#importTradesCsv').disabled=!loaded;
  $('#showTradeRecord').disabled=!loaded||!personalStockUniverse().length;
  $('#tradeRecordCount').textContent=loaded?`${count}条 · Supabase 私有迁移记录`:'待读取 · 登录后按需载入';
  const shown=tradeFilter==='all'?tradeRecords:tradeRecords.filter(record=>tradeFilter==='做T'?String(record.action).startsWith('做T'):record.action===tradeFilter);
  $('#tradeHistoryBody').innerHTML=shown.length?shown.map(record=>{const actionClass=record.action==='买入'?'buy':record.action==='卖出'?'sell':'t',ctx=record.context||{},positions=ctx.positions||{},historicalYield=Number(ctx.yield),yieldText=Number.isFinite(historicalYield)?`${historicalYield.toFixed(3)}%`:'—',learning=record.modelInsight||record.learning||tradeLearning(record);return `<tr><td>${escapeHtml(record.date)}</td><td><b>${escapeHtml(record.name||record.code)}</b><div class="stock-code">${escapeHtml(record.code)}</div></td><td><span class="trade-action ${actionClass}">${escapeHtml(record.action)}</span></td><td>${money(record.price)}<div class="stock-code">${Number(record.shares)}股</div></td><td><b>${yieldText}</b><div class="stock-code">${escapeHtml(ctx.asOf||record.date||'历史快照')}</div></td><td>${tradeZonePill('日',positions.day)}${tradeZonePill('周',positions.week)}${tradeZonePill('月',positions.month)}</td><td>${escapeHtml(learning)}</td><td><button class="strategy-delete" type="button" data-delete-trade="${escapeHtml(record.id)}">删除</button></td></tr>`;}).join(''):'<tr><td colspan="8" class="empty">当前筛选没有操作记录。</td></tr>';
  document.querySelectorAll('[data-delete-trade]').forEach(button=>button.onclick=()=>deleteTradeRecord(button.dataset.deleteTrade));
  const selected=$('#tradeStock').value;
  $('#tradeStock').innerHTML=personalStockUniverse().map(item=>`<option value="${escapeHtml(item.code)}">${escapeHtml(item.name)} · ${escapeHtml(item.code)}</option>`).join('');
  if([...$('#tradeStock').options].some(option=>option.value===selected))$('#tradeStock').value=selected;
}
function csvEscape(value,forceText=false){let text=String(value??'');if(forceText||/^[=+\-@]/.test(text))text=`'${text}`;return `"${text.replace(/"/g,'""')}"`;}
function exportTradeCsv(){if(!tradeRecords.length)return showMessage('暂无操作记录','当前没有可以导出的私有操作记录。');const headers=['记录ID','日期','股票代码','股票名称','操作','成交价格','成交股数','正式每股分红','历史股息率','成交日线位置','成交周线位置','成交月线位置','策略学习结果','创建时间','来源'],rows=[headers.map(value=>csvEscape(value)).join(',')];for(const record of [...tradeRecords].sort((a,b)=>String(b.date).localeCompare(String(a.date))||String(b.createdAt||'').localeCompare(String(a.createdAt||'')))){const ctx=record.context||{},positions=ctx.positions||{},zones=[positions.day?.zone||'',positions.week?.zone||'',positions.month?.zone||''],learning=record.modelInsight||record.learning||tradeLearning(record);rows.push([record.id,record.date,record.code,record.name,record.action,record.price,record.shares,record.dividendPerShare??'',ctx.yield??'',...zones,learning,record.createdAt??'',record.source??''].map((value,index)=>csvEscape(value,index===2)).join(','));}const blob=new Blob(['\ufeff'+rows.join('\r\n')],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=`我的股票操作-${new Date().toISOString().slice(0,10)}.csv`;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function parseCsv(text){const rows=[],row=[];let value='',quoted=false;for(let i=0;i<text.length;i++){const char=text[i];if(quoted){if(char==='"'&&text[i+1]==='"'){value+='"';i++;}else if(char==='"')quoted=false;else value+=char;}else if(char==='"')quoted=true;else if(char===','){row.push(value);value='';}else if(char==='\n'){row.push(value);rows.push([...row]);row.length=0;value='';}else if(char!=='\r')value+=char;}if(quoted)throw new Error('CSV存在未闭合的双引号');if(value||row.length){row.push(value);rows.push(row);}const clean=rows.filter(items=>items.some(item=>String(item).trim()));if(clean.length<2)return [];const headers=clean[0].map(x=>String(x).replace(/^\ufeff/,'').trim());return clean.slice(1).map(items=>Object.fromEntries(headers.map((header,index)=>[header,String(items[index]??'').trim()])));}
function csvPick(row,names){for(const name of names)if(row[name]!==undefined&&row[name]!=='')return row[name];return '';}
function csvStableId(parts){let hash=2166136261;for(const char of parts.join('|')){hash^=char.charCodeAt(0);hash=Math.imul(hash,16777619);}return `csv-${(hash>>>0).toString(16).padStart(8,'0')}`;}
function validateTradeCsv(text){const rows=parseCsv(text);if(!rows.length)throw new Error('CSV没有可导入的数据行');if(rows.length>5000)throw new Error('单次最多导入5000条操作');const allowed=new Set(['买入','卖出','做T买入','做T卖出']),occurrences=new Map(),seenIds=new Set(),records=[],errors=[];rows.forEach((row,index)=>{const line=index+2,dateValue=csvPick(row,['日期','date']).trim(),rawCode=csvPick(row,['股票代码','代码','code']).replace(/^['\s]+/,'').trim().toUpperCase().replace(/\.(SH|SZ)$/,'');const code=/^\d+$/.test(rawCode)?rawCode.padStart(6,'0'):rawCode,action=csvPick(row,['操作','action']).replace(/\s+/g,''),price=Number(csvPick(row,['成交价格','价格','price']).replace(/[¥,\s]/g,'')),shares=Number(csvPick(row,['成交股数','股数','shares']).replace(/[,\s]/g,'')),parsedDate=/^\d{4}-\d{2}-\d{2}$/.test(dateValue)?new Date(`${dateValue}T00:00:00Z`):null,validDate=!!parsedDate&&!Number.isNaN(parsedDate.getTime())&&parsedDate.toISOString().slice(0,10)===dateValue;if(!validDate)errors.push(`第${line}行：日期应为YYYY-MM-DD且必须是真实日期`);if(!/^\d{6}$/.test(code))errors.push(`第${line}行：股票代码应为6位数字`);if(!allowed.has(action))errors.push(`第${line}行：操作仅支持买入、卖出、做T买入、做T卖出`);if(!Number.isFinite(price)||price<=0)errors.push(`第${line}行：成交价格必须大于0`);if(!Number.isInteger(shares)||shares<=0)errors.push(`第${line}行：成交股数必须是正整数`);if(!validDate||!/^\d{6}$/.test(code)||!allowed.has(action)||!Number.isFinite(price)||price<=0||!Number.isInteger(shares)||shares<=0)return;const tuple=[dateValue,code,action,String(price),String(shares)],key=tuple.join('|'),occurrence=(occurrences.get(key)||0)+1;occurrences.set(key,occurrence);const suppliedId=csvPick(row,['记录ID','id']).trim(),id=suppliedId||csvStableId([...tuple,String(occurrence)]);if(!/^[A-Za-z0-9._:-]{1,160}$/.test(id)){errors.push(`第${line}行：记录ID格式无效`);return;}if(seenIds.has(id)){errors.push(`第${line}行：记录ID重复`);return;}seenIds.add(id);const candidate=catalog.find(item=>item.code===code),marketStock=market.stocks.find(item=>item.code===code),holding=holdings.find(item=>item.code===code),name=csvPick(row,['股票名称','股票','name'])||candidate?.name||marketStock?.name||holding?.name||code,dividendRaw=csvPick(row,['正式每股分红','每股分红','dividendPerShare']),dividend=dividendRaw===''?NaN:Number(dividendRaw.replace(/[,\s]/g,'')),yieldRaw=csvPick(row,['历史股息率','股息率','yield']),historicalYield=yieldRaw===''?NaN:Number(yieldRaw.replace(/[%\s]/g,'')),positions={day:{zone:csvPick(row,['成交日线位置','日线位置','dayZone'])},week:{zone:csvPick(row,['成交周线位置','周线位置','weekZone'])},month:{zone:csvPick(row,['成交月线位置','月线位置','monthZone'])}},cleanPositions=Object.fromEntries(Object.entries(positions).filter(([,item])=>item.zone)),createdAt=csvPick(row,['创建时间','createdAt']);records.push({id,date:dateValue,code,name,action,price,shares,dividendPerShare:Number.isFinite(dividend)?dividend:(Number.isFinite(stock(code).totalDividend)?stock(code).totalDividend:null),createdAt:createdAt&&!Number.isNaN(Date.parse(createdAt))?createdAt:new Date().toISOString(),source:'csv-import',learning:csvPick(row,['策略学习结果','learning']),context:{status:'pending',requestedDate:dateValue,...(Number.isFinite(historicalYield)?{yield:historicalYield}:{}),...(Object.keys(cleanPositions).length?{positions:cleanPositions}:{} )}});});if(errors.length)throw new Error(errors.slice(0,8).join('；')+(errors.length>8?`；另有${errors.length-8}处错误`:''));return records;}
async function importTradeCsvFile(file){if(!file)return;await requireAuth();if(file.size>5*1024*1024)throw new Error('CSV文件不能超过5MB');const imported=validateTradeCsv(await file.text()),existingIds=new Set(tradeRecords.map(r=>r.id)),fresh=imported.filter(r=>!existingIds.has(r.id));if(!fresh.length)return showMessage('没有新增记录',`CSV中的${imported.length}条操作均已存在，没有重复导入。`);if(!confirm(`CSV校验通过，共${imported.length}条，其中${fresh.length}条是新记录。确定写入当前账号的私有 Supabase 吗？`))return;await mutateTradeRecords(records=>[...fresh,...records],'private: bulk import trade records from csv');renderStrategy();showMessage('CSV批量导入完成',`已新增${fresh.length}条操作，跳过${imported.length-fresh.length}条重复记录。新记录会在后续私有复盘任务中补全历史行情。`);}
async function openTradeRecord(){try{await requireAuth();if(!personalLoadedParts.has('holdings'))await loadPersonalPart('holdings');const universe=personalStockUniverse();if(!universe.length)return showMessage('暂无股票','请先在 Part 1 添加私有自选股。');renderStrategy();$('#tradeDate').value=new Date().toISOString().slice(0,10);const code=$('#tradeStock').value||universe[0].code;$('#tradeStock').value=code;$('#tradePrice').value=Number(stock(code).price)>0?stock(code).price:'';$('#tradeShares').value='';$('#tradeRecordDialog').showModal();}catch(error){if(!/请先/.test(error.message))showMessage('暂时不能记录',error?.message||'私有记录未修改。');}}
async function deleteTradeRecord(id){if(!authSession)return openLogin();const record=tradeRecords.find(item=>String(item.id)===String(id));if(!record)return;if(!confirm(`确定从当前账号的私有操作历史删除 ${record.date||''} ${record.name||record.code||''} 吗？`))return;try{await supabaseRpc('personal_delete_trade_record',{p_source_id:String(id)});await loadPersonalPart('strategy',true);renderStrategy();showMessage('已删除','该条私有操作记录已删除，后续画像会按剩余历史重新计算。');}catch(error){showMessage('删除失败',error?.message||'私有操作记录未修改。');}}
$('#exportTradesCsv').onclick=()=>{if(!authSession)return openLogin();exportTradeCsv();};
$('#importTradesCsv').onclick=async()=>{try{await requireAuth();$('#tradeCsvFile').click();}catch(error){if(!/请先/.test(error.message))showMessage('暂时不能导入',error?.message||'私有记录未修改。');}};
$('#tradeCsvFile').onchange=async event=>{const file=event.target.files?.[0];try{await importTradeCsvFile(file);}catch(error){showMessage('CSV导入失败',error?.message||'私有记录未修改。');}finally{event.target.value='';}};
$('#showTradeRecord').onclick=openTradeRecord;
$('#refreshStrategy').onclick=async()=>{if(!authSession)return openLogin();const button=$('#refreshStrategy');button.disabled=true;try{await refreshStrategyCloud();showMessage('私有策略记录已刷新','仅重新读取当前账号的 Part 6 私有 RPC；未调用模型、行情、VPS 或交易接口。');}catch(error){showMessage('刷新失败',error?.message||'私有记录未更新。');}finally{button.disabled=false;}};
$('#cancelTradeRecord').onclick=()=>$('#tradeRecordDialog').close();
$('#tradeStock').onchange=()=>{const code=$('#tradeStock').value;$('#tradePrice').value=Number(stock(code).price)>0?stock(code).price:'';};
$('#tradeRecordForm').onsubmit=async e=>{e.preventDefault();const code=$('#tradeStock').value,universe=personalStockUniverse(),target=universe.find(item=>item.code===code),s=stock(code),price=Number($('#tradePrice').value),shares=Number($('#tradeShares').value),action=$('#tradeAction').value,dateValue=$('#tradeDate').value;if(!target||!dateValue||!Number.isFinite(price)||price<=0||!Number.isInteger(shares)||shares<=0)return showMessage('记录未保存','请检查日期、股票、成交价格和成交股数。');const position=s.positions||{},yieldRate=Number.isFinite(s.totalDividend)&&Number.isFinite(s.price)&&s.price>0?s.totalDividend/s.price*100:null,record={id:crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`,date:dateValue,code,name:target.name,action,price,shares,dividendPerShare:Number.isFinite(s.totalDividend)?s.totalDividend:null,createdAt:new Date().toISOString(),source:'dashboard-private',context:{status:'pending',requestedDate:dateValue,marketPriceAtRecord:s.price,totalDividend:s.totalDividend,...(yieldRate===null?{}:{yield:yieldRate}),...(position&&Object.keys(position).length?{positions:compactPositions(position)}:{})}};const submit=$('#tradeRecordForm button[type="submit"]');try{submit.disabled=true;submit.textContent='正在写入私有空间…';await mutateTradeRecords(records=>[record,...records],'private: add trade record');$('#tradeRecordDialog').close();renderStrategy();showMessage('操作已保存',`${target.name} ${action} ${shares}股已写入当前账号的私有历史。`);}catch(error){showMessage('记录未保存',error?.message||'私有记录未修改。');}finally{submit.disabled=false;submit.textContent='保存操作';}};
document.querySelectorAll('[data-trade-filter]').forEach(button=>button.onclick=()=>{tradeFilter=button.dataset.tradeFilter;document.querySelectorAll('[data-trade-filter]').forEach(x=>x.classList.toggle('active',x===button));renderStrategy();});

function scoreCandidate(item,q){const name=item.name.toLowerCase(),code=item.code,pinyin=item.pinyin||'',initials=item.initials||'';if(q===code||q===name||q===initials||q===pinyin)return 100;if(code.startsWith(q)||name.startsWith(q)||initials.startsWith(q)||pinyin.startsWith(q))return 80;if(code.includes(q)||name.includes(q)||initials.includes(q)||pinyin.includes(q))return 50;return 0;}
function searchCatalog(){if(!authSession||watchlistOperation)return;const q=$('#stockSearch').value.trim().toLowerCase().replace(/\s+/g,'');selectedCandidate=null;$('#selectedStock').textContent='请选择下方匹配结果';if(!q){$('#suggestions').classList.add('hidden');return;}const matches=catalog.map(item=>({item,score:scoreCandidate(item,q)})).filter(x=>x.score).sort((a,b)=>b.score-a.score||a.item.code.localeCompare(b.item.code)).slice(0,8);$('#suggestions').innerHTML=matches.length?matches.map(({item})=>`<button type="button" data-pick="${item.code}"><b>${escapeHtml(item.name)}</b><span>${item.code}.${item.market}</span><small>${escapeHtml(item.pinyin)}</small></button>`).join(''):'<p>没有找到匹配的A股</p>';$('#suggestions').classList.remove('hidden');document.querySelectorAll('[data-pick]').forEach(button=>button.onclick=()=>{if(!authSession||watchlistOperation)return;selectedCandidate=catalog.find(x=>x.code===button.dataset.pick);$('#stockSearch').value=`${selectedCandidate.name} ${selectedCandidate.code}`;$('#selectedStock').textContent=`已选择：${selectedCandidate.name} ${selectedCandidate.code}.${selectedCandidate.market}`;$('#suggestions').classList.add('hidden');});}
$('#stockSearch').oninput=searchCatalog;
$('#cancelChanges').onclick=()=>{if(!authSession||watchlistOperation)return;resetWatchlistDraft();renderHoldings();};
$('#returnToday').onclick=()=>switchTab('today');
$('#saveChanges').onclick=openWatchlistSave;
$('#confirmWatchlistSave').onclick=saveWatchlistDraft;
$('#closeWatchlistSave').onclick=()=>$('#watchlistSaveDialog').close();
$('#refreshWatchlist').onclick=refreshWatchlist;
$('#addForm').onsubmit=async e=>{e.preventDefault();try{await addWatchlistItem();}catch(error){showMessage('添加失败',error?.message||'私有自选清单未修改。');}};
async function removeCloudStock(){await requireAuth();throw new Error('请使用当前页面的私有自选清单删除按钮。');}

function initSupabaseClient(){
  if(PART0_LOCAL_ONLY_PREVIEW||!SUPABASE_URL||!SUPABASE_ANON_KEY||!window.supabase||typeof window.supabase.createClient!=='function')return null;
  try{
    supabaseClient=window.supabase.createClient(SUPABASE_URL,SUPABASE_ANON_KEY,{auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:false,flowType:'pkce'}});
    supabaseClient.auth.onAuthStateChange((event,session)=>{
      authSession=session||null;
      if(!authSession){stopPrivateAutoRefresh();currentUsername='';vpsAdmin=false;privatePortfolio=null;runtimeDisplay=null;whitelistControl=null;privateLoadState='not_loaded';privateLoadError='';holdings=[];clearPersonalData();}
      updateAuthUI();
      render();
      if(authSession&&(event==='SIGNED_IN'||event==='TOKEN_REFRESHED'))setTimeout(()=>{if(!authSession)return;if(document.visibilityState==='visible')void loadPrivateDashboard();startPrivateAutoRefresh();},0);
    });
    return supabaseClient;
  }catch(error){supabaseClient=null;privateLoadState='error';privateLoadError='auth_client_unavailable';return null;}
}
function stopPrivateAutoRefresh(){if(privateRefreshTimer!==null){clearInterval(privateRefreshTimer);privateRefreshTimer=null;}}
function startPrivateAutoRefresh(){if(privateRefreshTimer!==null||!authSession||!supabaseClient)return;privateRefreshTimer=window.setInterval(()=>{if(!authSession||document.visibilityState!=='visible')return;void loadPrivateDashboard();},PRIVATE_DASHBOARD_REFRESH_MS);}
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&authSession)void loadPrivateDashboard();});
function authErrorText(error){const code=error?.code||'';if(code==='invalid_credentials')return '用户名或密码错误';if(code==='rate_limited')return '登录尝试过于频繁，请稍后再试';if(code==='temporarily_unavailable')return '认证服务暂时不可用，请稍后再试';if(code==='auth_not_configured')return '认证配置尚未完成';return '登录未完成，请稍后重试';}
function canonicalUsername(value){const username=String(value??'').normalize('NFKC').trim().toLowerCase();return /^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])$/.test(username)?username:'';}
async function callAuthFunction(name,body){if(!SUPABASE_FUNCTIONS_BASE)throw Object.assign(new Error('auth_not_configured'),{code:'auth_not_configured'});let response;try{response=await fetch(`${SUPABASE_FUNCTIONS_BASE}/${name}`,{method:'POST',headers:{Accept:'application/json','Content-Type':'application/json'},cache:'no-store',body:JSON.stringify(body)});}catch(error){throw Object.assign(new Error('temporarily_unavailable'),{code:'temporarily_unavailable'});}let payload=null;try{payload=await response.json();}catch(error){}if(response.ok)return payload||{};const code=payload?.error||response.status===401?'invalid_credentials':response.status===429?'rate_limited':'temporarily_unavailable';throw Object.assign(new Error(code),{code});}
function rpcFailureText(name,error){const code=String(error?.code||'');if(code==='PGRST202'||code==='PGRST203')return '私有接口目录尚未刷新，请执行最新数据库修复后刷新页面';if(code==='42702')return '私有反馈服务需要执行最新前向修复；完成后请强制刷新并重新登录';if(code==='42501'||Number(error?.status)===403)return '当前账号没有这项私有操作权限';if(code==='23514')return '提交内容未通过私有数据校验';if(Number(error?.status)===401)return '登录会话已失效，请重新登录';return name.startsWith('personal_')?'私有数据接口暂时失败，请稍后重试':'请求暂时失败，请稍后重试';}
async function supabaseRpc(name,args={}){if(!supabaseClient||!authSession)throw Object.assign(new Error('auth_required'),{code:'auth_required'});const {data,error}=await supabaseClient.rpc(name,args);if(error){const rpcCode=String(error.code||error.status||'rpc_failed');throw Object.assign(new Error(rpcFailureText(name,error)),{code:'rpc_failed',rpcCode});}return data;}
function clearPersonalData(){for(const id of ['watchlistSaveDialog','messageDialog']){const dialog=document.querySelector('#'+id);if(dialog?.open)dialog.close?.();}for(const id of ['watchlistSaveDetail','suggestions','messageText']){const node=document.querySelector('#'+id);if(node)node.textContent='';}const input=document.querySelector('#stockSearch');if(input)input.value='';const hint=document.querySelector('#selectedStock');if(hint)hint.textContent='支持中文、代码、拼音；选择后加入草稿';watchlistDraft=null;watchlistBase=null;watchlistSaving=false;watchlistOperation=null;watchlistMessage='';watchlistUncertain=false;refreshHealth=null;refreshHealthReadFailed=false;personalMigrationState=null;personalLoadedParts=new Set();personalPartLoads=new Map();personalPartErrors=new Map();trackedStocks=[];part2Config={version:1,groups:[],extraStocks:[]};market={stocks:[],events:[],updatedAt:null};marketLoaded=false;newsMemory={items:[],updatedAt:null,lastScanAt:null};tradeRecords=[];strategyFeedback=[];feedbackPending=false;strategyRecommendations=[];strategyRecommendationMeta={};strategyDataTimes={};strategyAnalysis={status:'waiting',learnedRules:[],advice:[]};strategyProfile={schemaVersion:1,externalLearnedRules:[],personalBehaviorEvidence:{},fixedGuardrails:[],nonExecutablePositionSuggestions:[],conflictsAndGaps:[]};focusedStudy=null;focusedStudyNew=null;strategyApiHealth={status:'unknown',reason:'请登录后读取私有策略记录'};}
function personalRpcForPage(pageId){return {holdings:'personal_get_part1',positions:'personal_get_part2',grid:'personal_get_part4_v4',calendar:'personal_get_part4_v4',news:'personal_get_part5',strategy:'personal_get_part6'}[pageId]||'';}
function personalCount(value){const number=Number(value);return Number.isFinite(number)&&number>=0?Math.floor(number):0;}
function privateHistoryStatusHtml(pageId,label){if(!authSession)return `<div class="private-history-gate signed-out"><b>${escapeHtml(label)}已迁入私有空间</b><p>公开链接不会显示个人历史。请点击右上角“登录查看私有历史”，使用你的用户名和密码登录后自动读取。</p></div>`;if(personalLoadedParts.has(pageId))return '';if(personalPartLoads.has(pageId))return `<div class="private-history-gate loading"><b>正在读取${escapeHtml(label)}</b><p>数据仅从当前登录账号的 Supabase 私有 RPC 返回，请稍候。</p></div>`;if(personalPartErrors.has(pageId))return `<div class="private-history-gate error"><b>${escapeHtml(label)}暂未读取成功</b><p>登录会话仍保持；请再次点击本栏目重试。公开页面不会回退到旧 GitHub JSON。</p></div>`;if(personalMigrationState&&personalCount(personalMigrationState.source_files)===0)return `<div class="private-history-gate empty"><b>当前账号没有已迁入的历史数据</b><p>请确认使用的是已绑定历史内容的用户名登录。</p></div>`;return '';}
function renderPrivateHistoryStrip(){const box=$('#privateHistoryStrip');if(!box)return;if(!authSession){box.dataset.state='signed-out';box.innerHTML='<b>历史信息已私有化</b><span>公开访问不会展示原 Part 1—6 内容；请点击右上角“登录查看私有历史”。</span>';return;}const state=personalMigrationState;if(!state){box.dataset.state='unavailable';box.innerHTML='<b>私有历史状态暂未读取</b><span>Part 0 的 VPS 投影不受影响；进入 Part 1—6 会单独重试相应私有读取。</span>';return;}const sourceFiles=personalCount(state.source_files);if(!sourceFiles){box.dataset.state='empty';box.innerHTML='<b>当前账号没有迁入历史</b><span>请确认当前登录用户名是否为已绑定旧数据的账号。</span>';return;}const marketText=state.market_stock_count?` · 导入时行情 ${personalCount(state.market_stock_count)}只 · 导入时日历 ${personalCount(state.calendar_event_count)}条`:'';box.dataset.state='ready';box.innerHTML=`<b>历史已迁入当前账号</b><span>自选 ${personalCount(state.watchlist_count)} 只${marketText} · 公告 ${personalCount(state.news_count)} 条 · 操作 ${personalCount(state.trade_count)} 笔 · 反馈 ${personalCount(state.feedback_count)} 条 · 建议 ${personalCount(state.recommendation_count)} 条；点击 Part 1—6 按需查看。</span>`;}
function applyPersonalPart(pageId,data){if(pageId==='holdings'){const delta=watchlistChanges(),keepDraft=watchlistDraft!==null&&(delta.added.length||delta.removed.length||watchlistUncertain);trackedStocks=Array.isArray(data?.watchlist)?data.watchlist.filter(item=>/^\d{6}$/.test(String(item?.code||''))).map(item=>({...item,code:String(item.code),name:String(item.name||item.code)})):[];if(personalMigrationState)personalMigrationState={...personalMigrationState,watchlist_count:trackedStocks.length};renderPrivateHistoryStrip();if(!keepDraft&&!watchlistOperation)resetWatchlistDraft();}else if(pageId==='positions'){part2Config=data&&typeof data==='object'?data:{version:1,groups:[],extraStocks:[]};}else if(pageId==='grid'||pageId==='calendar'){market=data&&typeof data==='object'?data:{stocks:[],events:[]};marketLoaded=true;}else if(pageId==='news'){newsMemory=data&&typeof data==='object'?data:{items:[],updatedAt:null,lastScanAt:null};}else if(pageId==='strategy'){tradeRecords=Array.isArray(data?.trades?.records)?data.trades.records:[];strategyFeedback=Array.isArray(data?.feedback?.records)?data.feedback.records:[];strategyRecommendations=Array.isArray(data?.recommendations?.records)?data.recommendations.records:[];strategyRecommendationMeta=data?.recommendations&&typeof data.recommendations==='object'?data.recommendations:{};strategyDataTimes={trades:data?.trades?.updatedAt,feedback:data?.feedback?.updatedAt};strategyProfile=data?.profile&&typeof data.profile==='object'?data.profile:strategyProfile;strategyAnalysis=data?.analysis&&typeof data.analysis==='object'?data.analysis:strategyAnalysis;focusedStudy=data?.focused_study&&typeof data.focused_study==='object'?data.focused_study:null;focusedStudyNew=data?.focused_study_new&&typeof data.focused_study_new==='object'?data.focused_study_new:null;strategyApiHealth=data?.api_health&&typeof data.api_health==='object'?data.api_health:{status:'unknown',reason:'私有健康记录为空'};}}
async function loadPersonalMarket(force=false){if(!authSession||!supabaseClient)return false;if(marketLoaded&&!force){personalLoadedParts.add('market');return true;}if(personalPartLoads.has('market')){await personalPartLoads.get('market');return marketLoaded;}const sessionAtStart=authSession;personalPartErrors.delete('market');const request=(async()=>{try{const data=await supabaseRpc('personal_get_part4_v4');if(authSession!==sessionAtStart)return;applyPersonalPart('calendar',data);marketLoaded=true;personalPartErrors.delete('market');}catch(error){if(authSession===sessionAtStart){marketLoaded=false;personalPartErrors.set('market','private_read_failed');}}finally{personalPartLoads.delete('market');}if(authSession===sessionAtStart)render();})();personalPartLoads.set('market',request);await request;return marketLoaded;}
async function loadPersonalPart(pageId,force=false){if(pageId==='holdings'&&watchlistOperation)return;const rpc=personalRpcForPage(pageId);if(!rpc||!authSession||!supabaseClient)return;if(pageId!=='holdings'&&['positions','grid','calendar','news','strategy'].includes(pageId)&&!personalLoadedParts.has('holdings'))await loadPersonalPart('holdings');if(personalLoadedParts.has(pageId)&&!force)return;if(personalPartLoads.has(pageId))return personalPartLoads.get(pageId);const sessionAtStart=authSession;personalPartErrors.delete(pageId);const request=(async()=>{try{let data;if(pageId==='holdings'){data=await supabaseRpc('personal_get_part1');if(authSession!==sessionAtStart)return;applyPersonalPart(pageId,data);personalLoadedParts.add(pageId);await loadPersonalMarket(force);}else if(pageId==='grid'||pageId==='calendar'){if(!await loadPersonalMarket(force))throw new Error('private_market_read_failed');personalLoadedParts.add(pageId);}else{data=await supabaseRpc(rpc);if(authSession!==sessionAtStart)return;applyPersonalPart(pageId,data);personalLoadedParts.add(pageId);if(['positions','strategy'].includes(pageId)&&!await loadPersonalMarket(force))throw new Error('private_market_read_failed');}personalPartErrors.delete(pageId);}catch(error){if(authSession===sessionAtStart){personalLoadedParts.delete(pageId);personalPartErrors.set(pageId,'private_read_failed');}}finally{if(personalPartLoads.get(pageId)===request)personalPartLoads.delete(pageId);}if(authSession===sessionAtStart)render();})();personalPartLoads.set(pageId,request);render();return request;}

function openLogin(){$('#loginError').textContent='';if(!$('#loginDialog').open)$('#loginDialog').showModal();}
function updateAuthUI(){const logged=!!authSession;const personalReady=personalCount(personalMigrationState?.source_files)>0;$('#authStatus').textContent=logged?(currentUsername?`已登录：${currentUsername}`:'已登录'):'未登录';$('#authStatus').classList.toggle('logged',logged);$('#loginButton').textContent=logged?'退出登录':'登录查看私有历史';document.body.classList.toggle('authenticated',logged);if($('#authStrip'))$('#authStrip').firstElementChild.textContent=logged?(personalReady?'当前浏览器会话已保持；私有历史已迁入当前账号，点击 Part 1—6 按需读取。':'当前浏览器会话已保持；正在核对当前账号的私有历史状态。'):'公开浏览模式：为保护个人历史，Part 1—6 不显示旧数据；点击右上角登录后才可读取。';renderPrivateHistoryStrip();}
async function loginWithUsername(username,password){if(!supabaseClient)throw Object.assign(new Error('auth_not_configured'),{code:'auth_not_configured'});const normalized=canonicalUsername(username);if(!normalized||typeof password!=='string'||password.length<6)throw Object.assign(new Error('invalid_credentials'),{code:'invalid_credentials'});const session=await callAuthFunction('username-login',{username:normalized,password});if(!session?.access_token||!session?.refresh_token)throw Object.assign(new Error('temporarily_unavailable'),{code:'temporarily_unavailable'});const {error}=await supabaseClient.auth.setSession({access_token:session.access_token,refresh_token:session.refresh_token});if(error)throw Object.assign(new Error('temporarily_unavailable'),{code:'temporarily_unavailable'});}
async function requestRecovery(username){const normalized=canonicalUsername(username);if(!normalized)throw Object.assign(new Error('temporarily_unavailable'),{code:'temporarily_unavailable'});return callAuthFunction('username-recovery-request',{username:normalized});}
async function signOut(){if(supabaseClient)await supabaseClient.auth.signOut();stopPrivateAutoRefresh();authSession=null;currentUsername='';vpsAdmin=false;privatePortfolio=null;runtimeDisplay=null;whitelistControl=null;privateLoadState='not_loaded';holdings=[];clearPersonalData();updateAuthUI();render();}
async function requireAuth(){if(authSession)return authSession;openLogin();throw Object.assign(new Error('auth_required'),{code:'auth_required'});}
async function requireAdmin(){await requireAuth();if(!vpsAdmin){showMessage('权限不足','当前账号没有显式 VPS 管理员权限，不能提交白名单。');throw Object.assign(new Error('admin_required'),{code:'admin_required'});}return true;}
function applyPrivatePortfolio(value){
  const scopes=Array.isArray(value)?value:value&&typeof value==='object'?[value]:[];
  const candidate=scopes.find(item=>item&&item.scope_key==='primary');
  // The initialized Hosted envelope is not an account snapshot. Never call it empty holdings.
  privatePortfolio=candidate&&Number.isInteger(candidate.projection_sequence)&&candidate.projection_sequence>0&&Number.isFinite(Date.parse(candidate.source_generated_at))?candidate:null;
  const rows=Array.isArray(privatePortfolio?.positions)?privatePortfolio.positions:[];
  holdings=rows.filter(item=>/^\d{6}\.(SH|SZ)$/.test(String(item?.symbol||''))).map(item=>{
    const symbol=String(item.symbol).toUpperCase(),name=item.display_name||symbolDisplay(symbol);
    return {code:symbol.slice(0,6),symbol,name,shares:Number(item.held_quantity)||0,cost:item.average_cost_per_share,marketValue:item.market_value,privatePosition:item};
  });
}
function loadPrivateDashboard(){
  if(!authSession||!supabaseClient)return Promise.resolve();
  if(privateLoadInFlight)return privateLoadInFlight;
  const sessionAtStart=authSession;
  const flight=loadPrivateDashboardOnce(sessionAtStart).finally(()=>{
    if(privateLoadInFlight===flight)privateLoadInFlight=null;
    // A replaced session invalidates old results, but must not lose its read.
    if(authSession&&authSession!==sessionAtStart&&document.visibilityState==='visible')setTimeout(()=>{
      if(authSession&&document.visibilityState==='visible')void loadPrivateDashboard();
    },0);
  });
  privateLoadInFlight=flight;
  return flight;
}
async function loadPrivateDashboardOnce(sessionAtStart){
  privateLoadState='loading';privateLoadError='';render();
  try{
    const [portfolio,runtime,admin,username]=await Promise.all([supabaseRpc('vps_private_get_portfolio'),supabaseRpc('vps_private_get_runtime_display'),supabaseRpc('vps_is_admin'),supabaseRpc('app_get_current_username')]);
    if(authSession!==sessionAtStart)return;
    applyPrivatePortfolio(portfolio);runtimeDisplay=runtime&&typeof runtime==='object'?runtime:null;vpsAdmin=admin===true;currentUsername=typeof username==='string'?username:'';
    const control=vpsAdmin?await supabaseRpc('vps_get_whitelist_control_state'):null;
    if(authSession!==sessionAtStart)return;
    whitelistControl=control;privateLoadState='ready';
  }catch(error){if(authSession===sessionAtStart){privateLoadState='error';privateLoadError='private_read_failed';privatePortfolio=null;runtimeDisplay=null;whitelistControl=null;vpsAdmin=false;}}
  if(authSession!==sessionAtStart)return;
  try{const migrationState=await supabaseRpc('personal_get_migration_state');if(authSession===sessionAtStart)personalMigrationState=migrationState&&typeof migrationState==='object'?migrationState:null;}catch(error){if(authSession===sessionAtStart)personalMigrationState=null;}
  if(authSession!==sessionAtStart)return;
  try{const health=await supabaseRpc('personal_get_refresh_health');if(authSession===sessionAtStart){refreshHealth=health&&typeof health==='object'?health:null;refreshHealthReadFailed=false;}}catch(error){if(authSession===sessionAtStart)refreshHealthReadFailed=true;}
  if(authSession!==sessionAtStart)return;
  updateAuthUI();render();
  const activePage=document.querySelector('.page.active')?.id||'today';
  if(personalRpcForPage(activePage))await loadPersonalPart(activePage,true);
  else if(personalCount(personalMigrationState?.source_files)>0)await loadPersonalPart('holdings');
}
if($('#recoverLink'))$('#recoverLink').onclick=()=>{if($('#loginDialog').open)$('#loginDialog').close();$('#recoveryUsername').value=$('#loginUsername').value||'';$('#recoveryMessage').textContent='';$('#recoveryDialog').showModal();};
if($('#cancelRecovery'))$('#cancelRecovery').onclick=()=>$('#recoveryDialog').close();
if($('#recoveryForm'))$('#recoveryForm').onsubmit=async e=>{e.preventDefault();const button=$('#recoveryForm button[type="submit"]');try{button.disabled=true;const payload=await requestRecovery($('#recoveryUsername').value);$('#recoveryMessage').textContent=payload.message||'如果用户名存在，恢复邮件已发送。';}catch(error){$('#recoveryMessage').textContent='恢复服务暂时不可用，请稍后再试。';}finally{button.disabled=false;}};
$('#loginButton').onclick=async()=>{if(authSession){try{await signOut();}catch(error){showMessage('退出失败','会话退出未完成，请稍后重试。');}}else openLogin();};
$('#loginForm').onsubmit=async e=>{e.preventDefault();$('#loginError').textContent='正在安全验证…';const submit=$('#loginForm button[type="submit"]');try{submit.disabled=true;await loginWithUsername($('#loginUsername').value,$('#loginPassword').value);$('#loginPassword').value='';$('#loginDialog').close();await loadPrivateDashboard();}catch(error){$('#loginError').textContent=authErrorText(error);}finally{submit.disabled=false;}};
$('#reloadPart0Preview').onclick=async()=>{if(PART0_LOCAL_ONLY_PREVIEW){part0PreviewRenderedAt=new Date();renderTodayBoard();showMessage('本地预览已重新载入','本次只重新渲染浏览器内存中的 Part 0 界面，没有连接 Supabase、VPS、行情、模拟盘或订单接口。');return;}if(!authSession)return openLogin();const button=$('#reloadPart0Preview');button.disabled=true;try{await loadPrivateDashboard();showMessage(privateLoadState==='ready'?'回执已重新读取':'回执读取失败',privateLoadState==='ready'?'仅重新读取已有私有投影；没有有效运行回执时仍显示待接入，不表示VPS已修复。':'当前不能判断VPS实际状态；本次没有触发VPS、行情或交易接口。');}finally{button.disabled=false;}};
$('#openPart1FromPart0').onclick=()=>{if(PART0_LOCAL_ONLY_PREVIEW){showMessage('严格本地预览模式','本地模式只验证 Part 0，不载入 Part 1 数据；返回独立预览地址即可继续验证今日看板。');return;}switchTab('holdings');};
async function loadTrackedStocks(){return [];}
async function loadPart2Config(){return {version:1,groups:[],extraStocks:[]};}
function syncHoldingsWithCloud(){return;}
async function mutateTrackedStocks(){await requireAdmin();throw new Error('股票池请在 Part 0 白名单控制中提交 immutable revision。');}
async function mutatePart2Config(){await requireAuth();throw new Error('Part 2 配置尚未迁移到私有 RPC，当前不写入公开数据。');}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
$('#refreshMarket').onclick=async()=>{if(!authSession)return openLogin();const ok=await loadPersonalMarket(true);showMessage(ok?'快照已重新读取':'快照读取失败',ok?'仅重新读取 Supabase 已保存快照；没有重新采集行情。重新采集由本机采集任务完成，采集时间以表内时间为准。':'保留上次结果；本次未触发采集。');};
$('#refreshNews').onclick=async()=>{if(!authSession)return openLogin();await loadPersonalPart('news',true);showMessage(personalPartErrors.has('news')?'新闻读取失败':'新闻快照已重新读取','仅重新读取私有新闻记录；本机增量采集任务负责更新，页面不会直接调用搜索或写入 GitHub。');};
async function start(){
  if(PART0_LOCAL_ONLY_PREVIEW){updateAuthUI();render();guardPart0PreviewControls();$('#updateText').textContent='本地 Part 0 界面预览 · 未读取私有数据';return;}
  importPortfolioFromHash();
  initSupabaseClient();
  if(supabaseClient){try{const {data,error}=await supabaseClient.auth.getSession();if(!error)authSession=data.session||null;}catch(error){authSession=null;}}
  const catalogData=await fetch('data/stock-catalog.json').then(response=>response.ok?response.json():[]).catch(()=>[]);
  if(Array.isArray(catalogData))catalog=catalogData;
  clearPersonalData();
  if(authSession){await loadPrivateDashboard();startPrivateAutoRefresh();}
  updateAuthUI();render();
}
start();
