const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../assets/app.js'),'utf8');
function context(names,extra={}){
  const c=vm.createContext({console,...extra});
  for(const name of names){
    const start=source.indexOf(`function ${name}(`);assert.ok(start>=0,`missing ${name}`);
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
test('market read and recommendations use new private contracts',()=>{
 assert.ok(/supabaseRpc\('personal_get_part4_v4'\)/.test(source));
 assert.ok(/strategyRecommendationMeta\.performance/.test(source));
 assert.ok(!/strategyAnalysis\.status==='success'\?`\$\{stage\} · 分析已更新`/.test(source));
});
