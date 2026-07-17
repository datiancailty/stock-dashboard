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
    market='sh' if code.startswith(('5','6','9')) else 'sz';secid=('1.' if market=='sh' else '0.')+code
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
    if rows:return [x for x in rows if x['high']>0 and x['low']>0]
    try:
        symbol=market+code
        param=f'{symbol},day,{start[:4]}-{start[4:6]}-{start[6:]},{end[:4]}-{end[4:6]}-{end[6:]},80,'
        response=requests.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',params={'param':param},headers=HEADERS,timeout=20)
        response.raise_for_status();data=(response.json().get('data') or {}).get(symbol) or {};raw=data.get('day') or []
        for cells in raw:
            if len(cells)>=5:rows.append({'date':date.fromisoformat(cells[0]),'close':number(cells[2]),'high':number(cells[3]),'low':number(cells[4])})
    except (requests.RequestException,ValueError,TypeError):pass
    return [x for x in rows if x['high']>0 and x['low']>0]

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
    compact_records=[{'date':r.get('date'),'code':r.get('code'),'name':r.get('name'),'action':r.get('action'),'price':r.get('price'),'shares':r.get('shares'),'yield':(r.get('context') or {}).get('yield'),'positions':(r.get('context') or {}).get('positions')} for r in records]
    compact_stocks=[{'code':s.get('code'),'name':s.get('name'),'price':s.get('price'),'yield':round((number(s.get('annualDividend'))+number(s.get('interimDividend')))/number(s.get('price'))*100,3) if number(s.get('price')) else 0,'positions':s.get('positions')} for s in stocks]
    prompt={'fixedPrinciples':['5%左右开始分批买入','接近7%属于高性价比区','结合日周月位置做T','4%～4.5%进入全部卖出区'],'tradeRecords':compact_records,'currentStocks':compact_stocks,'task':'从用户真实成交中归纳个人偏好，并给出今日观察建议。不要承诺收益，不要自动交易。只输出JSON。','schema':{'profileSummary':'string','learnedRules':['string'],'recordInsights':{'record_id':'string'},'advice':[{'code':'string','action':'string','reason':'string','confidence':0}]}}
    response=requests.post(MODEL_URL,headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json={'model':MODEL,'messages':[{'role':'system','content':'你是个人投资复盘助手。区分事实、用户固定原则和归纳偏好；输出严格JSON，不输出思维链。'},{'role':'user','content':json.dumps(prompt,ensure_ascii=False)}],'temperature':0.2,'max_tokens':3000,'response_format':{'type':'json_object'}},timeout=120)
    response.raise_for_status();body=response.json();content=body['choices'][0]['message']['content'];return extract_json(content),None

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
        output={'updatedAt':now,'model':MODEL,'status':'success','profileSummary':str(analysis.get('profileSummary') or ''),'learnedRules':[str(x) for x in (analysis.get('learnedRules') or [])][:12],'advice':(analysis.get('advice') or [])[:30]}
    else:
        rules=[]
        for record in records:
            text=record.get('learning') or deterministic_learning(record)
            if text not in rules:rules.append(text)
        output={'updatedAt':now,'model':MODEL if os.getenv('STRATEGY_MODEL_API_KEY') else None,'status':'rules_only','profileSummary':'模型分析暂不可用；历史行情补全和固定规则建议仍正常运行。','learnedRules':rules[:12],'advice':[],'retryReason':error}
    ANALYSIS.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'ok':True,'records':len(records),'enriched':sum((r.get('context') or {}).get('status')=='complete' for r in records),'modelStatus':output['status']},ensure_ascii=False))

if __name__=='__main__':main()
