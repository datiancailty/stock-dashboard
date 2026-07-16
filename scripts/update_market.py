#!/usr/bin/env python3
"""用东方财富妙想一次批量更新 A 股价格、上一完整年度分红和分红事件。"""
import json, os, re, sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT=Path(__file__).resolve().parents[1]
API='https://mkapi2.dfcfs.com/finskillshub/api/claw/query'
BJ=ZoneInfo('Asia/Shanghai')
DIVIDEND_BATCH_SIZE=5
KLINE_API='https://push2his.eastmoney.com/api/qt/stock/kline/get'

def number(v):
    if v in (None,'','-'): return 0.0
    m=re.search(r'-?\d+(?:\.\d+)?',str(v).replace(',',''))
    return float(m.group()) if m else 0.0

def api_query(names, year):
    # 分红查询控制在小批量，避免股票较多时妙想结果表被截断。
    q=f"{names}{year}年度分红明细，列出年度分配和中期分配的方案进度、每股股利税前、分红方案、股权登记日、除权除息日、派息日"
    r=requests.post(API,headers={'apikey':os.environ['MX_APIKEY'],'Content-Type':'application/json'},json={'toolQuery':q},timeout=45)
    r.raise_for_status(); data=r.json()
    if data.get('status')!=0: raise RuntimeError(f"妙想API错误: {data.get('status')} {data.get('message')}")
    return data

def fetch_prices(stocks):
    """通过东方财富公开行情接口取齐全部价格；批量失败时逐只补齐。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'}
    secid=lambda s:('1.' if s['code'].startswith(('5','6','9')) else '0.')+s['code']
    secids=','.join(secid(s) for s in stocks)
    params={'fltt':'2','invt':'2','fields':'f12,f14,f2','secids':secids}
    prices={}
    for host in ('https://push2delay.eastmoney.com','https://push2.eastmoney.com'):
        try:
            r=requests.get(host+'/api/qt/ulist.np/get',params=params,headers=headers,timeout=25)
            r.raise_for_status()
            for row in (r.json().get('data') or {}).get('diff') or []:
                price=number(row.get('f2'))
                if price>0: prices[str(row.get('f12'))]=price
            if len(prices)==len(stocks): return prices
        except (requests.RequestException,ValueError,TypeError):
            continue
    missing=[s for s in stocks if s['code'] not in prices]
    def one(s):
        for host in ('https://push2delay.eastmoney.com','https://push2.eastmoney.com'):
            try:
                r=requests.get(host+'/api/qt/stock/get',params={'fltt':'2','invt':'2','fields':'f43,f57,f58','secid':secid(s)},headers=headers,timeout=20)
                r.raise_for_status(); row=r.json().get('data') or {}; price=number(row.get('f43'))
                if price>0: return s['code'],price
            except (requests.RequestException,ValueError,TypeError):
                continue
        return s['code'],0
    with ThreadPoolExecutor(max_workers=5) as pool:
        for future in as_completed(pool.submit(one,s) for s in missing):
            code,price=future.result()
            if price>0: prices[code]=price
    if not prices: raise RuntimeError('东方财富公开行情接口未返回任何有效价格')
    return prices

def position_item(current, rows):
    """计算当前价在给定K线集合最高/最低价中的百分位与上中下分区。"""
    if not rows: return None
    low=min(row['low'] for row in rows); high=max(row['high'] for row in rows)
    if high<=low: percent=50.0
    else: percent=max(0.0,min(100.0,(current-low)/(high-low)*100))
    zone='下部' if percent<100/3 else ('中部' if percent<200/3 else '上部')
    return {'zone':zone,'percent':round(percent,1),'low':round(low,3),'high':round(high,3)}

def fetch_positions(stocks, prices, previous):
    """并发读取日K，计算最近交易日、当前交易周、当前交易月的位置。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'}
    old={s.get('code'):s.get('positions') for s in previous.get('stocks',[])}
    def one(stock):
        code=stock['code']; market_prefix='sh' if code.startswith(('5','6','9')) else 'sz'; secid=('1.' if market_prefix=='sh' else '0.')+code
        lines=[]
        try:
            r=requests.get(KLINE_API,params={'secid':secid,'klt':'101','fqt':'0','lmt':'45','end':'20500101','fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56','ut':'fa5fd1943c7b386f172d6893dbfba10b'},headers=headers,timeout=18)
            r.raise_for_status(); lines=(r.json().get('data') or {}).get('klines') or []
        except (requests.RequestException,ValueError,TypeError):
            pass
        # 东方财富历史接口偶发限流时，使用腾讯公开不复权日K补齐，字段口径一致。
        if not lines:
            try:
                symbol=market_prefix+code
                r=requests.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',params={'param':f'{symbol},day,,,45,'},headers=headers,timeout=18)
                r.raise_for_status(); data=(r.json().get('data') or {}).get(symbol) or {}; raw=data.get('day') or []
                lines=[','.join(map(str,row[:6])) for row in raw]
            except (requests.RequestException,ValueError,TypeError):
                return code,old.get(code)
        try:
            rows=[]
            for line in lines:
                cells=line.split(',')
                if len(cells)<5: continue
                rows.append({'date':date.fromisoformat(cells[0]),'high':number(cells[3]),'low':number(cells[4])})
            rows=[row for row in rows if row['high']>0 and row['low']>0]
            if not rows: return code,old.get(code)
            latest=rows[-1]['date']; iso=latest.isocalendar(); current=prices.get(code) or number(lines[-1].split(',')[2])
            week=[row for row in rows if row['date'].isocalendar()[:2]==iso[:2]]
            month=[row for row in rows if (row['date'].year,row['date'].month)==(latest.year,latest.month)]
            return code,{'asOf':latest.isoformat(),'day':position_item(current,[rows[-1]]),'week':position_item(current,week),'month':position_item(current,month)}
        except (ValueError,TypeError,IndexError):
            return code,old.get(code)
    result={}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for future in as_completed(pool.submit(one,stock) for stock in stocks):
            code,value=future.result()
            if value: result[code]=value
    return result

