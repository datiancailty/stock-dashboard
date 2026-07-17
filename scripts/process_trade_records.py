#!/usr/bin/env python3
"""补齐历史成交日行情环境，并用服务端模型更新个人策略画像。"""
import json, os, re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT=Path(__file__).resolve().parents[1]
BJ=ZoneInfo('Asia/Shanghai')
TRADES=ROOT/'data/trade-records.json'
MARKET=ROOT/'data/market.json'
ANALYSIS=ROOT/'data/strategy-analysis.json'
MODEL='gpt-5.6-sol'
MODEL_URL='https://api.fenno.ai/v1/chat/completions'
HEADERS={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'}
KLINE_CACHE={}

def number(value):
    try:return float(value)
    except (TypeError,ValueError):return 0.0

def position_item(current,rows):
    if not rows:return None
    low=min(x['low'] for x in rows);high=max(x['high'] for x in rows)
    percent=50.0 if high<=low else max(0,min(100,(current-low)/(high-low)*100))
    zone='下部' if percent<100/3 else ('中部' if percent<200/3 else '上部')
    return {'zone':zone,'percent':round(percent,1),'low':round(low,3),'high':round(high,3)}

def kline_rows(code,target):
    cache_key=(code,target.isoformat())
    if cache_key in KLINE_CACHE:return KLINE_CACHE[cache_key]
    # 沪市可转债以 11 开头；其余现有记录按股票/基金常用代码前缀判断。
    market='sh' if code.startswith(('5','6','9','11')) else 'sz';secid=('1.' if market=='sh' else '0.')+code
    start=(target.replace(day=1)-timedelta(days=10)).strftime('%Y%m%d')
    end=(target+timedelta(days=10)).strftime('%Y%m%d')
    rows=[]
    try:
        params={'secid':secid,'klt':'101','fqt':'0','lmt':'1000','beg':start,'end':end,'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56','ut':'fa5fd1943c7b386f172d6893dbfba10b'}
        response=requests.get('https://push2his.eastmoney.com/api/qt/stock/kline/get',params=params,headers=HEADERS,timeout=20)
        response.raise_for_status();lines=(response.json().get('data') or {}).get('klines') or []
        for line in lines:
            cells=line.split(',')
            if len(cells)>=5:rows.append({'date':date.fromisoformat(cells[0]),'close':number(cells[2]),'high':number(cells[3]),'low':number(cells[4])})
    except (requests.RequestException,ValueError,TypeError):pass
    if rows:
        result=[x for x in rows if x['high']>0 and x['low']>0];KLINE_CACHE[cache_key]=result;return result
    try:
        symbol=market+code
        param=f'{symbol},day,{start[:4]}-{start[4:6]}-{start[6:]},{end[:4]}-{end[4:6]}-{end[6:]},80,'
        response=requests.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',params={'param':param},headers=HEADERS,timeout=20)
        response.raise_for_status();data=(response.json().get('data') or {}).get(symbol) or {};raw=data.get('day') or []
        for cells in raw:
            if len(cells)>=5:rows.append({'date':date.fromisoformat(cells[0]),'close':number(cells[2]),'high':number(cells[3]),'low':number(cells[4])})
    except (requests.RequestException,ValueError,TypeError):pass
    result=[x for x in rows if x['high']>0 and x['low']>0];KLINE_CACHE[cache_key]=result;return result

def historical_context(record,market_by_code):
    target=date.fromisoformat(record['date']);rows=[x for x in kline_rows(record['code'],target) if x['date']<=target]
    if not rows:raise RuntimeError('历史K线暂不可用')
    bar=rows[-1];iso=bar['date'].isocalendar();week=[x for x in rows if x['date'].isocalendar()[:2]==iso[:2]];month=[x for x in rows if (x['date'].year,x['date'].month)==(bar['date'].year,bar['date'].month)]
    price=number(record.get('price'));stock=market_by_code.get(record['code'],{});dps=number(record.get('dividendPerShare')) or number(stock.get('annualDividend'))+number(stock.get('interimDividend'))
    return {'status':'complete','requestedDate':record['date'],'asOf':bar['date'].isoformat(),'isExactTradingDate':bar['date']==target,'marketPriceAtRecord':price,'historicalClose':bar['close'],'totalDividend':dps,'yield':round(dps/price*100,6) if price else 0,'positions':{'asOf':bar['date'].isoformat(),'day':position_item(price,[bar]),'week':position_item(price,week),'month':position_item(price,month)},'source':'东方财富/腾讯公开不复权日K','enrichedAt':datetime.now(BJ).isoformat(timespec='seconds')}

def deterministic_learning(record):
    ctx=record.get('context') or {};y=number(ctx.get('yield'));day=((ctx.get('positions') or {}).get('day') or {}).get('zone','');action=record.get('action','')
    if action=='做T卖出' and day=='上部':return '日线上部做T卖出：强化高位减仓习惯'
    if action=='做T买入' and day=='下部':return '日线下部做T买入：强化回落接回习惯'
    if '买入' in action and y>=7:return '7%以上买入：强化高股息率高性价比偏好'
    if '买入' in action and y>=5:return '5%以上买入：强化分批建仓规则'
    if '卖出' in action and 0<y<=4.5:return '4%～4.5%卖出：强化清仓底线'
    return '已纳入策略画像，等待更多相似操作形成稳定规律'

def extract_json(text):
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip(),flags=re.I)
    try:return json.loads(text)
    except json.JSONDecodeError:
        start=text.find('{');end=text.rfind('}')
        if start>=0 and end>start:return json.loads(text[start:end+1])
        raise

