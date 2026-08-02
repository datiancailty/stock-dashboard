#!/usr/bin/env python3
"""回测画像策略的既有位置口径：收盘价在当日/当周截至当日/当月截至当日高低区间中的三等分位置。"""
import glob,json,re,statistics
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCES={'600887':ROOT/'analysis/mx-yili','600036':ROOT/'analysis/mx-cmb','000538':ROOT/'analysis/mx-baiyao'}
NAMES={'600887':'伊利股份','600036':'招商银行','000538':'云南白药'}

def num(v):
 m=re.search(r'-?\d+(?:\.\d+)?',str(v).replace(',','')) if v is not None else None
 return float(m.group()) if m else None

def load(code,folder):
 p=glob.glob(str(folder/'*raw.json'))[0]
 root=json.load(open(p,encoding='utf-8')); tabs=root['data']['data']['searchDataResultDTO']['dataTableDTOList']
 t=next(t for t in tabs if t.get('code','').startswith(code) and {'开盘价','最高价','最低价','收盘价'}<=set(t.get('nameMap',{}).values()))
 inv={v:k for k,v in t['nameMap'].items()}; rows=[]
 for i,s in enumerate(t['table']['headName']):
  rows.append({'date':datetime.strptime(s[:10],'%Y-%m-%d').date(),**{k:num(t['table'][inv[v]][i]) for k,v in [('open','开盘价'),('high','最高价'),('low','最低价'),('close','收盘价')]}})
 return sorted(rows,key=lambda x:x['date'])

def position(current,rows):
 lo=min(x['low'] for x in rows); hi=max(x['high'] for x in rows)
 pct=50 if hi<=lo else max(0,min(100,(current-lo)/(hi-lo)*100))
 return ('下部' if pct<100/3 else ('中部' if pct<200/3 else '上部')),pct

def analyze(code):
 rows=load(code,SOURCES[code]); signals=[]
 for i,r in enumerate(rows):
  if r['date']<datetime(2024,1,1).date(): continue
  iso=r['date'].isocalendar()[:2]
  week=[x for x in rows[:i+1] if x['date'].isocalendar()[:2]==iso]
  month=[x for x in rows[:i+1] if (x['date'].year,x['date'].month)==(r['date'].year,r['date'].month)]
  dz,dp=position(r['close'],[r]); wz,wp=position(r['close'],week); mz,mp=position(r['close'],month)
  if (dz,wz,mz)==('下部','下部','中部'):
   f={n:(rows[i+n]['close']/r['close']-1) if i+n<len(rows) else None for n in (5,10,20,30,60)}
   path=rows[i+1:min(len(rows),i+31)]
   signals.append({'date':str(r['date']),'close':r['close'],'positionPct':{'day':round(dp,1),'week':round(wp,1),'month':round(mp,1)},'forward':f,'max30':max((x['high']/r['close']-1 for x in path),default=None),'min30':min((x['low']/r['close']-1 for x in path),default=None)})
 episodes=[]
 for x in signals:
  if not episodes or (datetime.fromisoformat(x['date']).date()-datetime.fromisoformat(episodes[-1][-1]['date']).date()).days>4: episodes.append([x])
  else: episodes[-1].append(x)
 def stats(samples):
  z={'signals':len(samples)}
  for n in (5,10,20,30,60):
   vals=[x['forward'][n] for x in samples if x['forward'][n] is not None]
   z[f'd{n}']={'n':len(vals),'medianPct':round(statistics.median(vals)*100,2) if vals else None,'winPct':round(sum(v>0 for v in vals)/len(vals)*100,1) if vals else None}
  ups=[x['max30'] for x in samples if x['max30'] is not None]; dds=[x['min30'] for x in samples if x['min30'] is not None]
  z['max30MedianPct']=round(statistics.median(ups)*100,2) if ups else None; z['drawdown30MedianPct']=round(statistics.median(dds)*100,2) if dds else None
  return z
 return {'code':code,'name':NAMES[code],'sourceRows':len(rows),'sourceRange':[str(rows[0]['date']),str(rows[-1]['date'])],'allSignalDays':stats(signals),'episodeFirstSignal':stats([e[0] for e in episodes]),'episodes':len(episodes),'episodeDates':[[e[0]['date'],e[-1]['date'],len(e)] for e in episodes]}

result={'schemaVersion':1,'generatedAt':datetime.now().astimezone().isoformat(timespec='seconds'),'dataSource':'东方财富妙想金融数据（前复权日线）','evaluationWindow':'2024-01-01至数据最新交易日','indicatorDefinition':{'position':'当前收盘价在高低区间中的百分位；下部<33.33%，中部为33.33%至<66.67%，上部≥66.67%','day':'当日最高/最低','week':'ISO周内截至当日最高/最低','month':'自然月内截至当日最高/最低','signal':'周下+日下+月中','antiLookahead':'周/月只使用截至信号日已经出现的数据；未来收益使用前复权收盘价；不计分红、费用和滑点。'},'stocks':[analyze(c) for c in SOURCES]}
out=ROOT/'data/strategy-backtest-week-day-down-month-mid.json'; out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
