const PART0_LOCAL_ONLY_PREVIEW=new URLSearchParams(location.search).get('part0-local-preview')==='1';
const SUPABASE_CONFIG=window.STOCK_DASHBOARD_SUPABASE_CONFIG||{};
const SUPABASE_URL=String(SUPABASE_CONFIG.url||'').replace(/\/$/,'');
const SUPABASE_ANON_KEY=String(SUPABASE_CONFIG.anonKey||'');
const SUPABASE_FUNCTIONS_BASE=SUPABASE_URL?`${SUPABASE_URL}/functions/v1`:'';
let supabaseClient=null,authSession=null,currentUsername='',vpsAdmin=false,privatePortfolio=null,runtimeDisplay=null,whitelistControl=null,privateLoadState='not_loaded',privateLoadError='',privateRefreshTimer=null,privateLoadInFlight=false;
let personalMigrationState=null,personalLoadedParts=new Set(),personalPartLoads=new Map(),personalPartErrors=new Map();
const PRIVATE_DASHBOARD_REFRESH_MS=15*60*1000;
let market={stocks:[],events:[],updatedAt:null},marketLoaded=false;
let newsMemory={items:[],updatedAt:null,lastScanAt:null};
let catalog=[],trackedStocks=[];
let part2Config={version:1,groups:[],extraStocks:[]};
let bollSettings={buyStep:.5,buyCount:4,sellStep:.5,sellCount:4,lowDev:.5,highDev:.5,yieldDev:.25};
let bollFilter='全部',bollSort='yield',bollCollapsed=new Set();
let holdings=[],overrides={},selectedCandidate=null;
let viewMonth=new Date(),selectedDate=new Date().toISOString().slice(0,10);
let tradeRecords=[],strategyRecommendations=[],strategyAnalysis={status:'waiting',learnedRules:[],advice:[]},strategyProfile={schemaVersion:1,externalLearnedRules:[],personalBehaviorEvidence:{},fixedGuardrails:[],nonExecutablePositionSuggestions:[],conflictsAndGaps:[]},focusedStudy=null,focusedStudyNew=null,strategyApiHealth={status:'unknown',reason:'私有策略记录尚未接入'};
let strategyFeedback=[];
let feedbackPending=false;
let tradeFilter='all';
const $=s=>document.querySelector(s);
const money=n=>Number.isFinite(+n)?`¥${(+n).toFixed(2)}`:'—';
const costMoney=n=>Number.isFinite(+n)?`¥${(+n).toFixed(3)}`:'—';
const escapeHtml=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const formatTime=v=>v?new Date(String(v).replace(' ','T')).toLocaleString('zh-CN',{hour12:false}):'—';