def model_analysis(records,stocks):
    key=os.getenv('STRATEGY_MODEL_API_KEY')
    if not key:return None,'missing_secret'
    ordered=sorted(records,key=lambda r:(str(r.get('date') or ''),str(r.get('time') or ''),str(r.get('id') or '')),reverse=True)
    def brief(r):
        ctx=r.get('context') or {};positions=ctx.get('positions') or {}
        return {'id':r.get('id'),'date':r.get('date'),'code':r.get('code'),'name':r.get('name'),'action':r.get('action'),'price':r.get('price'),'shares':r.get('shares'),'yield':ctx.get('yield'),'day':(positions.get('day') or {}).get('zone'),'week':(positions.get('week') or {}).get('zone'),'month':(positions.get('month') or {}).get('zone')}
    # 旧记录不能因上下文变长而“失忆”：全量记录先汇总成统计证据，逐笔明细保留最近180笔。
    stock_stats={}
    for record in records:
        item=stock_stats.setdefault(str(record.get('code') or ''),{'code':record.get('code'),'name':record.get('name'),'buyCount':0,'sellCount':0,'buyShares':0,'sellShares':0,'buyAmount':0.0,'sellAmount':0.0,'buyZones':{},'sellZones':{}})
        side='buy' if '买入' in str(record.get('action') or '') else ('sell' if '卖出' in str(record.get('action') or '') else None)
        if not side:continue
        shares=int(number(record.get('shares')));price=number(record.get('price'));ctx=record.get('context') or {};zone=(((ctx.get('positions') or {}).get('day') or {}).get('zone'))
        item[side+'Count']+=1;item[side+'Shares']+=shares;item[side+'Amount']+=price*shares
        if zone:item[side+'Zones'][zone]=item[side+'Zones'].get(zone,0)+1
    for item in stock_stats.values():
        item['avgBuyPrice']=round(item.pop('buyAmount')/item['buyShares'],3) if item['buyShares'] else None
        item['avgSellPrice']=round(item.pop('sellAmount')/item['sellShares'],3) if item['sellShares'] else None
    all_stats={'recordCount':len(records),'buyCount':sum('买入' in str(r.get('action') or '') for r in records),'sellCount':sum('卖出' in str(r.get('action') or '') for r in records),'firstDate':min((str(r.get('date')) for r in records),default=None),'lastDate':max((str(r.get('date')) for r in records),default=None),'byStock':sorted(stock_stats.values(),key=lambda x:x['buyCount']+x['sellCount'],reverse=True)}
    compact_records=[brief(r) for r in ordered[:180]]
    compact_stocks=[{'code':s.get('code'),'name':s.get('name'),'price':s.get('price'),'yield':round((number(s.get('annualDividend'))+number(s.get('interimDividend')))/number(s.get('price'))*100,3) if number(s.get('price')) else 0,'positions':{p:((s.get('positions') or {}).get(p) or {}).get('zone') for p in ('day','week','month')}} for s in stocks]
    prompt={'strategyMode':'持续更新的个人交易复盘策略；每次新增记录都以全量统计重新生成','fixedPrinciples':['5%左右开始分批买入','接近7%属于高性价比区','结合日周月位置做T','4%～4.5%进入全部卖出区'],'allHistoryStats':all_stats,'dataCaveat':'历史股息率使用录入时保存的正式每股分红快照除以成交价，不等同于成交当日可知的历史分红口径；不要据此断言历史收益。','recentTradeRecords':compact_records,'currentStocks':compact_stocks,'task':'基于真实成交归纳可验证的操作偏好，形成“初步、可迭代”的个人策略，并给出今日条件式观察建议。必须说明证据数量；不能仅凭成交记录断言盈利，不能承诺收益，不能要求用户盲从，不能自动交易。建议应写成满足条件才执行、执行前由用户确认的形式。recordInsights只需覆盖最近20笔。只输出JSON。','schema':{'profileSummary':'string','learnedRules':['string'],'recordInsights':{'recent_record_id':'string'},'advice':[{'code':'string','action':'继续观察|等待接近5%|可分批买入|高性价比分批买|小仓分批/等待|做T卖出观察|卖出区提醒|暂不追入|等待正式数据','reason':'string','confidence':0}]}}
    request_body={'model':MODEL,'messages':[{'role':'system','content':'你是谨慎的个人投资复盘助手。区分事实、用户固定原则和待验证偏好；策略会随新增记录持续更新，但不代表模型权重训练。输出严格JSON，不输出思维链。'},{'role':'user','content':json.dumps(prompt,ensure_ascii=False)}],'temperature':0.2,'max_tokens':3500,'response_format':{'type':'json_object'}}
    response=None
    for attempt in range(2):
        try:
            response=requests.post(MODEL_URL,headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json=request_body,timeout=180);response.raise_for_status();break
        except requests.RequestException:
            if attempt:raise
    if response is None:raise requests.RequestException('模型请求未返回结果')
    body=response.json();content=body['choices'][0]['message']['content'];return extract_json(content),None

