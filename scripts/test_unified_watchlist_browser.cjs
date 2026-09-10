/* Isolated browser acceptance: synthetic fixtures only, external requests blocked.
   Requires playwright on NODE_PATH and a configured Playwright browser cache. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'..');
const OUT=process.env.DASHBOARD_TEST_OUTPUT;
if(!OUT||!path.isAbsolute(OUT))throw new Error('Set an absolute DASHBOARD_TEST_OUTPUT outside the repository');
fs.mkdirSync(OUT,{recursive:true});
const contentTypes={'.html':'text/html','.css':'text/css','.js':'application/javascript','.json':'application/json'};
const server=http.createServer((req,res)=>{
 const route=decodeURIComponent(new URL(req.url,'http://local.invalid').pathname);
 const file=path.resolve(ROOT,'.'+(route==='/'?'/index.html':route));
 if(!file.startsWith(ROOT+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
 res.setHeader('Content-Type',contentTypes[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));
});
let browser;
const checks=[],errors=[],external=[];
const check=(label,value)=>{assert.ok(value,label);checks.push(label);};
async function main(){
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin=`http://127.0.0.1:${server.address().port}`;
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1500,height:1080},deviceScaleFactor:1});
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.origin!==origin){external.push(url.origin);await route.abort();return;}
  if(url.pathname==='/assets/runtime-config.js'){await route.fulfill({contentType:'application/javascript',body:'window.STOCK_DASHBOARD_SUPABASE_CONFIG={};'});return;}
  if(url.pathname==='/data/stock-catalog.json'){await route.fulfill({contentType:'application/json',body:'[]'});return;}
  await route.continue();
 });
 const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
 await page.goto(origin,{waitUntil:'networkidle'});
 check('ordinary route has all seven tabs',await page.locator('.tab').count()===7);
 check('ordinary route has no real-looking private rows',await page.locator('#watchlistBody tr').count()===0);
 await page.locator('[data-tab=holdings]').click();
 check('signed-out private tab opens login',await page.locator('#loginDialog').evaluate(x=>x.open));
 await page.evaluate(()=>document.querySelector('#loginDialog').close());
 await page.evaluate(()=>{
  const rows=Array.from({length:8},(_,i)=>({code:String(600000+i),name:'合成标的'+String(i+1).padStart(2,'0'),shares:i===0?100:0,cost:i===0?9:0}));
  const symbols=rows.slice(0,3).map(x=>x.code+'.SH');
  const dates='2026-09-10';
  const snapshot={stocks:rows.map((x,i)=>({code:x.code,name:x.name,price:10+i,quoteAsOf:dates,weeklyBoll:{asOf:dates,lower:8+i,middle:10+i,upper:12+i},positions:{asOf:dates},forwardBasis:{status:'missing',amount:null},confirmedBasis:{status:'ready',amount:1,windowStart:'2025-09-10',windowEnd:dates}})),events:[],updatedAt:dates+'T18:00:00+08:00'};
  const projection={scope_key:'primary',projection_sequence:null,source_generated_at:null,positions:[]};
  let saved=structuredClone(rows);
  window.syntheticCalls=[];
  authSession={user:{id:'synthetic-only'}};
  catalog=[...rows.map(x=>({...x,market:'SH'})),{code:'600008',name:'合成追加',market:'SH',pinyin:'hechengzhuijia',initials:'hczj'}];
  trackedStocks=structuredClone(rows);market=snapshot;marketLoaded=true;
  currentUsername='本地合成验收';vpsAdmin=true;privateLoadState='ready';
  whitelistControl={desired_revision_no:2,desired_symbols:symbols,active_revision_no:1,active_symbols:symbols};
  runtimeDisplay={runtime:{health_status:'unknown',generated_at:null,mode:'DRY_RUN'},events:[]};
  personalMigrationState={source_files:1,watchlist_count:rows.length};
  personalLoadedParts=new Set(['holdings','positions','grid','calendar','news','strategy']);
  supabaseClient={rpc:async(name,args={})=>{
   window.syntheticCalls.push(name);
   const responses={personal_get_part1:{watchlist:saved},personal_get_part2:{groups:[],extraStocks:[]},personal_get_part4_v4:snapshot,personal_get_part5:{items:[],lastScanAt:null},personal_get_part6:{trades:{records:[]},feedback:{records:[]},analysis:{status:'waiting'},recommendations:{records:[]}},vps_is_admin:true,app_get_current_username:'本地合成验收',vps_private_get_portfolio:projection,vps_private_get_runtime_display:runtimeDisplay,vps_get_whitelist_control_state:whitelistControl,personal_get_migration_state:personalMigrationState,personal_get_refresh_health:null};
   if(name==='personal_replace_watchlist'){saved=structuredClone(args.p_items);return {data:{count:saved.length},error:null};}
   if(!(name in responses))throw new Error('Unexpected synthetic RPC: '+name);
   return {data:structuredClone(responses[name]),error:null};
  },auth:{signOut:async()=>{}}};
  updateAuthUI();render();document.querySelector('#updateText').textContent='本地合成数据交互验收 · 非线上账户';
 });
 await page.locator('[data-tab=today]').click();
 await page.screenshot({path:path.join(OUT,'part0-synthetic.png'),fullPage:true});
 await page.locator('#openPart1FromPart0').click();
 check('Part0 management link navigates to Part1',await page.locator('#holdings').evaluate(x=>x.classList.contains('active')));
 check('Part1 removes portfolio overview and cost form',await page.locator('#summaryCards,#addShares,#addCost').count()===0);
 check('real middle BOLL is ready',await page.locator('[data-watchlist-code="600000"]').innerText().then(t=>t.includes('BOLL已就绪')));
 check('mid-only is not ready',await page.evaluate(()=>{const b=market.stocks[0].weeklyBoll;market.stocks[0].weeklyBoll={asOf:b.asOf,lower:b.lower,mid:b.middle,upper:b.upper};const ok=watchlistDataStatus('600000',2).tone==='warning';market.stocks[0].weeklyBoll=b;return ok;}));
 await page.locator('#stockSearch').fill('hczj');
 await page.locator('[data-pick="600008"]').click();
 await page.locator('#showAdd').click();
 check('search add remains draft',await page.evaluate(()=>trackedStocks.length===8&&watchlistDraft.length===9));
 await page.locator('[data-delete-watchlist="600000"]').click();
 check('removal is reversible draft',await page.locator('[data-watchlist-code="600000"]').evaluate(x=>x.classList.contains('removed')));
 await page.locator('[data-delete-watchlist="600000"]').click();
 await page.locator('#saveChanges').click();
 check('save opens scoped confirmation',await page.locator('#watchlistSaveDialog').evaluate(x=>x.open));
 check('no write before confirmation',await page.evaluate(()=>!syntheticCalls.includes('personal_replace_watchlist')));
 await page.locator('#confirmWatchlistSave').click();
 await page.waitForFunction(()=>trackedStocks.length===9&&!watchlistSaving);
 check('save writes once and not to VPS',await page.evaluate(()=>syntheticCalls.filter(x=>x==='personal_replace_watchlist').length===1&&!syntheticCalls.some(x=>/vps_submit|sync_|append_/.test(x))));
 check('saved original metadata preserved',await page.evaluate(()=>trackedStocks.find(x=>x.code==='600000').cost===9));
 check('saved state is not VPS activation',await page.locator('#watchlistSync').innerText().then(t=>t.includes('待独立接入')&&t.includes('本次不提交目标')));
 await page.screenshot({path:path.join(OUT,'part1-synthetic.png'),fullPage:true});
 for(const width of [1500,1024,390]){
  await page.setViewportSize({width,height:1080});
  for(const tab of ['today','holdings','positions','grid','calendar','news','strategy']){
   await page.locator(`[data-tab=${tab}]`).click();
   const size=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth}));
   if(size.scroll>size.client){
    const detail=await page.evaluate(()=>[...document.querySelectorAll('.page.active *')].map(e=>({tag:e.tagName,id:e.id,cls:e.className,x:e.getBoundingClientRect().x,right:e.getBoundingClientRect().right,width:e.getBoundingClientRect().width,style:{minWidth:getComputedStyle(e).minWidth,display:getComputedStyle(e).display,overflow:getComputedStyle(e).overflow}})).filter(e=>e.right>innerWidth).slice(0,30));
    console.log(JSON.stringify({overflow:{width,tab,size,detail}}));
    await page.screenshot({path:path.join(OUT,'overflow-synthetic.png'),fullPage:true});
   }
   check(`${width}px ${tab}: no page overflow`,size.scroll<=size.client);
   check(`${width}px ${tab}: active page visible`,await page.locator(`#${tab}`).isVisible());
   if(tab==='holdings')check(`${width}px Part1: every removal button is inside the visible card`,await page.evaluate(()=>{const bounds=document.querySelector('#holdingList').getBoundingClientRect();return [...document.querySelectorAll('[data-delete-watchlist]')].every(x=>{const box=x.getBoundingClientRect();return box.right<=bounds.right&&box.left>=bounds.left;});}));
   if(tab==='today')check(`${width}px Part0: monitor cells are not clipped`,await page.evaluate(()=>[...document.querySelectorAll('.unified-monitor-table tr')].every(row=>[...row.children].every(cell=>cell.getBoundingClientRect().right<=row.closest('.unified-monitor-card').getBoundingClientRect().right))));
   if(width===390&&['today','holdings'].includes(tab))await page.screenshot({path:path.join(OUT,`${tab}-mobile-synthetic.png`),fullPage:true});
  }
 }
 check('Part2 uses full saved universe',await page.evaluate(()=>bollItems().length===9));
 check('Part3 includes pending new symbol',await page.locator('#gridBody tr').count()===9);
 check('Part5 selector follows saved identities',await page.locator('#newsStockFilter option').count()===10);
 check('Part6 stock selector follows saved identities',await page.locator('#tradeStock option').count()===9);
 await page.setViewportSize({width:1500,height:1080});
 await page.locator('[data-tab=today]').click();await page.locator('#reloadPart0Preview').click();
 await page.waitForFunction(()=>document.querySelector('#messageDialog').open);
 check('monitor refresh reports a read not a repaired VPS',await page.locator('#messageText').innerText().then(t=>t.includes('不表示VPS已修复')));
 await page.evaluate(()=>document.querySelector('#messageDialog').close());
 await page.locator('#loginButton').click();
 check('sign-out removes private rows',await page.locator('#watchlistBody tr').count()===0);
 check('no private data localStorage writes',await page.evaluate(()=>localStorage.length===0));
 check('no external requests',external.length===0);
 check('no page JavaScript errors',errors.length===0);
 const preview=await context.newPage();preview.on('pageerror',e=>errors.push(e.message));
 await preview.goto(origin+'/?part0-local-preview=1',{waitUntil:'networkidle'});
 check('strict local route remains visibly synthetic',await preview.locator('#part0PreviewBanner').innerText().then(t=>t.includes('固定合成名单')));
 check('strict local route has no session',await preview.evaluate(()=>authSession===null));
 const result={status:'passed',checks:checks.length,details:checks,syntheticOnly:true,hostedWrites:false,externalRequests:external.length,pageErrors:errors};
 fs.writeFileSync(path.join(OUT,'verification.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}
main().catch(error=>{console.error(error);fs.writeFileSync(path.join(OUT,'failure.json'),JSON.stringify({error:error.message,checks,errors,external},null,2));process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));});
