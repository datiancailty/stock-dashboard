#!/usr/bin/env python3
"""中国核电、中国广核、中国平安、东阿阿胶的日/周/月位置专项学习。

信号在收盘后形成，收益从下一交易日开盘开始计算，避免用信号日收盘价造成未来函数。
数据来源为东方财富官方公开历史K线接口；妙想skill调用额度耗尽时使用此官方公开接口作为回退。
"""
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
import requests

ROOT=Path(__file__).resolve().parents[1]
RAW_DIR=ROOT/'analysis/mx-focused-v2'
OUT=ROOT/'data/strategy-backtest-new-focused-stocks.json'
END=date.today()+timedelta(days=1)
REFRESH='--refresh' in sys.argv
STOCKS={
    '601985': {'name':'中国核电','market':1},
    '003816': {'name':'中国广核','market':0},
    '601318': {'name':'中国平安','market':1},
    '000423': {'name':'东阿阿胶','market':0},
}
API='https://push2his.eastmoney.com/api/qt/stock/kline/get'
HEADERS={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'}
PREFERRED_SIGNAL='周下+日下+月中'
VARIANT_DEFS=[
    {'signal':'周下+日下+月中','week':'下部','day':'下部','month':'中部','preferred':True},
    {'signal':'周下+日下+月下','week':'下部','day':'下部','month':'下部'},
    {'signal':'周下+日中+月中','week':'下部','day':'中部','month':'中部'},
    {'signal':'周下+日中+月下','week':'下部','day':'中部','month':'下部'},
    {'signal':'周中+日下+月中','week':'中部','day':'下部','month':'中部'},
    {'signal':'周中+日下+月下','week':'中部','day':'下部','month':'下部'},
    {'signal':'周中+日中+月中','week':'中部','day':'中部','month':'中部'},
    {'signal':'周中+日中+月下','week':'中部','day':'中部','month':'下部'},
]


def fetch(code,meta):
    RAW_DIR.mkdir(parents=True,exist_ok=True)
    path=RAW_DIR/f'{code}.json'
    if path.exists() and not REFRESH:
        cached=json.loads(path.read_text(encoding='utf-8')); rows=[]
        for r in cached.get('rows',[]):
            rows.append({'date':date.fromisoformat(str(r['date'])[:10]),'open':float(r['open']),'close':float(r['close']),'high':float(r['high']),'low':float(r['low'])})
        if len(rows)>=100: return rows,cached.get('source','本地缓存历史K线')
    secid=f"{meta['market']}.{code}"
    market_prefix='sh' if meta['market']==1 else 'sz'
    params={
        'secid':secid,'klt':'101','fqt':'1','lmt':'2000',
        'beg':'20220101','end':END.strftime('%Y%m%d'),
        'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56',
        'ut':'fa5fd1943c7b386f172d6893dbfba10b',
    }
    payload=None; source=None
    for host in ('https://push2his.eastmoney.com','https://push2.eastmoney.com','https://push2delay.eastmoney.com'):
        try:
            response=requests.get(host+'/api/qt/stock/kline/get',params=params,headers=HEADERS,timeout=25)
            response.raise_for_status(); candidate=response.json(); klines=(candidate.get('data') or {}).get('klines') or []
            if klines:
                payload=candidate; source='东方财富官方公开历史K线接口（前复权fqt=1）'; break
        except (requests.RequestException,ValueError,TypeError):
            continue
    if payload is None:
        # 官方东方财富历史端点临时拒绝时，使用公开行情镜像取得同一日线序列，并在结果中保留来源边界。
        try:
            response=requests.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',params={'param':f'{market_prefix}{code},day,2022-01-01,{END.isoformat()},2000,'},headers=HEADERS,timeout=30)
            response.raise_for_status(); candidate=response.json(); key=f'{market_prefix}{code}'; klines=((candidate.get('data') or {}).get(key) or {}).get('day') or []
            if klines:
                payload={'data':{'klines':[','.join(map(str,row)) for row in klines]}}; source='东方财富妙想调用受限；使用腾讯公开历史日K回退（未提供前复权参数）'
        except (requests.RequestException,ValueError,TypeError) as error:
            raise RuntimeError(f'{code}历史K线获取失败: {type(error).__name__}') from error
    klines=(payload.get('data') or {}).get('klines') or []
    if not klines: raise RuntimeError(f'{code}未返回历史K线')
    rows=[]
    for line in klines:
        cells=line.split(',') if isinstance(line,str) else line
        if len(cells)<6: continue
        try:
            rows.append({'date':date.fromisoformat(str(cells[0])[:10]),'open':float(cells[1]),'close':float(cells[2]),'high':float(cells[3]),'low':float(cells[4])})
        except (TypeError,ValueError):
            continue
    rows=sorted([r for r in rows if r['high']>=r['low']>0 and r['open']>0 and r['close']>0],key=lambda x:x['date'])
    if len(rows)<100: raise RuntimeError(f'{code}有效K线太少: {len(rows)}')
    path.write_text(json.dumps({'source':source,'secid':secid,'adjustment':'前复权(fqt=1)' if source and '东方财富官方' in source else '镜像接口原始日K，未提供前复权参数','requestedStart':'2022-01-01','requestedEnd':END.isoformat(),'rows':rows},ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    return rows,source


def position(close, bars):
    low=min(x['low'] for x in bars); high=max(x['high'] for x in bars)
    pct=50.0 if high<=low else max(0,min(100,(close-low)/(high-low)*100))
    zone='下部' if pct<100/3 else ('中部' if pct<200/3 else '上部')
    return zone,pct


def state_for(rows,i):
    r=rows[i]; prior=rows[:i+1]
    iso=r['date'].isocalendar()[:2]
    week=[x for x in prior if x['date'].isocalendar()[:2]==iso]
    month=[x for x in prior if (x['date'].year,x['date'].month)==(r['date'].year,r['date'].month)]
    dz,dp=position(r['close'],[r]); wz,wp=position(r['close'],week); mz,mp=position(r['close'],month)
    return {'day':dz,'week':wz,'month':mz,'percent':{'day':round(dp,1),'week':round(wp,1),'month':round(mp,1)}}


def pct(v): return round(v*100,2) if v is not None else None


def stats(samples):
    out={'signals':len(samples)}
    for n in (5,10,20,30,60):
        vals=[x['forward'].get(n) for x in samples if x['forward'].get(n) is not None]
        out[f'd{n}']={'n':len(vals),'medianPct':pct(statistics.median(vals)) if vals else None,'winPct':round(sum(v>0 for v in vals)/len(vals)*100,1) if vals else None}
    mfe=[x['max30'] for x in samples if x.get('max30') is not None]
    dd=[x['min30'] for x in samples if x.get('min30') is not None]
    out['max30MedianPct']=pct(statistics.median(mfe)) if mfe else None
    out['drawdown30MedianPct']=pct(statistics.median(dd)) if dd else None
    return out


def make_samples(rows, predicate, states=None):
    samples=[]
    for i,r in enumerate(rows):
        state=states[i] if states is not None else state_for(rows,i)
        if r['date']<date(2022,1,1) or not predicate(state): continue
        entry_i=i+1
        if entry_i>=len(rows): continue
        entry=rows[entry_i]
        forward={}
        for n in (5,10,20,30,60):
            exit_i=entry_i+n-1
            if exit_i<len(rows): forward[n]=rows[exit_i]['close']/entry['open']-1
        path=rows[entry_i:min(len(rows),entry_i+30)]
        samples.append({'signalDate':str(r['date']),'signalClose':r['close'],'entryDate':str(entry['date']),'entryOpen':entry['open'],'state':state_for(rows,i),'forward':forward,'max30':max((x['high']/entry['open']-1 for x in path),default=None),'min30':min((x['low']/entry['open']-1 for x in path),default=None)})
    return samples


def cluster(samples):
    episodes=[]
    for s in samples:
        current=date.fromisoformat(s['signalDate'])
        if not episodes or (current-date.fromisoformat(episodes[-1][-1]['signalDate'])).days>4:
            episodes.append([s])
        else: episodes[-1].append(s)
    return episodes


def year_stats(samples):
    grouped=defaultdict(list)
    for s in samples: grouped[s['signalDate'][:4]].append(s)
    return {year:stats(items) for year,items in sorted(grouped.items())}


def variant_summary(rows, states, definition):
    predicate=lambda st: st['day']==definition['day'] and st['week']==definition['week'] and st['month']==definition['month']
    samples=make_samples(rows,predicate,states)
    episodes=cluster(samples)
    first=[episode[0] for episode in episodes]
    return {
        'signal':definition['signal'],
        'preferred':bool(definition.get('preferred')),
        'signalDays':stats(samples),
        'episodes':len(episodes),
        'episodeFirst':stats(first),
        'yearStats':year_stats(first),
    }


def state_matrix(rows):
    grouped=defaultdict(list)
    for i,r in enumerate(rows):
        if r['date']<date(2022,1,1): continue
        st=state_for(rows,i); key='/'.join(st[x] for x in ('day','week','month'))
        # Avoid separate sample construction to keep the state exactly tied to this date.
        entry_i=i+1
        if entry_i>=len(rows): continue
        entry=rows[entry_i]; path=rows[entry_i:min(len(rows),entry_i+30)]
        forward={}
        for n in (5,20,30):
            ei=entry_i+n-1
            if ei<len(rows): forward[n]=rows[ei]['close']/entry['open']-1
        grouped[key].append({'signalDate':str(r['date']),'entryDate':str(entry['date']),'entryOpen':entry['open'],'forward':forward,'max30':max((x['high']/entry['open']-1 for x in path),default=None),'min30':min((x['low']/entry['open']-1 for x in path),default=None)})
    out=[]
    for key,items in grouped.items():
        episodes=cluster(items)
        if len(episodes)<5: continue
        item={'state':key,'signalDays':len(items),'episodes':len(episodes),'all':stats(items),'episodeFirst':stats([e[0] for e in episodes])}
        out.append(item)
    return sorted(out,key=lambda x:(-x['episodes'],x['all']['d20']['medianPct'] if x['all']['d20']['medianPct'] is not None else -999))


def classify(s):
    a=s['primary']['episodeFirst']; d20=a['d20']; d30=a['d30']; years=s['primary'].get('yearStats',{}); latest=years.get(max(years)) if years else None
    # Full-history优势如果在最近一个有>=3个信号簇的年度转弱，先降级，避免把旧行情特征直接当成当前买点。
    if latest is not None and latest['signals']>=3 and d20['n']>=8 and (d20['medianPct'] or -999)>0 and ((latest['d20']['medianPct'] is not None and latest['d20']['medianPct']<0) or (latest['d20']['winPct'] is not None and latest['d20']['winPct']<50)):
        return {'tier':'历史有优势但近期降级','window':'等待当前状态确认','rule':'全历史主信号曾有优势，但最近年度转弱；不单独买入，先等待日线止跌、股息率/250日线重新满足，并用新信号簇复核。','basis':f"全历史20日{d20['winPct']}%/{d20['medianPct']}%，最近年度20日{latest['d20']['winPct']}%/{latest['d20']['medianPct']}%"}
    if d20['n']>=8 and (d20['winPct'] or 0)>=60 and (d20['medianPct'] or -999)>0:
        return {'tier':'首批候选','window':'5—20个交易日','rule':'主信号出现后进入首批候选窗口；仍需正式股息率、250日线、核心/机动仓身份和用户确认。','basis':f"独立信号簇20日胜率{d20['winPct']}%，中位收益{d20['medianPct']}%"}
    if d30['n']>=8 and (d30['winPct'] or 0)>=60 and (d30['medianPct'] or -999)>0:
        return {'tier':'耐心候选','window':'20—30个交易日','rule':'主信号出现后不以5日结果否定，观察20—30日；只允许条件式分批复核。','basis':f"独立信号簇30日胜率{d30['winPct']}%，中位收益{d30['medianPct']}%"}
    return {'tier':'观察/需确认','window':'至少等待止跌或新信号簇','rule':'主信号不能单独触发买入；增加止跌确认或股息率/250日线联合条件后再人工复核。','basis':f"独立信号簇20日{d20['winPct']}%/{d20['medianPct']}%，30日{d30['winPct']}%/{d30['medianPct']}%"}


def analyze(code,meta):
    rows,source=fetch(code,meta)
    states=[state_for(rows,i) for i in range(len(rows))]
    preferred=VARIANT_DEFS[0]
    predicate=lambda st: st['day']==preferred['day'] and st['week']==preferred['week'] and st['month']==preferred['month']
    samples=make_samples(rows,predicate,states); eps=cluster(samples)
    first=[e[0] for e in eps]
    primary={'signal':PREFERRED_SIGNAL,'signalDays':stats(samples),'episodeFirst':stats(first),'episodeDates':[[e[0]['signalDate'],e[-1]['signalDate'],len(e)] for e in eps],'yearStats':year_stats(first)}
    result={'code':code,'name':meta['name'],'source':source,'sourceRows':len(rows),'sourceRange':[str(rows[0]['date']),str(rows[-1]['date'])],'primary':primary,'variantStudy':{'preferredSignal':PREFERRED_SIGNAL,'testedSignals':[item['signal'] for item in VARIANT_DEFS],'variants':[variant_summary(rows,states,item) for item in VARIANT_DEFS]},'stateMatrix':state_matrix(rows)}
    result['learnedClassification']=classify(result)
    result['latestState']=state_for(rows,len(rows)-1)
    return result


def main():
    result={'schemaVersion':2,'generatedAt':datetime.now().astimezone().isoformat(timespec='seconds'),'dataSource':'东方财富官方公开历史K线接口（前复权fqt=1）；东方财富妙想API本次因调用额度达到上限未返回数据','evaluationWindow':'2022-01-01至接口最新交易日','entryMethod':'信号日收盘后形成信号，下一交易日开盘进入；收益不含分红、费用、滑点','indicatorDefinition':{'day':'当日最高/最低区间内收盘价百分位','week':'当前ISO周截至信号日的最高/最低区间内收盘价百分位','month':'当前自然月截至信号日的最高/最低区间内收盘价百分位','zone':'下部<33.33%，中部为33.33%至<66.67%，上部≥66.67%','primarySignal':PREFERRED_SIGNAL,'testedSignalSpace':'周中/下 + 日中/下 + 月中/下，共8种组合','antiLookahead':'周/月只使用信号日及之前数据；收益从下一交易日开盘计算；连续4个自然日内的信号合并为同一观察窗口'},'stocks':[analyze(code,meta) for code,meta in STOCKS.items()]}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'stocks':[(s['name'],s['sourceRange'],s['primary']['episodeFirst']['signals'],s['learnedClassification']['tier']) for s in result['stocks']],'output':str(OUT)},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