def main():
    payload=json.loads(TRADES.read_text()) if TRADES.exists() else {'version':1,'records':[]};records=payload.get('records') or []
    market=json.loads(MARKET.read_text()) if MARKET.exists() else {'stocks':[]};stocks=market.get('stocks') or [];market_by_code={s.get('code'):s for s in stocks}
    changed=False
    for record in records:
        context=record.get('context') or {}
        if context.get('status')=='complete' and context.get('requestedDate')==record.get('date'):continue
        try:record['context']=historical_context(record,market_by_code);record['learning']=deterministic_learning(record);changed=True
        except (ValueError,RuntimeError,requests.RequestException) as error:
            record['context']={**context,'status':'retry','requestedDate':record.get('date'),'error':'历史行情暂不可用，将在下次任务重试'};changed=True
    now=datetime.now(BJ).isoformat(timespec='seconds')
    if changed:payload['updatedAt']=now;TRADES.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    try:analysis,error=model_analysis(records,stocks)
    except (requests.RequestException,ValueError,KeyError,IndexError,json.JSONDecodeError) as exc:analysis,error=None,type(exc).__name__
    if analysis:
        insights=analysis.get('recordInsights') or {}
        for record in records:
            if str(record.get('id')) in insights:record['modelInsight']=str(insights[str(record['id'])])[:500];changed=True
        if changed:payload['updatedAt']=now;TRADES.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
        tracked_codes={str(s.get('code')) for s in stocks};allowed_actions={'继续观察','等待接近5%','可分批买入','高性价比分批买','小仓分批/等待','做T卖出观察','卖出区提醒','暂不追入','等待正式数据'};safe_advice=[]
        for item in analysis.get('advice') or []:
            if not isinstance(item,dict) or str(item.get('code')) not in tracked_codes:continue
            action=str(item.get('action') or '继续观察');confidence=max(0,min(100,number(item.get('confidence'))))
            safe_advice.append({'code':str(item['code']),'action':action if action in allowed_actions else '继续观察','reason':str(item.get('reason') or '')[:600],'confidence':round(confidence,1)})
        output={'updatedAt':now,'model':MODEL,'status':'success','profileSummary':str(analysis.get('profileSummary') or '')[:1500],'learnedRules':[str(x)[:500] for x in (analysis.get('learnedRules') or [])][:12],'advice':safe_advice[:30]}
    else:
        rules=[]
        for record in records:
            text=record.get('learning') or deterministic_learning(record)
            if text not in rules:rules.append(text)
        buy_count=sum('买入' in str(r.get('action') or '') for r in records);sell_count=sum('卖出' in str(r.get('action') or '') for r in records);name_counts={}
        for record in records:name_counts[str(record.get('name') or record.get('code') or '未知')]=name_counts.get(str(record.get('name') or record.get('code') or '未知'),0)+1
        top_names='、'.join(name for name,_ in sorted(name_counts.items(),key=lambda x:x[1],reverse=True)[:5]);date_values=[str(r.get('date')) for r in records if r.get('date')]
        fallback_profile=f'已纳入{len(records)}笔真实成交（买入{buy_count}笔、卖出{sell_count}笔），记录跨度{min(date_values) if date_values else "待积累"}至{max(date_values) if date_values else "待积累"}；操作较多的标的包括{top_names or "待积累"}。模型分析暂不可用，以下仅保留固定规则和可验证统计，后续任务会自动重试。'
        output={'updatedAt':now,'model':MODEL if os.getenv('STRATEGY_MODEL_API_KEY') else None,'status':'rules_only','profileSummary':fallback_profile,'learnedRules':rules[:12],'advice':[],'retryReason':error}
    ANALYSIS.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'ok':True,'records':len(records),'enriched':sum((r.get('context') or {}).get('status')=='complete' for r in records),'modelStatus':output['status']},ensure_ascii=False))

if __name__=='__main__':main()
