'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {JSDOM}=require('jsdom');const ROOT=path.resolve(__dirname,'..');
test('calendar renders three distinct implementation dates with no payment inference',async t=>{
 const dom=new JSDOM(fs.readFileSync(path.join(ROOT,'index.html'),'utf8'),{url:'https://synthetic.invalid',runScripts:'outside-only',pretendToBeVisual:true});
 t.after(()=>dom.window.close());dom.window.fetch=async url=>{assert.equal(url,'data/stock-catalog.json');return {ok:true,json:async()=>[]};};
 const run=s=>vm.runInContext(s,dom.getInternalVMContext());run(fs.readFileSync(path.join(ROOT,'assets/app.js'),'utf8'));
 await new Promise(r=>setTimeout(r,0));
 const dates=[['股权登记日','2026-09-25'],['除权除息日','2026-09-28'],['派息日','2026-09-29']];
 dom.window.events=dates.map(([type,date])=>({id:'synthetic:'+type,code:'600000',name:'合成甲',type,date,description:'结构化日期核对 <img src=x onerror=alert(1)>',source:'东方财富公司公告 + F10分红日期核对',sourceUrl:'https://data.eastmoney.com/notices/detail/600000/AN202609200000000001.html'}));
 run("authSession={user:{id:'synthetic'}};trackedStocks=[{code:'600000',name:'合成甲'}];personalMigrationState={source_files:1};personalLoadedParts.add('calendar');market.events=events;viewMonth=new Date(2026,8,1);selectedDate='2026-09-25';renderCalendar();");
 for(const [type,date] of dates){
  const button=dom.window.document.querySelector(`[data-day='${date}']`);assert.ok(button.classList.contains('has-event'));button.click();
  const list=dom.window.document.querySelector('#eventList');assert.equal(list.querySelectorAll('.event').length,1);assert.ok(list.textContent.includes(type));
  for(const [other] of dates)if(type!==other)assert.ok(!list.textContent.includes(other));
  assert.equal(list.querySelectorAll('img').length,0);assert.equal(list.querySelector('a').textContent,'原公告 ↗');
 }
 run("selectedDate='2026-09-30';renderCalendar();");assert.match(dom.window.document.querySelector('#eventList').textContent,/当天没有/);
 run("trackedStocks=[];selectedDate='2026-09-29';renderCalendar();");assert.equal(dom.window.document.querySelectorAll('#eventList .event').length,0);
});