/*
 * Part 0 is deliberately local-UI-only in this phase.  Do not add Supabase,
 * VPS, broker, provider, account, or secret-bearing fetches here.  The hosted
 * gate will later replace the safe public state with an authenticated,
 * RLS-scoped adapter that only projects the approved vps_* fields.
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
function renderPart0RevisionCards(model){const local=model.isLocalPreview,privateModel=isPrivatePart0Model(model);let cards;if(local)cards=[['目标白名单',`${model.desiredRevision.symbolCount} 条 · 固定布局示例`,'仅用于本地布局，不代表实际白名单','本地布局示例'],['VPS 已激活白名单','未连接',model.activeRevision.status,'等待回执'],['私有访问','未启用','仅在 Supabase RLS 授权成功后读取获准脱敏字段','本地预览']];else if(!privateModel)cards=[['目标白名单','私有数据未公开','公开页面不展示真实目标列表；等待管理员授权后的脱敏投影','公开安全状态'],['VPS 已激活白名单','私有数据未公开','只以 VPS 回执为准；公开页面暂不展示','待授权'],['私有访问','等待管理员登录','认证后仍需显式 scope membership 才能读取持仓','未登录']];else{const desiredNo=whitelistControl?.desired_revision_no,desiredSymbols=Array.isArray(whitelistControl?.desired_symbols)?whitelistControl.desired_symbols:[],activeNo=whitelistControl?.active_revision_no,activeSymbols=Array.isArray(whitelistControl?.active_symbols)?whitelistControl.active_symbols:[],portfolioLabel=privatePortfolio?'持仓投影已接收':'未接收持仓投影';cards=[['目标白名单',desiredNo?`revision ${desiredNo} · ${desiredSymbols.length}条`:'暂无目标版本',whitelistControl?.desired_status?`状态：${whitelistControl.desired_status}；提交不等于激活`:'管理员尚未提交目标版本',desiredNo?'私有目标状态':'无目标'],['VPS 已激活白名单',activeNo?`revision ${activeNo} · ${activeSymbols.length}条`:'暂无已激活版本',activeNo?'只以 VPS activation/ACK 证据为准':'等待合法周期完成激活','VPS 回执状态'],['私有访问',portfolioLabel,privatePortfolio?'仅展示认证 RPC 允许的持仓字段；金额由数据库计算':'当前账户投影为空或尚未授予 primary scope membership',privatePortfolio?'已授权':'已登录 · 待授权']];}$('#part0RevisionCards').innerHTML=cards.map(([title,value,detail,label])=>`<article class="part0-revision-card"><small>${escapeHtml(title)}</small><strong>${escapeHtml(value)}</strong><span>${escapeHtml(detail)}</span><em class="revision-label">${escapeHtml(label)}</em></article>`).join('');if($('#manageWhitelist'))$('#manageWhitelist').disabled=!privateModel||!vpsAdmin;}
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
  const local=model.isLocalPreview;
  const privateModel=isPrivatePart0Model(model);
  const midpoint=Math.ceil(model.whitelist.length/2);
  const columns=[model.whitelist.slice(0,midpoint),model.whitelist.slice(midpoint)];
  const column=items=>`<div class="universe-half"><div class="universe-head"><span>股票 / 代码</span><span>${local?'状态样式':'当前状态'}</span></div>${items.map(item=>`<div class="universe-row"><span class="universe-stock"><b>${escapeHtml(item.displayName||item.display_name||symbolDisplay(item.symbol))}</b><span>${escapeHtml(item.symbol)}</span></span>${local?part0StateTag(item.previewState):privateStatusTag(item)}</div>`).join('')}</div>`;
  const whitelistBody=model.whitelist.length
    ?`<div class="universe-columns">${columns.map(column).join('')}</div>`
    :privateModel
      ?`<div class="monitor-empty"><b>${vpsAdmin?'暂无已激活白名单':'管理员白名单未公开'}</b><span>${vpsAdmin?'只有状态为 active 的 VPS 版本会出现在这里；目标提交不等于激活。':'白名单控制状态仅对 VPS 管理员开放。'}</span></div>`
      :'<div class="monitor-empty"><b>真实白名单未公开</b><span>管理员授权后，页面只接收经过字段投影的 VPS 状态。</span></div>';
  const countLabel=local?`${model.whitelist.length} 条固定布局示例`:privateModel?`${model.whitelist.length} 条已激活成员`:'私有列表未公开';
  const activeText=local
    ?'目标版本：固定布局示例；VPS 激活版本：未连接。'
    :privateModel
      ?`目标 revision ${model.desiredRevisionNo||'—'} · VPS active revision ${model.activeRevisionNo||'—'}；两者分开显示。`
      :'目标版本与 VPS 激活版本：私有数据未公开。';
  const manageLabel=privateModel&&vpsAdmin?'添加/删除':'白名单管理';
  $('#part0MonitorGrid').innerHTML=`<article class="whitelist-card"><div class="monitor-card-top"><div><h4>VPS 监控股票白名单${local?' · 固定布局示例':''}</h4><p>策略范围独立于 Part 1；${local?'本地只展示人工构造的布局样例。':privateModel?'仅显示已激活版本；每行状态来自脱敏运行投影。':'公开页面不展示真实股票或账户数据。'}</p></div><div class="monitor-card-top-right"><span class="count-badge">${escapeHtml(countLabel)}</span><span class="independent-badge">独立于 Part 1</span></div></div><div class="whitelist-intro"><b>${local?'固定布局示例：':privateModel?'VPS 已激活版本：':'公开安全状态：'}</b>${local?'22 条示例仅用于检查双栏、状态样式和响应式布局，非实际白名单、非当日策略信号。':privateModel?'目标 revision、active revision 和 VPS 回执状态严格分开；提交或同步中不显示为已激活。':'真实白名单仅在管理员授权后以获准脱敏字段投影，不能从公开页面推断。'}</div>${whitelistBody}<div class="whitelist-footer"><span>${escapeHtml(activeText)}</span><button class="ghost protected" type="button" id="manageWhitelist" ${!privateModel||!vpsAdmin?'disabled':''}>${escapeHtml(manageLabel)}</button></div></article><article class="sim-card"><div class="monitor-card-top"><div><h4>模拟盘持仓摘要${local?' · 本地布局示例':''}</h4><p>${local?'本地 UI 只检查空状态布局，不读取账户。':privateModel?'只显示认证 RPC 允许的持仓字段；金额由数据库计算。':'登录后才读取获准的私有投影。'}</p></div><span class="independent-badge">${local?'预览':privateModel?'只读':'未授权'}</span></div>${renderPart0SimPositions(model.simPositions||[],model)}<div class="sim-footer"><span>${privateModel&&privatePortfolio?`投影序号：${privatePortfolio.projection_sequence??'—'}`:'不展示账户标识、订单或 Provider 原始响应'}</span><b>${local?'非实际账户':privateModel?'DRY_RUN · 只读':'等待授权'}</b></div></article>`;
  const manage=$('#manageWhitelist');
  if(manage)manage.onclick=openWhitelistEditor;
}
function currentPart0Model(){
  if(PART0_LOCAL_ONLY_PREVIEW)return PART0_LOCAL_PREVIEW;
  if(!authSession)return PART0_SAFE_PUBLIC;
  const runtime=runtimeDisplay?.runtime||{};
  const activeSymbols=Array.isArray(whitelistControl?.active_symbols)?whitelistControl.active_symbols:[];
  const events=Array.isArray(runtimeDisplay?.events)
    ?runtimeDisplay.events.map(item=>({time:item.occurred_at||'—',message:item.message||'已记录脱敏运行事件。',note:item.event_code||'runtime_event',severity:item.severity}))
    :[];
  return {source:'private-authenticated',isLocalPreview:false,isPrivate:true,desiredRevisionNo:whitelistControl?.desired_revision_no||null,activeRevisionNo:whitelistControl?.active_revision_no||null,whitelist:activeSymbols.map(symbol=>({symbol,displayName:symbolDisplay(symbol),status_key:'active'})),simPositions:privatePortfolio?.positions||[],runtime:{...runtime,mode:runtime.mode||'DRY_RUN',healthStatus:runtime.health_status||'unknown'},events:events.length?events:[{time:'私有',message:'尚未接收到脱敏运行事件。',note:'等待 VPS 周期'}]};
}
function renderPart0Runtime(model){
  const prefix=model.isLocalPreview?'本地预览':isPrivatePart0Model(model)?'私有运行投影':'公开安全状态';
  const r=model.runtime||{};
  const observed=key=>r[key]?'已观察':'未观察';
  const time=key=>r[key]?formatTime(r[key]):'未观察';
  const cards=model.isLocalPreview
    ?[['09:00 自检','未执行',`${prefix}不访问 VPS`],['行情快照','未读取',`${prefix}不触发行情 Provider`],['指标计算','未读取',`${prefix}不计算 BOLL / MA250`],['状态持久化','未接入',`${prefix}不写入 Supabase`],['15 分钟策略周期','未启用','本地页面不改变正式周期']]
    :isPrivatePart0Model(model)
      ?[['09:00 自检',observed('last_strategy_cycle_at'),`最近周期：${time('last_strategy_cycle_at')}`],['行情快照',observed('last_quote_snapshot_at'),`最近行情：${time('last_quote_snapshot_at')}`],['指标计算',observed('last_strategy_cycle_at'),`策略周期：${time('last_strategy_cycle_at')}`],['状态持久化',observed('generated_at'),`运行投影：${time('generated_at')}`],['15 分钟策略周期',observed('last_strategy_cycle_at'),`当前模式：${r.mode||'DRY_RUN'}`]]
      :[['09:00 自检','未执行',`${prefix}不访问 VPS`],['行情快照','未读取',`${prefix}不触发行情 Provider`],['指标计算','未读取',`${prefix}不计算 BOLL / MA250`],['状态持久化','未接入',`${prefix}不写入 Supabase`],['15 分钟策略周期','未启用','公开页面不改变正式周期']];
  $('#part0RuntimeGrid').innerHTML=cards.map(([title,value,note])=>`<article class="runtime-card"><div class="runtime-card-head"><i class="runtime-check"></i>${escapeHtml(title)}</div><div class="runtime-value">${escapeHtml(value)}</div><div class="runtime-note">${escapeHtml(note)}</div><div class="runtime-progress"><i style="width:${value==='已观察'?'100':'0'}%"></i></div></article>`).join('');
}
function renderPart0Log(model){
  const events=Array.isArray(model.events)?model.events:[];
  $('#part0RunLog').innerHTML=events.map(item=>`<div class="log-row"><i class="${item.severity==='error'?'log-bad':'log-good'}"></i><span class="log-time">${escapeHtml(item.time)}</span><span class="log-event">${escapeHtml(item.message)}</span><span class="log-note">${escapeHtml(item.note)}</span></div>`).join('')||'<p class="muted">暂无可显示的脱敏运行事件。</p>';
}
function renderTodayBoard(){
  const page=$('#today');
  if(!page)return;
  const model=currentPart0Model();
  const local=model.isLocalPreview;
  const privateModel=isPrivatePart0Model(model);
  const runtime=model.runtime||{};
  const health=privateModel&&privateLoadState==='ready'?(runtime.healthStatus||'unknown'):'unknown';
  const privateBanner=privateModel
    ?`<div><b>私有控制面</b>：当前通过 Supabase 用户会话读取获准脱敏投影；已登录且页面可见时每15分钟自动重新读取私有 RPC，不会调用 VPS、行情或交易接口，也不会把私有持仓写入 GitHub 或浏览器本地存储。<span>${privateLoadState==='error'?'私有状态暂时未更新，请稍后重试。':`用户名：${escapeHtml(currentUsername||'已登录')}`}</span></div>`
    :`<div><b>公开安全状态</b>：当前不渲染真实 VPS 白名单、模拟盘持仓或运行明细；私有数据须经用户名/密码会话、RLS 和字段投影后才可显示。<span>公开页面不触发私有读取。</span></div>`;
  page.dataset.part0Mode=model.source;
  $('#part0PreviewBanner').innerHTML=local?`<div><b>本地 UI 预览</b>：当前只渲染 22 条固定布局示例（非实际白名单/非当日策略信号）和获准展示边界，未连接 Supabase、VPS、行情、模拟盘或订单接口。<span>界面渲染时间：${escapeHtml(part0PreviewRenderedLabel())}</span></div>`:privateBanner;
  const healthTitle=local?'本地布局预览':privateModel?(privateLoadState==='error'?'私有状态读取失败':health==='ok'?'数据链路已观察':'数据链路待核对'):'等待私有数据接入';
  const healthNote=local?'固定样例不代表数据链路健康':privateModel?`VPS 运行健康：${health}`:'当前公开页面状态不可视为数据链路健康';
  const healthTime=privateModel&&runtime.generated_at?`最近投影：${formatTime(runtime.generated_at)}`:local?'本地预览不触发 VPS 读取':'登录后读取获准运行投影';
  $('#part0Health').innerHTML=`<div class="monitor-health-main"><span class="monitor-dot"></span><span><b>${escapeHtml(healthTitle)}</b><small>${escapeHtml(healthNote)}</small></span></div><span class="monitor-health-divider"></span><div class="monitor-health-check"><strong>${escapeHtml(privateModel?`最近运行：${healthTime}`:'09:00 自检：未执行')}</strong>${escapeHtml(local?'本地预览不触发 VPS 读取':privateModel?'运行状态仅来自 VPS 脱敏投影，不由公开浏览器触发':'后续由 VPS 脱敏运行快照提供，不由公开浏览器触发')}</div><span class="dry-run">${escapeHtml(runtime.mode||'DRY_RUN')} · ${local?'预览':privateModel?'私有':'未连接'}</span>`;
  if($('#part0ControlAccess'))$('#part0ControlAccess').textContent=local?'固定布局示例 · 私有数据未接入':privateModel?(vpsAdmin?'管理员控制面 · 持仓 scope 独立授权':'已登录 · 持仓 scope 独立授权'):'公开安全状态 · 私有数据待授权接入';
  if($('#part0MonitorIntro'))$('#part0MonitorIntro').textContent=local?'VPS 独立股票池与模拟盘摘要分开显示；22 条固定布局示例仅用于本地 UI 检查。':privateModel?'VPS 独立股票池、模拟盘持仓和运行投影分开读取；只显示当前 RPC 允许的字段。':'公开页面不展示真实股票或账户数据。';
  if($('#part0TargetCount'))$('#part0TargetCount').textContent=local?`目标白名单：${model.whitelist.length} 条固定布局示例`:privateModel?(vpsAdmin?`目标白名单：${whitelistControl?.desired_symbols?.length||0} 条，已激活：${model.whitelist.length} 条`:'目标白名单：管理员可见'):'目标白名单：登录后显示';
  if($('#part0PreviewKey'))$('#part0PreviewKey').textContent=local?'固定布局示例':privateModel?'私有脱敏投影':'公开安全状态';
  if($('#part0SafetyNote'))$('#part0SafetyNote').textContent=local?'本地预览不载入受限账户、交易字段或 Provider 原始响应。':privateModel?'私有页面只展示认证 RPC 允许的脱敏字段；删除白名单标的不等于卖出或删除模拟盘持仓。':'公开页面不展示账户、交易或 Provider 原始内容，也不包含密钥；登录后仍需显式 scope membership 才能读取私有投影。';
  renderPart0RevisionCards(model);
  renderPart0Monitor(model);
  renderPart0Runtime(model);
  renderPart0Log(model);
}
function save(){
  // Private positions and costs are never persisted by the page. They are
  // derived from the authenticated Supabase projection on each load.
}
function importPortfolioFromHash(){
  if(location.hash.startsWith('#portfolio='))history.replaceState(null,'',location.pathname+location.search);
}
function syncTradeRecordsToHoldings(){return;}
function stock(code){const auto=market.stocks.find(s=>s.code===code)||{};const o=overrides[code]||{};const annual=o.annual??auto.annualDividend??0,interim=o.interim??auto.interimDividend??0,price=o.price??auto.price??0;return {...auto,...o,code,name:auto.name||holdings.find(h=>h.code===code)?.name||(part2Config.extraStocks||[]).find(h=>h.code===code)?.name||code,annualDividend:+annual,interimDividend:+interim,price:+price,totalDividend:+annual+ +interim,manual:!!overrides[code]};}

function sortedHoldingsByMarketValue(){return [...holdings].sort((a,b)=>{const aValue=Number(a.marketValue??a.privatePosition?.market_value??0),bValue=Number(b.marketValue??b.privatePosition?.market_value??0);return bValue-aValue||a.code.localeCompare(b.code);});}
function showMessage(title,text){$('#messageTitle').textContent=title;$('#messageText').textContent=text;$('#messageDialog').showModal();}
function switchTab(id){const personalPart=!!personalRpcForPage(id);document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===id));document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id===id));if(id==='today')renderTodayBoard();if(id==='holdings')renderHoldings();if(id==='positions')renderPositions();if(id==='grid')renderGrid();if(id==='calendar')renderCalendar();if(id==='news')renderNews();if(id==='strategy')renderStrategy();if(personalPart){if(authSession)void loadPersonalPart(id);else openLogin();}}
function guardPart0PreviewControls(){if(!PART0_LOCAL_ONLY_PREVIEW)return;const allowed=new Set(['reloadPart0Preview','openPart1FromPart0']);document.querySelectorAll('button,input,select').forEach(control=>{if(control.classList.contains('tab')||allowed.has(control.id)||control.closest('#messageDialog'))return;control.disabled=true;control.title='严格本地预览不会执行网页数据操作';});}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
$('#reloadPart0Preview').onclick=()=>{if(!PART0_LOCAL_ONLY_PREVIEW)return;part0PreviewRenderedAt=new Date();renderTodayBoard();showMessage('本地预览已重新载入','本次只重新渲染浏览器内存中的 Part 0 界面，没有连接 Supabase、VPS、行情、模拟盘或订单接口。');};
$('#openPart1FromPart0').onclick=()=>{if(PART0_LOCAL_ONLY_PREVIEW){showMessage('严格本地预览模式','本地模式只验证 Part 0，不载入 Part 1 数据；返回独立预览地址即可继续验证今日看板。');return;}switchTab('holdings');};

function render(){renderTodayBoard();renderHoldings();renderPositions();renderGrid();renderCalendar();renderNews();renderStrategy();const d=market.updatedAt?new Date(market.updatedAt):null;$('#updateText').textContent=d?`私有行情快照 · ${d.toLocaleString('zh-CN',{hour12:false})}`:'私有快照 · 登录后读取';}
function renderHoldings(){
  const historyState=privateHistoryStatusHtml('holdings','Part 1 自选股历史');
  const valueLabel=(value,kind='money')=>value===null||value===undefined||value===''?'—':kind==='cost'?costMoney(value):money(value);
  if(historyState){
    $('#holdingList').innerHTML=historyState;
    $('#summaryCards').innerHTML=[['自选股票','待读取'],['当前持仓','待读取'],['正式分红','待读取'],['行情覆盖','待读取']].map(([title,value])=>`<div class="summary-card"><small>${escapeHtml(title)}</small><strong>${escapeHtml(value)}</strong></div>`).join('');
    return;
  }
  const numeric=value=>value===null||value===undefined||value===''||!Number.isFinite(Number(value))?null:Number(value);
  const positionsByCode=new Map((privatePortfolio?.positions||[]).map(item=>[String(item.symbol||item.code||'').slice(0,6),item]));
  const watchlist=[...trackedStocks].sort((a,b)=>String(a.code).localeCompare(String(b.code)));
  let totalDividend=0,dividendReady=0,heldCount=0;
  const rows=watchlist.map(item=>{
    const code=String(item.code||'');
    const payload=item.payload&&typeof item.payload==='object'?item.payload:item;
    const p=positionsByCode.get(code)||{};
    const s=stock(code);
    const name=item.name||s.name||symbolDisplay(code)||code;
    const shares=numeric(p.held_quantity??payload.shares??payload.quantity??item.shares);
    const cost=numeric(p.average_cost_per_share??payload.cost??item.cost);
    const price=numeric(p.current_unadjusted_price??s.price);
    const marketValue=numeric(p.market_value)??(price!==null&&shares!==null?price*shares:null);
    const dividendPerShare=numeric(s.totalDividend);
    const annualDividend=dividendPerShare!==null&&shares!==null?dividendPerShare*shares:null;
    const yieldRate=dividendPerShare!==null&&price>0?dividendPerShare/price*100:null;
    const pnl=numeric(p.unrealized_pnl);
    const pnlPct=numeric(p.unrealized_pnl_pct);
    if(annualDividend!==null){totalDividend+=annualDividend;dividendReady++;}
    if(shares!==null&&shares>0)heldCount++;
    const avatar=name.slice(0,1)||'股';
    const rightValue=annualDividend===null?'—':money(annualDividend);
    const rightNote=annualDividend===null?'正式分红待补充':'按正式分红口径';
    const holdingLabel=shares===null?'未录入':`${shares}股`;
    const valueText=marketValue===null?'—':money(marketValue);
    const costText=cost===null?'—':costMoney(cost);
    const yieldText=yieldRate===null?'—':`${yieldRate.toFixed(2)}%`;
    const pnlText=pnl===null?'—':`${money(pnl)}${pnlPct===null?'':` · ${pnlPct.toFixed(2)}%`}`;
    const sourceLabel=p.position_state||p.data_status||'行情快照';
    return `<article class="holding"><div class="holding-top"><div class="identity"><span class="avatar">${escapeHtml(avatar)}</span><div><div class="stock-name">${escapeHtml(name)}</div><div class="stock-code">${escapeHtml(code)} · A股</div></div></div><div class="holding-money"><strong>${rightValue}</strong><small>${escapeHtml(rightNote)}</small></div></div><div class="holding-metrics"><div><small>持仓</small><b>${holdingLabel}</b></div><div><small>市值</small><b>${valueText}</b></div><div><small>成本</small><b>${costText}</b></div><div><small>股息率</small><b class="orange">${yieldText}</b></div></div><div class="holding-bottom"><span class="muted">现价 ${valueLabel(price)} · ${escapeHtml(sourceLabel)}</span><div class="holding-actions"><button class="text-danger protected" type="button" data-delete-watchlist="${escapeHtml(code)}">删除自选</button></div></div>${pnl!==null?`<div class="holding-private-note">模拟盘浮动盈亏：${escapeHtml(pnlText)} · 只读投影</div>`:''}</article>`;
  }).join('');
  const untrackedPositions=[...(privatePortfolio?.positions||[])].filter(item=>!watchlist.some(stockItem=>String(stockItem.code)===String(item.symbol||item.code||'').slice(0,6)));
  const extra=untrackedPositions.length?`<section class="legacy-watchlist"><div class="legacy-watchlist-head"><div><b>模拟盘中但不在自选清单的持仓</b><small>仅显示 VPS 私有投影；不会自动加入自选清单</small></div><span>${untrackedPositions.length}只</span></div><div class="legacy-watchlist-grid">${untrackedPositions.map(item=>`<span><b>${escapeHtml(item.display_name||item.symbol||item.code||'—')}</b><small>${escapeHtml(item.symbol||item.code||'—')}</small></span>`).join('')}</div></section>`:'';
  $('#holdingList').innerHTML=watchlist.length?rows+extra:'<p class="muted">当前账号没有私有自选股票。</p>';
  const marketCovered=watchlist.filter(item=>{const s=stock(item.code);return numeric(s.price)!==null||numeric(s.totalDividend)!==null;}).length;
  $('#summaryCards').innerHTML=[['自选股票',`${watchlist.length}只`],['当前持仓',`${heldCount}只`],['正式分红',dividendReady?money(totalDividend):'待补充'],['行情覆盖',`${marketCovered}/${watchlist.length}只`]].map(([title,value])=>`<div class="summary-card"><small>${escapeHtml(title)}</small><strong>${escapeHtml(value)}</strong></div>`).join('');
  $('#showAdd').disabled=!authSession;
  $('#showAdd').title=authSession?'添加只修改当前账号的私有自选清单，不会修改 VPS 白名单或模拟盘持仓。':'请先登录后添加私有自选股。';
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
function gridTargetCell(s,rate){const dividend=Number(s.totalDividend),price=Number(s.price);if(!(dividend>0)&&!(price>0))return '<td class="grid-target"><span class="muted">待正式数据</span></td>';if(!(dividend>0))return '<td class="grid-target"><span class="muted">待分红</span></td>';const target=dividend/(rate/100),delta=price>0?(target/price-1)*100:null;const note=delta===null?'当前价待更新':Math.abs(delta)<0.01?'已达':`${delta>=0?'需涨':'需跌'} ${Math.abs(delta).toFixed(1)}%`;return `<td class="grid-target"><strong>${money(target)}</strong><small>${escapeHtml(note)}</small></td>`;}
function renderGrid(){
  const historyState=privateHistoryStatusHtml('grid','Part 3 股息率价格网格');
  if(historyState){$('#gridSummary').innerHTML='';$('#gridBody').innerHTML=`<tr><td colspan="9">${historyState}</td></tr>`;return;}
  const universe=personalStockUniverse();
  const items=universe.map(item=>{const s=stock(item.code);return {...item,...s};});
  const validItems=items.filter(item=>Number(item.price)>0||Number(item.totalDividend)>0);
  $('#gridSummary').innerHTML=[['自选股票',`${items.length}只`],['行情覆盖',`${validItems.length}/${items.length}只`],['目标档位','5.0%—7.0%'],['数据时间',market.updatedAt?formatTime(market.updatedAt):'待读取']].map(([title,value])=>`<div class="summary-card"><small>${escapeHtml(title)}</small><strong>${escapeHtml(value)}</strong></div>`).join('');
  $('#gridBody').innerHTML=items.length?items.map(s=>{const parts=[];if(Number(s.annualDividend)>0)parts.push(`年报 ${Number(s.annualDividend).toFixed(4)}`);if(Number(s.interimDividend)>0)parts.push(`中报 ${Number(s.interimDividend).toFixed(4)}`);const currentYield=Number(s.price)>0?Number(s.totalDividend)/Number(s.price)*100:null;return `<tr><td><b>${escapeHtml(s.name||s.code)}</b><div class="stock-code">${escapeHtml(s.code)}</div></td><td><span>${escapeHtml(parts.join(' + ')||'待正式分红')}</span><small class="source-pill">正式 · 私有快照</small></td><td><b>${Number(s.totalDividend)>0?Number(s.totalDividend).toFixed(4):'—'}</b><div class="stock-code">元/股</div></td><td><b>${Number(s.price)>0?money(s.price):'—'}</b><div class="current-yield">${currentYield===null?'—':`${currentYield.toFixed(3)}%`}</div></td>${[5,5.5,6,6.5,7].map(rate=>gridTargetCell(s,rate)).join('')}</tr>`;}).join(''):'<tr><td colspan="9" class="muted">当前账号没有私有自选股票。</td></tr>';
}
function normalizedPart2Groups(){const groups=Array.isArray(part2Config.groups)?part2Config.groups:[],assigned=new Set(groups.flatMap(g=>g.codes||[])),other=groups.find(g=>g.name==='其他');for(const h of holdings)if(!assigned.has(h.code)){if(!other){groups.push({name:'其他',hydropower:false,codes:[h.code]});assigned.add(h.code);}else if(!other.codes.includes(h.code)){other.codes.push(h.code);assigned.add(h.code);}}return groups;}
function bollItems(){const holdingCodes=new Set(holdings.map(h=>h.code)),extras=new Map((part2Config.extraStocks||[]).map(s=>[s.code,s])),all=new Map();for(const h of holdings)all.set(h.code,{code:h.code,name:h.name,holding:true});for(const item of trackedStocks)if(!all.has(item.code))all.set(item.code,{code:item.code,name:item.name,holding:false,legacyWatchlist:true});for(const e of extras)if(!all.has(e[0]))all.set(e[0],{...e[1],holding:false});const groupBy=new Map();for(const group of normalizedPart2Groups())for(const code of group.codes||[])if(!groupBy.has(code))groupBy.set(code,group);return [...all.values()].map(item=>{const s=stock(item.code),group=groupBy.get(item.code)||{name:'其他',hydropower:false};return {...item,...s,group:group.name,hydropower:!!group.hydropower,holding:holdingCodes.has(item.code)};});}
function bollYield(s){return s.price?s.totalDividend/s.price*100:0;}
function bollLevels(s){const buyStart=s.hydropower?4:5,sellStart=s.hydropower?3:4;return {sell:Array.from({length:bollSettings.sellCount},(_,i)=>sellStart-i*bollSettings.sellStep).filter(x=>x>0).reverse(),buy:Array.from({length:bollSettings.buyCount},(_,i)=>buyStart+i*bollSettings.buyStep)};}
function bollSignal(s,type){const y=bollYield(s),b=s.weeklyBoll,buy=(s.hydropower?4:5)-bollSettings.yieldDev,sell=(s.hydropower?3:4)+bollSettings.yieldDev;if(type==='持仓')return s.holding;if(type==='买点下轨')return !!b&&y>=buy&&s.price<=b.lower*(1+bollSettings.lowDev/100);if(type==='卖点上轨')return !!b&&y<=sell&&s.price>=b.upper*(1-bollSettings.highDev/100);if(type==='近下轨')return !!b&&Math.abs((s.price-b.lower)/b.lower*100)<=bollSettings.lowDev;if(type==='近上轨')return !!b&&Math.abs((s.price-b.upper)/b.upper*100)<=bollSettings.highDev;return type==='全部'||s.group===type;}
function bollTrack(value,current,label){if(!value)return '<span class="muted">待更新</span>';const delta=(value/current-1)*100;return `<strong>${money(value)}</strong><small class="${delta>=0?'boll-up':'boll-down'}">${delta>=0?'+':''}${delta.toFixed(2)}%</small>`;}
function bollTargetCell(s,y,type,index){if(!s.totalDividend||!s.price)return `<td class="boll-target ${type}"><div class="boll-target-box"><strong>—</strong><small>待正式分红</small></div></td>`;const target=s.totalDividend/(y/100),currentY=bollYield(s),reached=type==='buy'?currentY>=y:currentY<=y,delta=(target/s.price-1)*100,level=Math.min(index,3);return `<td class="boll-target ${type} ${reached?'reached':''} level-${level}"><div class="boll-target-box"><strong>${money(target)}</strong><small>${reached?'已达':`${delta>=0?'需涨':'需跌'} ${Math.abs(delta).toFixed(1)}%`}</small></div></td>`;}
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
function bollRow(s){const b=s.weeklyBoll||{},levels=bollLevels(s),yieldRate=bollYield(s),cells=[...levels.sell.map((y,i)=>bollTargetCell(s,y,'sell',i)),...levels.buy.map((y,i)=>bollTargetCell(s,y,'buy',i))].join('');return `<tr><td><b>${escapeHtml(s.name)}</b>${s.holding?'<span class="boll-holding">持仓</span>':''}<div class="stock-code">${s.code}</div></td><td><strong>${money(s.price)}</strong></td><td class="boll-track boll-track-up">${bollTrack(b.upper,s.price,'上轨')}</td><td class="boll-track boll-track-mid">${bollTrack(b.middle,s.price,'中轨')}</td><td class="boll-track boll-track-down">${bollTrack(b.lower,s.price,'下轨')}</td>${bollVisual(s)}<td class="boll-yield"><strong>${yieldRate.toFixed(2)}%</strong></td><td><strong>${Number(s.totalDividend||0).toFixed(3)}</strong>${s.manual?'<span class="source-pill">手动</span>':''}</td>${cells}</tr>`;}
function renderBollChips(){const names=normalizedPart2Groups().map(g=>g.name);const labels=['全部','★ 持仓','买点下轨','卖点上轨','近下轨','近上轨',...names,'✎ 编辑'];$('#bollChips').innerHTML=labels.map(label=>`<button class="boll-chip ${bollFilter===label.replace('★ ','')?'active':''}" data-boll-filter="${escapeHtml(label)}">${escapeHtml(label)}</button>`).join('');document.querySelectorAll('[data-boll-filter]').forEach(button=>button.onclick=()=>{const value=button.dataset.bollFilter;if(value==='✎ 编辑')return openBollManage();bollFilter=value.replace('★ ','');renderBollGrid();});}
function renderBollGrid(){if(!$('#bollGroups'))return;const historyState=privateHistoryStatusHtml('positions','Part 2 周 BOLL 历史网格');if(historyState){$('#bollChips').innerHTML='';$('#bollGroups').innerHTML=historyState;return;}renderBollChips();let items=bollItems().filter(item=>bollSignal(item,bollFilter));items.sort((a,b)=>bollSort==='yield'?bollYield(b)-bollYield(a):a.price-b.price);const groups=normalizedPart2Groups().filter(group=>items.some(item=>item.group===group.name));$('#bollGroups').innerHTML=groups.map(group=>{const list=items.filter(item=>item.group===group.name),levels=bollLevels(list[0]),heads=[...levels.sell.map(y=>`<th class="boll-sell-head">${y}%</th>`),...levels.buy.map((y,i)=>`<th class="boll-buy-head ${i===0?'split':''}">${y}%</th>`)].join('');return `<section class="boll-group ${bollCollapsed.has(group.name)?'collapsed':''}" data-boll-group="${escapeHtml(group.name)}"><button class="boll-group-head"><span>⌄</span>${escapeHtml(group.name)}${group.hydropower?'<em>水电阈值</em>':''}<i>${list.length}</i></button><div class="boll-table-wrap"><table class="boll-table"><thead><tr><th>股票</th><th>现价</th><th class="boll-track-head boll-track-head-up">上轨</th><th class="boll-track-head boll-track-head-mid">中轨</th><th class="boll-track-head boll-track-head-down">下轨</th><th class="boll-visual-head">当前位置</th><th>现股息率</th><th>正式股息</th>${heads}</tr></thead><tbody>${list.map(bollRow).join('')}</tbody></table><div class="boll-formula">周BOLL：前复权周K · BOLL(20,2) · 样本标准差。橙色买入网格，绿色卖出网格；颜色越深信号越强，“已达”表示现价已触及该档。</div></div></section>`;}).join('')||'<p class="muted empty">当前筛选没有匹配标的。</p>';document.querySelectorAll('.boll-group-head').forEach(button=>button.onclick=()=>{const name=button.parentElement.dataset.bollGroup;bollCollapsed.has(name)?bollCollapsed.delete(name):bollCollapsed.add(name);renderBollGrid();});}
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
function personalStockUniverse(){const all=new Map();for(const item of trackedStocks)all.set(item.code,{code:item.code,name:item.name||item.code});for(const item of holdings)all.set(item.code,{code:item.code,name:item.name||item.code});return [...all.values()];}
function renderCalendar(){const historyState=privateHistoryStatusHtml('calendar','Part 4 分红日历');if(historyState){$('#calendarGrid').innerHTML=historyState;$('#eventTitle').textContent='私有历史等待读取';$('#eventList').innerHTML='';return;}const universe=personalStockUniverse(),codes=new Set(universe.map(item=>item.code)),y=viewMonth.getFullYear(),m=viewMonth.getMonth();$('#monthTitle').textContent=`${y}年${m+1}月`;const first=new Date(y,m,1),start=new Date(y,m,1-((first.getDay()+6)%7));let html=['一','二','三','四','五','六','日'].map(x=>`<div class="day-name">${x}</div>`).join('');for(let i=0;i<42;i++){const d=new Date(start);d.setDate(start.getDate()+i);const key=[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');const has=market.events.some(e=>e.date===key&&codes.has(e.code));html+=`<button class="day ${d.getMonth()!==m?'other':''} ${has?'has-event':''} ${key===selectedDate?'selected':''}" data-day="${key}">${d.getDate()}</button>`;}$('#calendarGrid').innerHTML=html;document.querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>{selectedDate=b.dataset.day;renderCalendar();});const events=market.events.filter(e=>e.date===selectedDate&&codes.has(e.code));$('#eventTitle').textContent=`${selectedDate} 分红事件`;$('#eventList').innerHTML=events.length?events.map(e=>`<div class="event"><div class="event-head"><b>${escapeHtml(e.name)} · ${escapeHtml(e.type)}</b><span class="amount">${e.amount?money(e.amount):''}</span></div><small>${e.code} · ${escapeHtml(e.description||'')}</small></div>`).join(''):'<p class="muted">当天没有私有自选股分红事件。</p>';}
$('#prevMonth').onclick=()=>{viewMonth=new Date(viewMonth.getFullYear(),viewMonth.getMonth()-1,1);renderCalendar();};$('#nextMonth').onclick=()=>{viewMonth=new Date(viewMonth.getFullYear(),viewMonth.getMonth()+1,1);renderCalendar();};

function renderNews(){const historyState=privateHistoryStatusHtml('news','Part 5 公告历史');if(historyState){$('#newsSummary').innerHTML=historyState;$('#newsUpdateText').textContent='私有历史等待读取';$('#newsList').innerHTML='';return;}const universe=personalStockUniverse(),codes=new Set(universe.map(item=>item.code)),filter=$('#newsStockFilter').value;const relevant=newsMemory.items.filter(item=>codes.has(item.code)&&(!filter||item.code===filter));const estimates=relevant.filter(x=>Number.isFinite(+x.estimatedDividendPerShare)).length;$('#newsSummary').innerHTML=[['已记住公告/新闻',`${relevant.length}条`],['可计算每股分红',`${estimates}条`],['处理方式','私有历史迁移'],['正式值保护','预估不覆盖']].map(x=>`<div class="summary-card"><small>${x[0]}</small><strong>${x[1]}</strong></div>`).join('');$('#newsUpdateText').textContent=`私有记录更新：${formatTime(newsMemory.lastScanAt)}`;const currentOptions=[...$('#newsStockFilter').options].map(o=>o.value);if(currentOptions.length!==universe.length+1){$('#newsStockFilter').innerHTML='<option value="">全部私有自选股</option>'+universe.map(h=>`<option value="${h.code}">${escapeHtml(h.name)} ${h.code}</option>`).join('');$('#newsStockFilter').value=filter;}
$('#newsList').innerHTML=relevant.length?relevant.map(item=>{const value=Number.isFinite(+item.estimatedDividendPerShare)?money(item.estimatedDividendPerShare):'暂不可计算';const link=item.url?`<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">查看原始来源 ↗</a>`:'<span class="muted">来源链接未返回</span>';return `<article class="news-card"><div class="news-card-head"><div><span class="status status-${item.status==='已实施'?'done':item.status==='正式预案'?'proposal':'forecast'}">${escapeHtml(item.status)}</span><b>${escapeHtml(item.name)} · ${item.code}</b></div><time>${escapeHtml(item.publishedAt?.slice(0,10)||'')}</time></div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.summary)}</p><div class="estimate-box"><div><small>每股分红结果</small><strong>${value}</strong></div><div><small>可信度</small><strong>${escapeHtml(item.confidence)}</strong></div><div class="calculation"><small>计算依据</small><span>${escapeHtml(item.calculation)}</span></div></div><div class="news-foot"><span>${escapeHtml(item.source||'东方财富资讯')}</span>${link}</div></article>`}).join(''):'<p class="muted empty">暂无已保存的相关公告。系统每天会自动增量检查。</p>';}
$('#newsStockFilter').onchange=renderNews;

function normalizeTradePayload(value){if(Array.isArray(value))return {version:1,updatedAt:null,records:value};return {version:1,updatedAt:value?.updatedAt||null,records:Array.isArray(value?.records)?value.records:[]};}
async function loadCloudTradeRecords(){return normalizeTradePayload({records:tradeRecords});}
async function mutateTradeRecords(mutator){await requireAuth();const before=new Set(tradeRecords.map(record=>String(record.id)));const next=mutator([...tradeRecords]);const additions=next.filter(record=>record&&record.id&&!before.has(String(record.id)));if(additions.length)await supabaseRpc('personal_append_trade_records',{p_records:additions});await loadPersonalPart('strategy',true);return {records:tradeRecords,inserted:additions.length};}
async function loadStrategyFeedback(){return normalizeTradePayload({records:strategyFeedback});}
async function mutateStrategyFeedback(record){await requireAuth();await supabaseRpc('personal_append_strategy_feedback_v2',{p_record:record});await loadPersonalPart('strategy',true);return record;}
async function replacePrivateWatchlist(items){await requireAuth();const clean=items.map(item=>{const payload=item.payload&&typeof item.payload==='object'?{...item.payload}:{};return {...payload,code:String(item.code),name:String(item.name||item.code),...(item.shares!==undefined?{shares:item.shares}:{}),...(item.cost!==undefined?{cost:item.cost}:{})};});await supabaseRpc('personal_replace_watchlist',{p_items:clean});await loadPersonalPart('holdings',true);return clean;}
async function addWatchlistItem(){await requireAuth();const raw=String($('#stockSearch').value||'').trim(),code=(raw.match(/\d{6}/)||[])[0]||selectedCandidate?.code,candidate=selectedCandidate||catalog.find(item=>item.code===code);if(!candidate)throw new Error('请先从搜索结果中选择股票');if(trackedStocks.some(item=>String(item.code)===String(candidate.code)))throw new Error('这只股票已经在自选清单中');const shares=Number($('#addShares').value),cost=Number($('#addCost').value);const item={code:String(candidate.code),name:String(candidate.name)};if(Number.isInteger(shares)&&shares>=0)item.shares=shares;if(Number.isFinite(cost)&&cost>=0)item.cost=cost;await replacePrivateWatchlist([...trackedStocks,item]);selectedCandidate=null;$('#addForm').reset();$('#addForm').classList.add('hidden');showMessage('已添加自选股',`${candidate.name} 已写入当前账号的私有自选清单。`);}
async function deleteWatchlistItem(code){if(!authSession)return openLogin();const target=trackedStocks.find(item=>String(item.code)===String(code));if(!target)return;if(!confirm(`确定从当前账号的私有自选清单删除 ${target.name||code} 吗？不会删除模拟盘持仓，也不会修改 VPS 白名单。`))return;try{await replacePrivateWatchlist(trackedStocks.filter(item=>String(item.code)!==String(code)));showMessage('已删除自选股',`${target.name||code} 已从当前账号的私有自选清单移除。`);}catch(error){showMessage('删除失败',error?.message||'私有自选清单未修改。');}}
async function refreshStrategyCloud(){await requireAuth();await loadPersonalPart('strategy',true);renderStrategy();}
function compactPositions(positions={}){return {asOf:positions.asOf||null,day:positions.day?{zone:positions.day.zone,percent:Number(positions.day.percent)}:null,week:positions.week?{zone:positions.week.zone,percent:Number(positions.week.percent)}:null,month:positions.month?{zone:positions.month.zone,percent:Number(positions.month.percent)}:null};}
function tradeLearning(record){const y=Number(record.context?.yield||0),day=record.context?.positions?.day?.zone||'',action=record.action;if(action==='做T卖出'&&day==='上部')return '日线上部做T卖出：强化高位减仓习惯';if(action==='做T买入'&&day==='下部')return '日线下部做T买入：强化回落接回习惯';if(action.includes('买入')&&y>=7)return '7%以上仍买入：强化高股息率高性价比偏好';if(action.includes('买入')&&y>=5)return day==='下部'?'5%以上且日线下部买入：强化回落加仓偏好':'5%以上买入：强化分批建仓规则';if(action.includes('卖出')&&y>0&&y<=4.5)return '4%～4.5%卖出：强化清仓底线';return '已纳入策略画像，等待更多相似操作形成稳定规律';}
function tradeZonePill(period,item){if(!item)return `<span class="trade-zone">${period}待更新</span>`;return `<span class="trade-zone ${zoneClass(item.zone)}">${period}${escapeHtml(item.zone[0])}</span>`;}
function learnedProfile(){const buys=tradeRecords.filter(r=>r.action.includes('买入')),sells=tradeRecords.filter(r=>r.action.includes('卖出')),tRecords=tradeRecords.filter(r=>r.action.startsWith('做T'));const avg=list=>list.length?list.reduce((sum,r)=>sum+Number(r.context?.yield||0),0)/list.length:0;const commonDay=list=>{const counts={};list.forEach(r=>{const z=r.context?.positions?.day?.zone;if(z)counts[z]=(counts[z]||0)+1;});return Object.entries(counts).sort((a,b)=>b[1]-a[1])[0]?.[0]||'待积累';};return {buys,sells,tRecords,buyYield:avg(buys),sellYield:avg(sells),buyDay:commonDay(buys),tDay:commonDay(tRecords)};}
function strategyAdviceFor(holding){const s=stock(holding.code),y=s.price?s.totalDividend/s.price*100:0,p=s.positions||{},day=p.day?.zone||'待更新',week=p.week?.zone||'待更新',month=p.month?.zone||'待更新',hasHolding=Number(holding.shares)>0;let action='继续观察',kind='wait',why='等待股息率进入你的明确买卖区间，并继续观察日、周、月位置。',priority=1;if(!s.price||!s.totalDividend){action='等待正式数据';why='当前价格或正式年度分红尚未完整，暂不生成买卖方向。';priority=0;}else if(y<=4.5){action=hasHolding?'卖出区提醒':'暂不追入';kind='sell';why=hasHolding?'股息率已进入你设定的4%～4.5%全部卖出区，优先检查是否需要清仓。':'股息率处于你的低性价比区，当前没有持仓时不建议追入。';priority=5;}else if(y>=7){action='高性价比分批买';kind='buy';why=day==='上部'?'股息率达到7%高性价比区，但日线偏上，适合分批而不是一次买满。':'股息率达到7%高性价比区，且日线没有处在上部，符合你的积极分批条件。';priority=6;}else if(y>=5){if(day==='下部'){action='可分批买入';kind='buy';why='股息率达到5%起买线，同时日线处于下部，符合你回落分批买入的框架。';priority=4;}else if(day==='上部'&&hasHolding){action='做T卖出观察';kind='wait';why='股息率仍在5%以上，但日线已经到上部；已有底仓时可观察是否先T出一部分。';priority=3;}else{action='小仓分批/等待';kind='wait';why=`股息率已达到5%起买线，但日线位于${day}，更符合先小仓或等待回落。`;priority=2;}}else if(y<5){action='等待接近5%';why='尚未达到你的5%起买线，也没有进入4%～4.5%的持仓卖出底线。';priority=1;}const similar=tradeRecords.filter(r=>r.code===holding.code||Math.abs(Number(r.context?.yield||0)-y)<.35).length,confidence=Math.min(92,55+Math.min(tradeRecords.length,10)*2+Math.min(similar,5)*3),ai=(strategyAnalysis.advice||[]).find(item=>String(item.code)===holding.code);return {holding,s,y,day,week,month,action,kind,why,priority,similar,confidence,ai};}
const RESEARCH_MATCH_SCALE='research_match_percent_0_to_100',RESEARCH_MATCH_SCHEMA_VERSION=3,RESEARCH_MATCH_MEANING='研究匹配度，不是涨跌概率、收益概率或自动下单依据';
function researchMatchPercent(value,analysis=strategyAnalysis){if(typeof value!=='number'||!Number.isFinite(value)||!Number.isInteger(value)||Number(analysis?.schemaVersion)!==RESEARCH_MATCH_SCHEMA_VERSION||analysis?.confidenceScale!==RESEARCH_MATCH_SCALE||analysis?.confidenceMeaning!==RESEARCH_MATCH_MEANING||value<0||value>100)return null;return value;}
function researchMatchBadge(value,analysis=strategyAnalysis){const percent=researchMatchPercent(value,analysis);return percent===null?'研究匹配度待刷新':`研究匹配度 ${percent}%`;}
function renderBriefCommand(advice){let command=strategyAnalysis.briefCommand,analysisForCommand=strategyAnalysis;if(!command?.action){const best=advice.find(item=>item.kind==='buy');command=best?{id:`local-${market.updatedAt||'current'}-${best.holding.code}`,code:best.holding.code,name:best.holding.name,action:'分批买入',reason:best.why,condition:'只在你确认仓位后小批执行，不一次买满。',confidence:best.confidence}:{id:`local-${market.updatedAt||'current'}-none`,code:'',name:'',action:'当前不买',reason:'当前没有股票满足你的明确买入条件。',condition:'继续等待正式股息率和位置进入固定买点。',confidence:70};analysisForCommand={schemaVersion:RESEARCH_MATCH_SCHEMA_VERSION,confidenceScale:RESEARCH_MATCH_SCALE,confidenceMeaning:RESEARCH_MATCH_MEANING};}const code=String(command.code||''),name=command.name||stock(code).name||'',stockData=code?stock(code):{},yieldRate=stockData.price?stockData.totalDividend/stockData.price*100:0,currentFeedback=strategyFeedback.find(x=>x.recommendationId===command.id),action=String(command.action||'当前不买'),researchMatch=researchMatchPercent(command.confidence,analysisForCommand);$('#briefCommandTitle').textContent=code?`${action}：${name}（${code}）`:action;$('#briefCommandBadge').textContent=researchMatchBadge(command.confidence,analysisForCommand);$('#briefCommandBadge').title=researchMatch===null?'旧版结果没有可验证的研究匹配度刻度；页面不会猜测 0.72、1 等数值代表的百分比，等待 v3 升级或新的本机 Worker 结果。':'研究匹配度表示当前建议与已确认规则、当前数据和有限样本的一致性；不是涨跌概率、收益概率或自动下单依据。';$('#briefCommandBadge').className=/买入/.test(action)?'buy':/卖出/.test(action)?'sell':'wait';$('#briefCommandReason').textContent=[command.reason,command.condition].filter(Boolean).map(text=>String(text).replace(/[；;。]+$/,'')).join('；')+'。';$('#briefCommandFacts').innerHTML=code?`<span>现价 <b>${money(stockData.price)}</b></span><span>正式股息率 <b>${yieldRate.toFixed(2)}%</b></span><span>反馈样本 <b>${Number(strategyFeedback.length||strategyAnalysis.feedbackStats?.count||0)}次</b></span>`:'<span>当前没有合格买点</span>';document.querySelectorAll('[data-strategy-feedback]').forEach(button=>{button.classList.toggle('selected',currentFeedback?.status===button.dataset.strategyFeedback);button.disabled=!!currentFeedback||feedbackPending;button.onclick=()=>submitStrategyFeedback(button.dataset.strategyFeedback,command);});}
async function submitStrategyFeedback(status,command){if(feedbackPending)return;feedbackPending=true;document.querySelectorAll('[data-strategy-feedback]').forEach(button=>{button.disabled=true;});const labels={executed:'已执行',not_executed:'没买 / 没执行',deferred:'暂缓观察'};try{await requireAuth();await mutateStrategyFeedback({id:crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`,recommendationId:String(command.id),status,code:String(command.code||''),name:String(command.name||''),action:String(command.action||''),reason:String(command.reason||''),condition:String(command.condition||''),recommendedAt:strategyAnalysis.updatedAt||new Date().toISOString(),createdAt:new Date().toISOString()});renderStrategy();showMessage('反馈已保存',`已记录“${labels[status]}”。后台任务会结合这次选择和全部真实操作重新生成下一条指令。`);}catch(error){if(!/请先/.test(error.message))showMessage('反馈未保存',error.message);}finally{feedbackPending=false;renderStrategy();}}
function renderStrategyPerformance(){
  const box=$('#strategyPerformance');
  if(!box)return;
  const p=strategyAnalysis.recommendationPerformance||{};
  const has=value=>value!==null&&value!==undefined&&Number.isFinite(Number(value));
  const rate=has(p.successRate)?`${Number(p.successRate).toFixed(1)}%`:'待积累';
  const time=has(p.avgTradingDaysToHit)?`${Number(p.avgTradingDaysToHit).toFixed(1)}个交易日`:'待积累';
  box.innerHTML=`<div><small>策略命中率</small><strong>${rate}</strong><span>${Number(p.successes||0)}次命中 / ${Number(p.resolved||0)}次已结算</span></div><div><small>平均达标时间</small><strong>${time}</strong><span>${Number(p.pending||0)}条买入指令观察中</span></div><p>私有推荐结果尚未接入时不计算样本；历史机械命中率也不等于未来收益保证。</p>`;
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
  el.textContent=`策略自我更新 API：${label}（${channel} · ${reason}${checked}）`;
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
  renderStrategyAudit();
  renderStrategyEvolution();
  renderFocusedStudy();
  const profile=learnedProfile();
  const count=tradeRecords.length;
  const progress=Math.min(100,count*8);
  const stage=count===0?'等待私有操作记录':count<5?'开始识别':count<12?'持续学习':'画像逐渐稳定';
  $('#strategyStage').textContent=strategyAnalysis.status==='success'?`${stage} · 分析已更新`:stage;
  $('#strategyProgress').style.width=`${progress}%`;
  $('#strategyStats').innerHTML=[['私有操作',`${count}笔`],['做T记录',`${profile.tRecords.length}笔`],['画像置信度',count<5?'较低':count<12?'中等':'较高']].map(([title,value])=>`<div class="strategy-stat"><small>${escapeHtml(title)}</small><strong>${escapeHtml(value)}</strong></div>`).join('');
  renderStrategyPerformance();
  const rules=[];
  if(strategyAnalysis.status&&strategyAnalysis.profileSummary)rules.push(`分析摘要：${strategyAnalysis.profileSummary}`);
  for(const rule of strategyAnalysis.learnedRules||[])if(rule&&!rules.includes(rule))rules.push(rule);
  if(profile.buys.length)rules.push(`买入记录平均股息率为 ${profile.buyYield.toFixed(2)}%，常见日线位置是${profile.buyDay}。`);
  if(profile.sells.length)rules.push(`卖出记录平均股息率为 ${profile.sellYield.toFixed(2)}%，继续对照4%～4.5%底线。`);
  if(profile.tRecords.length)rules.push(`已识别 ${profile.tRecords.length} 笔做T操作，常见日线位置是${profile.tDay}。`);
  if(!rules.length)rules.push(personalLoadedParts.has('strategy')?'当前私有操作记录中尚未形成可归纳规则。':'正在从当前登录账号读取私有操作记录；公开 GitHub 历史不会作为回退数据。');
  $('#learnedRules').innerHTML=rules.map((rule,i)=>`<div><b>${i+1}</b><span>${escapeHtml(rule)}</span></div>`).join('');
  const adviceHoldings=holdings.length?sortedHoldingsByMarketValue():trackedStocks.map(item=>({code:item.code,name:item.name||item.code,shares:Number(item.payload?.shares??item.shares)||0}));
  const advice=adviceHoldings.map(strategyAdviceFor).sort((a,b)=>b.priority-a.priority||b.y-a.y);
  renderBriefCommand(advice);
  $('#strategyAdvice').innerHTML=advice.length?advice.map(a=>`<article class="strategy-card ${escapeHtml(a.kind)}"><span class="strategy-action">${escapeHtml(a.action)}</span><h4>${escapeHtml(a.holding.name)}</h4><div class="stock-code">${escapeHtml(a.holding.code)}</div><div class="strategy-card-metrics"><div><small>当前正式股息率</small><b>${a.y.toFixed(3)}%</b></div><div><small>当前持仓</small><b>${Number(a.holding.shares)||0}股</b></div><div><small>日 / 周 / 月位置</small><b>${escapeHtml(a.day)} / ${escapeHtml(a.week)} / ${escapeHtml(a.month)}</b></div></div><p>${escapeHtml(a.why)}</p>${a.ai?.reason?`<small>私有分析补充：${escapeHtml(a.ai.reason)}</small>`:''}</article>`).join(''):'<p class="muted">当前没有可供分析的私有自选股票。</p>';
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
function validateTradeCsv(text){const rows=parseCsv(text);if(!rows.length)throw new Error('CSV没有可导入的数据行');if(rows.length>5000)throw new Error('单次最多导入5000条操作');const allowed=new Set(['买入','卖出','做T买入','做T卖出']),occurrences=new Map(),seenIds=new Set(),records=[],errors=[];rows.forEach((row,index)=>{const line=index+2,dateValue=csvPick(row,['日期','date']).trim(),rawCode=csvPick(row,['股票代码','代码','code']).replace(/^['\s]+/,'').trim().toUpperCase().replace(/\.(SH|SZ)$/,'');const code=/^\d+$/.test(rawCode)?rawCode.padStart(6,'0'):rawCode,action=csvPick(row,['操作','action']).replace(/\s+/g,''),price=Number(csvPick(row,['成交价格','价格','price']).replace(/[¥,\s]/g,'')),shares=Number(csvPick(row,['成交股数','股数','shares']).replace(/[,\s]/g,'')),parsedDate=/^\d{4}-\d{2}-\d{2}$/.test(dateValue)?new Date(`${dateValue}T00:00:00Z`):null,validDate=!!parsedDate&&!Number.isNaN(parsedDate.getTime())&&parsedDate.toISOString().slice(0,10)===dateValue;if(!validDate)errors.push(`第${line}行：日期应为YYYY-MM-DD且必须是真实日期`);if(!/^\d{6}$/.test(code))errors.push(`第${line}行：股票代码应为6位数字`);if(!allowed.has(action))errors.push(`第${line}行：操作仅支持买入、卖出、做T买入、做T卖出`);if(!Number.isFinite(price)||price<=0)errors.push(`第${line}行：成交价格必须大于0`);if(!Number.isInteger(shares)||shares<=0)errors.push(`第${line}行：成交股数必须是正整数`);if(!validDate||!/^\d{6}$/.test(code)||!allowed.has(action)||!Number.isFinite(price)||price<=0||!Number.isInteger(shares)||shares<=0)return;const tuple=[dateValue,code,action,String(price),String(shares)],key=tuple.join('|'),occurrence=(occurrences.get(key)||0)+1;occurrences.set(key,occurrence);const suppliedId=csvPick(row,['记录ID','id']).trim(),id=suppliedId||csvStableId([...tuple,String(occurrence)]);if(!/^[A-Za-z0-9._:-]{1,160}$/.test(id)){errors.push(`第${line}行：记录ID格式无效`);return;}if(seenIds.has(id)){errors.push(`第${line}行：记录ID重复`);return;}seenIds.add(id);const candidate=catalog.find(item=>item.code===code),marketStock=market.stocks.find(item=>item.code===code),holding=holdings.find(item=>item.code===code),name=csvPick(row,['股票名称','股票','name'])||candidate?.name||marketStock?.name||holding?.name||code,dividendRaw=csvPick(row,['正式每股分红','每股分红','dividendPerShare']),dividend=dividendRaw===''?NaN:Number(dividendRaw.replace(/[,\s]/g,'')),yieldRaw=csvPick(row,['历史股息率','股息率','yield']),historicalYield=yieldRaw===''?NaN:Number(yieldRaw.replace(/[%\s]/g,'')),positions={day:{zone:csvPick(row,['成交日线位置','日线位置','dayZone'])},week:{zone:csvPick(row,['成交周线位置','周线位置','weekZone'])},month:{zone:csvPick(row,['成交月线位置','月线位置','monthZone'])}},cleanPositions=Object.fromEntries(Object.entries(positions).filter(([,item])=>item.zone)),createdAt=csvPick(row,['创建时间','createdAt']);records.push({id,date:dateValue,code,name,action,price,shares,dividendPerShare:Number.isFinite(dividend)?dividend:Number(stock(code).totalDividend||0),createdAt:createdAt&&!Number.isNaN(Date.parse(createdAt))?createdAt:new Date().toISOString(),source:'csv-import',learning:csvPick(row,['策略学习结果','learning']),context:{status:'pending',requestedDate:dateValue,...(Number.isFinite(historicalYield)?{yield:historicalYield}:{}),...(Object.keys(cleanPositions).length?{positions:cleanPositions}:{} )}});});if(errors.length)throw new Error(errors.slice(0,8).join('；')+(errors.length>8?`；另有${errors.length-8}处错误`:''));return records;}
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
$('#tradeRecordForm').onsubmit=async e=>{e.preventDefault();const code=$('#tradeStock').value,universe=personalStockUniverse(),target=universe.find(item=>item.code===code),s=stock(code),price=Number($('#tradePrice').value),shares=Number($('#tradeShares').value),action=$('#tradeAction').value,dateValue=$('#tradeDate').value;if(!target||!dateValue||!Number.isFinite(price)||price<=0||!Number.isInteger(shares)||shares<=0)return showMessage('记录未保存','请检查日期、股票、成交价格和成交股数。');const position=s.positions||{},yieldRate=Number(s.price)>0?Number(s.totalDividend)/Number(s.price)*100:null,record={id:crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`,date:dateValue,code,name:target.name,action,price,shares,dividendPerShare:Number(s.totalDividend)||0,createdAt:new Date().toISOString(),source:'dashboard-private',context:{status:'pending',requestedDate:dateValue,marketPriceAtRecord:s.price,totalDividend:s.totalDividend,...(yieldRate===null?{}:{yield:yieldRate}),...(position&&Object.keys(position).length?{positions:compactPositions(position)}:{})}};const submit=$('#tradeRecordForm button[type="submit"]');try{submit.disabled=true;submit.textContent='正在写入私有空间…';await mutateTradeRecords(records=>[record,...records],'private: add trade record');$('#tradeRecordDialog').close();renderStrategy();showMessage('操作已保存',`${target.name} ${action} ${shares}股已写入当前账号的私有历史。`);}catch(error){showMessage('记录未保存',error?.message||'私有记录未修改。');}finally{submit.disabled=false;submit.textContent='保存操作';}};
document.querySelectorAll('[data-trade-filter]').forEach(button=>button.onclick=()=>{tradeFilter=button.dataset.tradeFilter;document.querySelectorAll('[data-trade-filter]').forEach(x=>x.classList.toggle('active',x===button));renderStrategy();});

function scoreCandidate(item,q){const name=item.name.toLowerCase(),code=item.code,pinyin=item.pinyin||'',initials=item.initials||'';if(q===code||q===name||q===initials||q===pinyin)return 100;if(code.startsWith(q)||name.startsWith(q)||initials.startsWith(q)||pinyin.startsWith(q))return 80;if(code.includes(q)||name.includes(q)||initials.includes(q)||pinyin.includes(q))return 50;return 0;}
function searchCatalog(){const q=$('#stockSearch').value.trim().toLowerCase().replace(/\s+/g,'');selectedCandidate=null;$('#selectedStock').textContent='请选择下方匹配结果';if(!q){$('#suggestions').classList.add('hidden');return;}const matches=catalog.map(item=>({item,score:scoreCandidate(item,q)})).filter(x=>x.score).sort((a,b)=>b.score-a.score||a.item.code.localeCompare(b.item.code)).slice(0,8);$('#suggestions').innerHTML=matches.length?matches.map(({item})=>`<button type="button" data-pick="${item.code}"><b>${escapeHtml(item.name)}</b><span>${item.code}.${item.market}</span><small>${escapeHtml(item.pinyin)}</small></button>`).join(''):'<p>没有找到匹配的A股</p>';$('#suggestions').classList.remove('hidden');document.querySelectorAll('[data-pick]').forEach(button=>button.onclick=()=>{selectedCandidate=catalog.find(x=>x.code===button.dataset.pick);$('#stockSearch').value=`${selectedCandidate.name} ${selectedCandidate.code}`;$('#selectedStock').textContent=`已选择：${selectedCandidate.name} ${selectedCandidate.code}.${selectedCandidate.market}`;$('#suggestions').classList.add('hidden');});}
$('#stockSearch').oninput=searchCatalog;
$('#showAdd').onclick=()=>{if(!authSession)return openLogin();$('#addForm').classList.toggle('hidden');if(!$('#addForm').classList.contains('hidden'))$('#stockSearch').focus();};
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
      if(authSession&&(event==='SIGNED_IN'||event==='TOKEN_REFRESHED'))queueMicrotask(()=>{void loadPrivateDashboard();startPrivateAutoRefresh();});
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
function clearPersonalData(){personalMigrationState=null;personalLoadedParts=new Set();personalPartLoads=new Map();personalPartErrors=new Map();trackedStocks=[];part2Config={version:1,groups:[],extraStocks:[]};market={stocks:[],events:[],updatedAt:null};marketLoaded=false;newsMemory={items:[],updatedAt:null,lastScanAt:null};tradeRecords=[];strategyFeedback=[];feedbackPending=false;strategyRecommendations=[];strategyAnalysis={status:'waiting',learnedRules:[],advice:[]};strategyProfile={schemaVersion:1,externalLearnedRules:[],personalBehaviorEvidence:{},fixedGuardrails:[],nonExecutablePositionSuggestions:[],conflictsAndGaps:[]};focusedStudy=null;focusedStudyNew=null;strategyApiHealth={status:'unknown',reason:'请登录后读取私有策略记录'};}
function personalRpcForPage(pageId){return {holdings:'personal_get_part1',positions:'personal_get_part2',grid:'personal_get_part4',calendar:'personal_get_part4',news:'personal_get_part5',strategy:'personal_get_part6'}[pageId]||'';}
function personalCount(value){const number=Number(value);return Number.isFinite(number)&&number>=0?Math.floor(number):0;}
function privateHistoryStatusHtml(pageId,label){if(!authSession)return `<div class="private-history-gate signed-out"><b>${escapeHtml(label)}已迁入私有空间</b><p>公开链接不会显示个人历史。请点击右上角“登录查看私有历史”，使用你的用户名和密码登录后自动读取。</p></div>`;if(personalLoadedParts.has(pageId))return '';if(personalPartLoads.has(pageId))return `<div class="private-history-gate loading"><b>正在读取${escapeHtml(label)}</b><p>数据仅从当前登录账号的 Supabase 私有 RPC 返回，请稍候。</p></div>`;if(personalPartErrors.has(pageId))return `<div class="private-history-gate error"><b>${escapeHtml(label)}暂未读取成功</b><p>登录会话仍保持；请再次点击本栏目重试。公开页面不会回退到旧 GitHub JSON。</p></div>`;if(personalMigrationState&&personalCount(personalMigrationState.source_files)===0)return `<div class="private-history-gate empty"><b>当前账号没有已迁入的历史数据</b><p>请确认使用的是已绑定历史内容的用户名登录。</p></div>`;return '';}
function renderPrivateHistoryStrip(){const box=$('#privateHistoryStrip');if(!box)return;if(!authSession){box.dataset.state='signed-out';box.innerHTML='<b>历史信息已私有化</b><span>公开访问不会展示原 Part 1—6 内容；请点击右上角“登录查看私有历史”。</span>';return;}const state=personalMigrationState;if(!state){box.dataset.state='unavailable';box.innerHTML='<b>私有历史状态暂未读取</b><span>Part 0 的 VPS 投影不受影响；进入 Part 1—6 会单独重试相应私有读取。</span>';return;}const sourceFiles=personalCount(state.source_files);if(!sourceFiles){box.dataset.state='empty';box.innerHTML='<b>当前账号没有迁入历史</b><span>请确认当前登录用户名是否为已绑定旧数据的账号。</span>';return;}const marketText=state.market_stock_count?` · 行情 ${personalCount(state.market_stock_count)}只 · 日历 ${personalCount(state.calendar_event_count)}条`:'';box.dataset.state='ready';box.innerHTML=`<b>历史已迁入当前账号</b><span>自选 ${personalCount(state.watchlist_count)} 只${marketText} · 公告 ${personalCount(state.news_count)} 条 · 操作 ${personalCount(state.trade_count)} 笔 · 反馈 ${personalCount(state.feedback_count)} 条 · 建议 ${personalCount(state.recommendation_count)} 条；点击 Part 1—6 按需查看。</span>`;}
function applyPersonalPart(pageId,data){if(pageId==='holdings'){trackedStocks=Array.isArray(data?.watchlist)?data.watchlist.filter(item=>/^\d{6}$/.test(String(item?.code||''))).map(item=>({...item,code:String(item.code),name:String(item.name||item.code)})):[];}else if(pageId==='positions'){part2Config=data&&typeof data==='object'?data:{version:1,groups:[],extraStocks:[]};}else if(pageId==='grid'||pageId==='calendar'){market=data&&typeof data==='object'?data:{stocks:[],events:[]};marketLoaded=true;}else if(pageId==='news'){newsMemory=data&&typeof data==='object'?data:{items:[],updatedAt:null,lastScanAt:null};}else if(pageId==='strategy'){tradeRecords=Array.isArray(data?.trades?.records)?data.trades.records:[];strategyFeedback=Array.isArray(data?.feedback?.records)?data.feedback.records:[];strategyRecommendations=Array.isArray(data?.recommendations?.records)?data.recommendations.records:[];strategyProfile=data?.profile&&typeof data.profile==='object'?data.profile:strategyProfile;strategyAnalysis=data?.analysis&&typeof data.analysis==='object'?data.analysis:strategyAnalysis;focusedStudy=data?.focused_study&&typeof data.focused_study==='object'?data.focused_study:null;focusedStudyNew=data?.focused_study_new&&typeof data.focused_study_new==='object'?data.focused_study_new:null;strategyApiHealth=data?.api_health&&typeof data.api_health==='object'?data.api_health:{status:'unknown',reason:'私有健康记录为空'};}}
async function loadPersonalMarket(force=false){if(!authSession||!supabaseClient)return false;if(marketLoaded&&!force){personalLoadedParts.add('market');return true;}if(personalPartLoads.has('market')){await personalPartLoads.get('market');return marketLoaded;}const sessionAtStart=authSession;personalPartErrors.delete('market');const request=(async()=>{try{const data=await supabaseRpc('personal_get_part4');if(authSession!==sessionAtStart)return;applyPersonalPart('calendar',data);marketLoaded=true;personalPartErrors.delete('market');}catch(error){if(authSession===sessionAtStart){marketLoaded=false;personalPartErrors.set('market','private_read_failed');}}finally{personalPartLoads.delete('market');}if(authSession===sessionAtStart)render();})();personalPartLoads.set('market',request);await request;return marketLoaded;}
async function loadPersonalPart(pageId,force=false){const rpc=personalRpcForPage(pageId);if(!rpc||!authSession||!supabaseClient)return;if(pageId!=='holdings'&&['positions','grid','calendar','news','strategy'].includes(pageId)&&!personalLoadedParts.has('holdings'))await loadPersonalPart('holdings');if(personalLoadedParts.has(pageId)&&!force)return;if(personalPartLoads.has(pageId))return personalPartLoads.get(pageId);const sessionAtStart=authSession;personalPartErrors.delete(pageId);const request=(async()=>{try{let data;if(pageId==='holdings'){data=await supabaseRpc('personal_get_part1');if(authSession!==sessionAtStart)return;applyPersonalPart(pageId,data);personalLoadedParts.add(pageId);await loadPersonalMarket(force);}else if(pageId==='grid'||pageId==='calendar'){if(!await loadPersonalMarket(force))throw new Error('private_market_read_failed');personalLoadedParts.add(pageId);}else{data=await supabaseRpc(rpc);if(authSession!==sessionAtStart)return;applyPersonalPart(pageId,data);personalLoadedParts.add(pageId);if(['positions','strategy'].includes(pageId)&&!await loadPersonalMarket(force))throw new Error('private_market_read_failed');}personalPartErrors.delete(pageId);}catch(error){if(authSession===sessionAtStart){personalLoadedParts.delete(pageId);personalPartErrors.set(pageId,'private_read_failed');}}finally{personalPartLoads.delete(pageId);}if(authSession===sessionAtStart)render();})();personalPartLoads.set(pageId,request);render();return request;}

function openLogin(){$('#loginError').textContent='';if(!$('#loginDialog').open)$('#loginDialog').showModal();}
function updateAuthUI(){const logged=!!authSession;const personalReady=personalCount(personalMigrationState?.source_files)>0;$('#authStatus').textContent=logged?(currentUsername?`已登录：${currentUsername}`:'已登录'):'未登录';$('#authStatus').classList.toggle('logged',logged);$('#loginButton').textContent=logged?'退出登录':'登录查看私有历史';document.body.classList.toggle('authenticated',logged);if($('#authStrip'))$('#authStrip').firstElementChild.textContent=logged?(personalReady?'当前浏览器会话已保持；私有历史已迁入当前账号，点击 Part 1—6 按需读取。':'当前浏览器会话已保持；正在核对当前账号的私有历史状态。'):'公开浏览模式：为保护个人历史，Part 1—6 不显示旧数据；点击右上角登录后才可读取。';renderPrivateHistoryStrip();}
async function loginWithUsername(username,password){if(!supabaseClient)throw Object.assign(new Error('auth_not_configured'),{code:'auth_not_configured'});const normalized=canonicalUsername(username);if(!normalized||typeof password!=='string'||password.length<6)throw Object.assign(new Error('invalid_credentials'),{code:'invalid_credentials'});const session=await callAuthFunction('username-login',{username:normalized,password});if(!session?.access_token||!session?.refresh_token)throw Object.assign(new Error('temporarily_unavailable'),{code:'temporarily_unavailable'});const {error}=await supabaseClient.auth.setSession({access_token:session.access_token,refresh_token:session.refresh_token});if(error)throw Object.assign(new Error('temporarily_unavailable'),{code:'temporarily_unavailable'});}
async function requestRecovery(username){const normalized=canonicalUsername(username);if(!normalized)throw Object.assign(new Error('temporarily_unavailable'),{code:'temporarily_unavailable'});return callAuthFunction('username-recovery-request',{username:normalized});}
async function signOut(){if(supabaseClient)await supabaseClient.auth.signOut();stopPrivateAutoRefresh();authSession=null;currentUsername='';vpsAdmin=false;privatePortfolio=null;runtimeDisplay=null;whitelistControl=null;privateLoadState='not_loaded';holdings=[];clearPersonalData();updateAuthUI();render();}
async function requireAuth(){if(authSession)return authSession;openLogin();throw Object.assign(new Error('auth_required'),{code:'auth_required'});}
async function requireAdmin(){await requireAuth();if(!vpsAdmin){showMessage('权限不足','当前账号没有显式 VPS 管理员权限，不能提交白名单。');throw Object.assign(new Error('admin_required'),{code:'admin_required'});}return true;}
function applyPrivatePortfolio(value){const scopes=Array.isArray(value)?value:[];privatePortfolio=scopes.find(item=>item&&item.scope_key==='primary')||null;const rows=Array.isArray(privatePortfolio?.positions)?privatePortfolio.positions:[];holdings=rows.map(item=>{const symbol=String(item.symbol||'').toUpperCase(),name=item.display_name||symbolDisplay(symbol);return {code:symbol.slice(0,6),symbol,name,shares:Number(item.held_quantity)||0,cost:item.average_cost_per_share,marketValue:item.market_value,privatePosition:item};});}
async function loadPrivateDashboard(){if(!authSession||!supabaseClient||privateLoadInFlight)return;const sessionAtStart=authSession;privateLoadInFlight=true;privateLoadState='loading';privateLoadError='';render();try{const [portfolio,runtime,admin,username]=await Promise.all([supabaseRpc('vps_private_get_portfolio'),supabaseRpc('vps_private_get_runtime_display'),supabaseRpc('vps_is_admin'),supabaseRpc('app_get_current_username')]);if(authSession!==sessionAtStart)return;applyPrivatePortfolio(portfolio);runtimeDisplay=runtime&&typeof runtime==='object'?runtime:null;vpsAdmin=admin===true;currentUsername=typeof username==='string'?username:'';whitelistControl=vpsAdmin?await supabaseRpc('vps_get_whitelist_control_state'):null;if(authSession!==sessionAtStart)return;privateLoadState='ready';}catch(error){if(authSession===sessionAtStart){privateLoadState='error';privateLoadError='private_read_failed';privatePortfolio=null;runtimeDisplay=null;whitelistControl=null;vpsAdmin=false;}}finally{privateLoadInFlight=false;}if(authSession!==sessionAtStart)return;try{const migrationState=await supabaseRpc('personal_get_migration_state');if(authSession===sessionAtStart)personalMigrationState=migrationState&&typeof migrationState==='object'?migrationState:null;}catch(error){if(authSession===sessionAtStart)personalMigrationState=null;}updateAuthUI();render();const activePage=document.querySelector('.page.active')?.id||'today';if(personalRpcForPage(activePage))await loadPersonalPart(activePage,true);else if(personalCount(personalMigrationState?.source_files)>0)void loadPersonalPart('holdings');}
function fillWhitelistEditor(){if(!$('#whitelistSymbols'))return;const control=whitelistControl||{},symbols=Array.isArray(control.desired_symbols)&&control.desired_symbols.length?control.desired_symbols:(Array.isArray(control.active_symbols)?control.active_symbols:[]);$('#whitelistSymbols').value=symbols.join('\n');$('#whitelistBase').textContent=control.edit_base_revision_no?`提交基线：revision ${control.edit_base_revision_no}`:'提交基线：暂无';$('#whitelistCount').textContent=`${symbols.length} / 50`;
}
function openWhitelistEditor(){try{requireAdmin().then(()=>{fillWhitelistEditor();$('#whitelistDialog').showModal();}).catch(()=>{});}catch(error){}}
function parseWhitelistEditor(){const raw=String($('#whitelistSymbols').value||'').split(/[\s,，]+/).map(item=>item.trim().toUpperCase()).filter(Boolean),symbols=[],seen=new Set();for(const symbol of raw){if(!/^\d{6}\.(SH|SZ)$/.test(symbol))throw new Error('白名单只能填写 6 位代码加 .SH/.SZ');if(seen.has(symbol))continue;seen.add(symbol);symbols.push(symbol);}if(symbols.length<1||symbols.length>50)throw new Error('白名单必须是 1—50 只 A 股，不能提交空列表');return symbols;}
async function submitWhitelist(){try{await requireAdmin();const symbols=parseWhitelistEditor(),control=await supabaseRpc('vps_get_whitelist_control_state'),previous=new Set([...(control?.active_symbols||[]),...(control?.desired_symbols||[])]),removed=[...previous].filter(symbol=>!symbols.includes(symbol)),held=(privatePortfolio?.positions||[]).map(item=>item.symbol).filter(symbol=>removed.includes(symbol));if(held.length&&!confirm(`将移除 ${held.join('、')}。移出策略白名单不等于卖出或删除模拟盘持仓，确定继续吗？`))return;const expected=control?.edit_base_revision_no===null||control?.edit_base_revision_no===undefined?null:Number(control.edit_base_revision_no);const {data,error}=await supabaseClient.rpc('vps_submit_whitelist_revision',{p_symbols:symbols,p_request_note:'dashboard whitelist update',p_expected_base_revision_no:expected});if(error)throw new Error('revision_submit_failed');const row=Array.isArray(data)?data[0]:data;$('#whitelistDialog').close();whitelistControl=await supabaseRpc('vps_get_whitelist_control_state');render();showMessage('目标白名单已提交',`已提交 revision ${row?.revision_no||'—'}，当前状态为 submitted。须等待 VPS 合法周期完成 pull、暂存、SQLite 激活、active-cache 和 ACK；提交本身不等于已生效。`);}catch(error){showMessage('白名单未提交',error?.message==='revision_submit_failed'?'控制版本提交失败，请刷新后重试。':error?.message||'请先登录并确认管理员权限。');}}
if($('#manageWhitelist'))$('#manageWhitelist').onclick=openWhitelistEditor;
if($('#whitelistSymbols'))$('#whitelistSymbols').oninput=()=>{$('#whitelistCount').textContent=`${String($('#whitelistSymbols').value||'').split(/[\s,，]+/).filter(Boolean).length} / 50`;};
if($('#whitelistForm'))$('#whitelistForm').onsubmit=e=>{e.preventDefault();submitWhitelist();};
if($('#cancelWhitelist'))$('#cancelWhitelist').onclick=()=>$('#whitelistDialog').close();
if($('#recoverLink'))$('#recoverLink').onclick=()=>{if($('#loginDialog').open)$('#loginDialog').close();$('#recoveryUsername').value=$('#loginUsername').value||'';$('#recoveryMessage').textContent='';$('#recoveryDialog').showModal();};
if($('#cancelRecovery'))$('#cancelRecovery').onclick=()=>$('#recoveryDialog').close();
if($('#recoveryForm'))$('#recoveryForm').onsubmit=async e=>{e.preventDefault();const button=$('#recoveryForm button[type="submit"]');try{button.disabled=true;const payload=await requestRecovery($('#recoveryUsername').value);$('#recoveryMessage').textContent=payload.message||'如果用户名存在，恢复邮件已发送。';}catch(error){$('#recoveryMessage').textContent='恢复服务暂时不可用，请稍后再试。';}finally{button.disabled=false;}};
$('#loginButton').onclick=async()=>{if(authSession){try{await signOut();}catch(error){showMessage('退出失败','会话退出未完成，请稍后重试。');}}else openLogin();};
$('#loginForm').onsubmit=async e=>{e.preventDefault();$('#loginError').textContent='正在安全验证…';const submit=$('#loginForm button[type="submit"]');try{submit.disabled=true;await loginWithUsername($('#loginUsername').value,$('#loginPassword').value);$('#loginPassword').value='';$('#loginDialog').close();await loadPrivateDashboard();}catch(error){$('#loginError').textContent=authErrorText(error);}finally{submit.disabled=false;}};
$('#reloadPart0Preview').onclick=async()=>{if(PART0_LOCAL_ONLY_PREVIEW){part0PreviewRenderedAt=new Date();renderTodayBoard();showMessage('本地预览已重新载入','本次只重新渲染浏览器内存中的 Part 0 界面，没有连接 Supabase、VPS、行情、模拟盘或订单接口。');return;}if(!authSession)return openLogin();const button=$('#reloadPart0Preview');button.disabled=true;try{await loadPrivateDashboard();showMessage('私有状态已刷新','页面只重新读取了 Supabase 私有 RPC，没有触发 VPS、行情或交易接口。');}finally{button.disabled=false;}};
$('#openPart1FromPart0').onclick=()=>{if(PART0_LOCAL_ONLY_PREVIEW){showMessage('严格本地预览模式','本地模式只验证 Part 0，不载入 Part 1 数据；返回独立预览地址即可继续验证今日看板。');return;}switchTab('holdings');};
async function loadTrackedStocks(){return [];}
async function loadPart2Config(){return {version:1,groups:[],extraStocks:[]};}
function syncHoldingsWithCloud(){return;}
async function mutateTrackedStocks(){await requireAdmin();throw new Error('股票池请在 Part 0 白名单控制中提交 immutable revision。');}
async function mutatePart2Config(){await requireAuth();throw new Error('Part 2 配置尚未迁移到私有 RPC，当前不写入公开数据。');}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
$('#refreshMarket').onclick=async()=>{if(!authSession)return openLogin();showMessage('行情刷新方式','当前页面不直接写 GitHub 或调用行情 Provider；行情由 VPS/后台周期更新后通过私有投影返回。');};
$('#refreshNews').onclick=async()=>{if(!authSession)return openLogin();showMessage('公告刷新方式','当前页面不直接写 GitHub；公告增量任务迁移到私有后台后再开放。');};
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