def dividend_batch(stocks, previous):
    """优先补新股票，其余按游标轮转；每次仍只消耗一次妙想调用。"""
    old_codes={s.get('code') for s in previous.get('stocks',[])}
    new=[s for s in stocks if s['code'] not in old_codes]
    if new:
        selected=new[:DIVIDEND_BATCH_SIZE]
        start=stocks.index(selected[-1])+1
    else:
        start=int(previous.get('dividendCursor',0))%max(len(stocks),1)
        selected=[stocks[(start+i)%len(stocks)] for i in range(min(DIVIDEND_BATCH_SIZE,len(stocks)))] if stocks else []
        start+=len(selected)
    return selected,start%max(len(stocks),1)

def result_dtos(payload):
    return payload.get('data',{}).get('data',{}).get('searchDataResultDTO',{}).get('dataTableDTOList',[])

def code_from_label(label, stocks):
    for s in stocks:
        if s['name'] in str(label) or s['code'] in str(label): return s['code']
    return None

def parse(payload, stocks, year, previous, prices, positions):
    old={s['code']:s for s in previous.get('stocks',[])}
    by={s['code']:{**s,'price':prices.get(s['code'],old.get(s['code'],{}).get('price',0)),'positions':positions.get(s['code'],old.get(s['code'],{}).get('positions')),'fiscalYear':year,'annualDividend':old.get(s['code'],{}).get('annualDividend',0),'interimDividend':old.get(s['code'],{}).get('interimDividend',0),'source':'东方财富公开行情/日K + mx-data'} for s in stocks}
    events=[]
    for dto in result_dtos(payload):
        table=dto.get('table') or {}; field=(dto.get('field') or {}).get('returnName',''); title=dto.get('title') or ''
        heads=table.get('headName') or []
        pretax=table.get('每股股利(税前,元)') or table.get('每股股利(税前)')
        plans=table.get('分红方案') or []
        if not isinstance(pretax,list): continue
        progress=table.get('方案进度') or ['']*len(heads)
        reg=table.get('股权登记日') or ['']*len(heads)
        exd=table.get('除权除息日') or ['']*len(heads)
        pay=table.get('派息日') or ['']*len(heads)
        code=code_from_label(title,stocks) or code_from_label(dto.get('code',''),stocks)
        if not code: continue
        for i,label in enumerate(heads):
            label=str(label); value=number(pretax[i] if i<len(pretax) else 0)
            # 分红方案中的“10派X元”通常比展示型每股字段保留更多精度，优先使用。
            if i<len(plans):
                pm=re.search(r'10派\s*([0-9]+(?:\.[0-9]+)?)\s*元',str(plans[i]))
                if pm: value=float(pm.group(1))/10
            implemented=(i>=len(progress) or not progress[i] or '实施' in str(progress[i]))
            if label==f'{year}年度分配' and implemented: by[code]['annualDividend']=value
            if label==f'{year}中期分配' and implemented: by[code]['interimDividend']=value
            if label in (f'{year}年度分配',f'{year}中期分配') and value>0:
                for datev,typ in ((reg[i] if i<len(reg) else '','股权登记日'),(exd[i] if i<len(exd) else '','除权除息日'),(pay[i] if i<len(pay) else '','派息日')):
                    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(datev)):
                        events.append({'date':datev,'code':code,'name':by[code]['name'],'type':typ,'amount':None,'description':f'{label} · 每股税前 {value:g}元'})
    merged_events={f"{e.get('date')}|{e.get('code')}|{e.get('type')}":e for e in previous.get('events',[])}
    for event in events: merged_events[f"{event.get('date')}|{event.get('code')}|{event.get('type')}"]=event
    return list(by.values()),list(merged_events.values())

def main():
    key=os.getenv('MX_APIKEY');
    if not key: raise SystemExit('缺少 MX_APIKEY')
    stocks=json.loads((ROOT/'data/stocks.json').read_text())
    out=ROOT/'data/market.json'; previous=json.loads(out.read_text()) if out.exists() else {}
    now=datetime.now(BJ); year=now.year-1
    prices=fetch_prices(stocks)
    positions=fetch_positions(stocks,prices,previous)
    batch,next_cursor=dividend_batch(stocks,previous)
    payload=api_query('、'.join(s['name'] for s in batch),year)
    parsed,events=parse(payload,stocks,year,previous,prices,positions)
    result={'updatedAt':now.isoformat(timespec='seconds'),'source':'东方财富公开批量行情 + 东方财富/腾讯公开日K + 妙想分红明细','strategy':'上一完整年度已实施的年报与中报税前每股股利之和','positionStrategy':'当前价在最近交易日、当前交易周、当前交易月最高最低价区间的位置；下部<33.33%，中部<66.67%，其余为上部','dividendCursor':next_cursor,'dividendBatch':[s['code'] for s in batch],'stocks':parsed,'events':events}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'ok':True,'updatedAt':result['updatedAt'],'stocks':len(parsed),'prices':len(prices),'positions':len(positions),'dividendBatch':result['dividendBatch'],'events':len(events)},ensure_ascii=False))

if __name__=='__main__': main()
