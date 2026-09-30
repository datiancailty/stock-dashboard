/* Real browser acceptance of the actual page. No external connection allowed. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('playwright');const {fixture}=require('./part0_test_fixture.cjs');
const ROOT=path.resolve(__dirname,'..'),OUT=process.env.DASHBOARD_TEST_OUTPUT;
if(!OUT||!path.isAbsolute(OUT)||path.resolve(OUT).startsWith(ROOT+path.sep))throw new Error('private output must be outside repository');
fs.mkdirSync(OUT,{recursive:true});
const payload=process.env.PART0_PRIVATE_CAPTURE?JSON.parse(fs.readFileSync(process.env.PART0_PRIVATE_CAPTURE,'utf8')).payload:fixture();
const actual=!!process.env.PART0_PRIVATE_CAPTURE;
const names=actual?Object.fromEntries(JSON.parse(fs.readFileSync(path.join(ROOT,'data/stock-catalog.json'),'utf8')).map(x=>[x.code,x.name])):{'600000':'合成甲','600001':'合成乙'};
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://local.invalid'),file=path.resolve(ROOT,'.'+(url.pathname==='/'?'/index.html':decodeURIComponent(url.pathname)));
 if(!file.startsWith(ROOT+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
 res.setHeader('Content-Type',({'.html':'text/html','.css':'text/css','.js':'application/javascript','.json':'application/json'})[path.extname(file)]||'text/plain');res.end(fs.readFileSync(file));
});
let browser;let checks=0;const errors=[],external=[];
const check=(value,message)=>{assert.ok(value,message);checks++;};
(async()=>{try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:1536,height:1100},deviceScaleFactor:1.5});
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());if(url.origin!==origin){external.push(url.origin);await route.abort();return;}
  if(url.pathname==='/assets/runtime-config.js'){await route.fulfill({contentType:'application/javascript',body:'window.STOCK_DASHBOARD_SUPABASE_CONFIG={};'});return;}
  await route.continue();
 });
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin,{waitUntil:'networkidle'});
 check(await page.locator('.tab').count()===7,'full nav');check(await page.locator('#part0PositionsBody tr').count()===0,'no public holdings');
 await page.evaluate(({payload,names,actual})=>{
  authSession={user:{id:'local-private-render-only'}};part0Monitor=payload;part0MonitorState='ready';part0TradeDate='2026-09-30';
  currentUsername='本地验收';privateLoadState='ready';catalog=Object.entries(names).map(([code,name])=>({code,name}));
  window.testCalls=[];supabaseClient={rpc:async name=>{testCalls.push(name);const data={personal_get_part0_monitor:payload,vps_private_get_portfolio:null,vps_private_get_runtime_display:null,vps_is_admin:false,app_get_current_username:'本地验收',personal_get_migration_state:{source_files:0},personal_get_refresh_health:null}[name];if(data===undefined)throw Error('unexpected RPC');return {data,error:null};},auth:{signOut:async()=>{}}};
  document.querySelector('#updateText').textContent=actual?'本地验收 · VPS只读快照（未上线）':'本地合成验收（非实际账户）';updateAuthUI();renderTodayBoard();
 },{payload,names,actual});
 check(await page.locator('#part0PositionsBody tr').count()===payload.account.positions.length,'real projection row count');
 const expectedFills=payload.trades.filter(x=>x.tradeDate==='2026-09-30').length;
 check(await page.locator('[data-part0-fill]').count()===expectedFills,'fill count');
 await page.locator('[data-part0-filter=sell]').click();check((await page.locator('#part0TradeRows').innerText()).includes('无卖出成交'),'honest empty sell state');
 await page.locator('[data-part0-filter=all]').click();
 check((await page.locator('#part0Health').innerText()).includes('到期停用'),'expiry not green enabled');
 await page.locator('.part0-past-error summary').click();check(await page.locator('.part0-past-error').evaluate(x=>x.open),'history expandable');await page.locator('.part0-past-error summary').click();
 const widths=[];
 for(const width of [1536,1100,785,390,375]){
  await page.setViewportSize({width,height:1100});const size=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth}));
  check(size.scroll<=size.client,`no page overflow ${width}`);widths.push({width,...size});
  check(await page.locator('#part0PositionsBody').evaluate(body=>{const card=body.closest('.part0-card').getBoundingClientRect();return [...body.querySelectorAll('td')].every(e=>{const b=e.getBoundingClientRect();return b.width>0&&b.right<=card.right&&b.left>=card.left;});}),`all holding metrics visible inside card ${width}`);
  if(width===1536||width===390)await page.screenshot({path:path.join(OUT,width===1536?'part0-desktop.png':'part0-mobile.png'),fullPage:true});
 }
 await page.locator('#reloadPart0Preview').click();await page.waitForFunction(()=>document.querySelector('#messageDialog').open);
 check(await page.evaluate(()=>testCalls.includes('personal_get_part0_monitor')&&!testCalls.some(x=>/submit|sync_|trade|cancel/.test(x))),'refresh only getters');
 await page.evaluate(()=>document.querySelector('#messageDialog').close());await page.locator('#loginButton').click();
 check(await page.locator('#part0PositionsBody tr').count()===0,'signout wipes positions');check(await page.evaluate(()=>localStorage.length===0),'no private storage');
 const preview=await context.newPage();await preview.addInitScript(()=>{window.fetch=()=>{throw new Error('preview network forbidden');};});
 preview.on('pageerror',e=>errors.push(e.message));await preview.goto(origin+'/?part0-local-preview=1',{waitUntil:'networkidle'});
 check((await preview.locator('#part0PreviewBanner').innerText()).includes('固定合成名单'),'offline preview explicit');
 check(external.length===0,'no external requests');check(errors.length===0,'no JavaScript errors');
 const result={verified:true,checks,widths,externalRequests:external.length,pageErrors:errors,privateSnapshot:actual,hostedUsed:false};fs.writeFileSync(path.join(OUT,'verification.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(error){console.error(error);process.exitCode=1;}finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}})();
