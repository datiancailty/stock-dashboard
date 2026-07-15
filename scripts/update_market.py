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

def number(v):
    if v in (None,'','-'): return 0.0
    m=re.search(r'-?\d+(?:\.\d+)?',str(v).replace(',',''))
    return float(m.group()) if m else 0.0

def api_query(names, year):
    q=f"{names}A股最新价，以及{year}年度分红明细，列出年度分配和中期分配的方案进度、每股股利税前、股权登记日、除权除息日、派息日"
    r=requests.post(API,headers={'apikey':os.environ['MX_APIKEY'],'Content-Type':'application/json'},json={'toolQuery':q},timeout=45)
    r.raise_for_status(); data=r.json()
    if data.get('status')!=0: raise RuntimeError(f"妙想API错误: {data.get('status')} {data.get('message')}")
    return data

def result_dtos(payload):
    return payload.get('data',{}).get('data',{}).get('searchDataResultDTO',{}).get('dataTableDTOList',[])

def code_from_label(label, stocks):
    for s in stocks:
        if s['name'] in str(label) or s['code'] in str(label): return s['code']
    return None

def parse(payload, stocks, year, previous):
    by={s['code']:{**s,'price':0,'fiscalYear':year,'annualDividend':0,'interimDividend':0,'source':'mx-data'} for s in stocks}
    events=[]
    for dto in result_dtos(payload):
        table=dto.get('table') or {}; field=(dto.get('field') or {}).get('returnName',''); title=dto.get('title') or ''
        # 行情表：既支持“多个股票为列”，也支持“单股票指标为列”。
        if '价' in field or '价' in title:
            heads=table.get('headName') or []
            for key,vals in table.items():
                if key=='headName' or not isinstance(vals,list) or not vals: continue
                code=code_from_label(key,stocks) or code_from_label(title,stocks)
                if code: by[code]['price']=number(vals[0])
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
    old={s['code']:s for s in previous.get('stocks',[])}
    for code,s in by.items():
        if not s['price']: s['price']=old.get(code,{}).get('price',0)
        if not s['annualDividend'] and not s['interimDividend']:
            s['annualDividend']=old.get(code,{}).get('annualDividend',0);s['interimDividend']=old.get(code,{}).get('interimDividend',0)
    return list(by.values()),events or previous.get('events',[])

def main():
    key=os.getenv('MX_APIKEY');
    if not key: raise SystemExit('缺少 MX_APIKEY')
    stocks=json.loads((ROOT/'data/stocks.json').read_text())
    out=ROOT/'data/market.json'; previous=json.loads(out.read_text()) if out.exists() else {}
    now=datetime.now(BJ); year=now.year-1
    payload=api_query('、'.join(s['name'] for s in stocks),year)
    parsed,events=parse(payload,stocks,year,previous)
    result={'updatedAt':now.isoformat(timespec='seconds'),'source':'东方财富妙想','strategy':'上一完整年度已实施的年报与中报税前每股股利之和','stocks':parsed,'events':events}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'ok':True,'updatedAt':result['updatedAt'],'stocks':len(parsed),'events':len(events)},ensure_ascii=False))

if __name__=='__main__': main()
