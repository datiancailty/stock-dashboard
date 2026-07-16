#!/usr/bin/env python3
"""用东方财富妙想一次批量更新 A 股价格、上一完整年度分红和分红事件。"""
import json, os, re, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT=Path(__file__).resolve().parents[1]
API='https://mkapi2.dfcfs.com/finskillshub/api/claw/query'
BJ=ZoneInfo('Asia/Shanghai')
DIVIDEND_BATCH_SIZE=5

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

def parse(payload, stocks, year, previous, prices):
    old={s['code']:s for s in previous.get('stocks',[])}
    by={s['code']:{**s,'price':prices.get(s['code'],old.get(s['code'],{}).get('price',0)),'fiscalYear':year,'annualDividend':old.get(s['code'],{}).get('annualDividend',0),'interimDividend':old.get(s['code'],{}).get('interimDividend',0),'source':'东方财富公开行情 + mx-data'} for s in stocks}
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
    batch,next_cursor=dividend_batch(stocks,previous)
    payload=api_query('、'.join(s['name'] for s in batch),year)
    parsed,events=parse(payload,stocks,year,previous,prices)
    result={'updatedAt':now.isoformat(timespec='seconds'),'source':'东方财富公开批量行情 + 妙想分红明细','strategy':'上一完整年度已实施的年报与中报税前每股股利之和','dividendCursor':next_cursor,'dividendBatch':[s['code'] for s in batch],'stocks':parsed,'events':events}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'ok':True,'updatedAt':result['updatedAt'],'stocks':len(parsed),'prices':len(prices),'dividendBatch':result['dividendBatch'],'events':len(events)},ensure_ascii=False))

if __name__=='__main__': main()
