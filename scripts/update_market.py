#!/usr/bin/env python3
"""用东方财富妙想一次批量更新 A 股价格、上一完整年度分红和分红事件。"""
import json, os, re, statistics, sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT=Path(__file__).resolve().parents[1]
API='https://mkapi2.dfcfs.com/finskillshub/api/claw/query'
BJ=ZoneInfo('Asia/Shanghai')
DIVIDEND_QUERY_BATCH_SIZE=5
KLINE_API='https://push2his.eastmoney.com/api/qt/stock/kline/get'

# These fields are used only by Part 2/3 forward-looking grids.  They never
# change Part 1's formal dividend display or any strategy/formal-yield rule.
IMPLEMENTED_PROGRESS_ALLOWLIST={'实施分配','已实施','实施完成','已完成'}
# Only these explicit disclosed plan stages may enter Part 2/3 future grids.
# Unknown wording fails closed; Part 4 remains the factual notice ledger.
DISCLOSED_FUTURE_PROGRESS_ALLOWLIST={'董事会预案','董事会通过','股东大会通过','预案通过','方案通过','待实施'}
NON_IMPLEMENTED_PROGRESS_TERMS=('不实施','未实施','取消','终止','预案','拟','待实施','预披露','董事会','股东大会')

def implementation_status(progress):
    """Return True only for an explicit, non-negated implementation state."""
    normalized=re.sub(r'\s+','',str(progress or ''))
    if not normalized or any(term in normalized for term in NON_IMPLEMENTED_PROGRESS_TERMS):
        return False
    return normalized in IMPLEMENTED_PROGRESS_ALLOWLIST

def planned_status(progress):
    """Accept only explicitly approved/disclosed future-plan states."""
    normalized=re.sub(r'\s+','',str(progress or ''))
    return normalized in DISCLOSED_FUTURE_PROGRESS_ALLOWLIST

def number(v):
    if v in (None,'','-'): return 0.0
    m=re.search(r'-?\d+(?:\.\d+)?',str(v).replace(',',''))
    return float(m.group()) if m else 0.0

def api_query(names, year):
    # 分红查询控制在小批量，避免股票较多时妙想结果表被截断。
    q=f"{names}{year}年度及{year+1}年中期现金分红明细，列出年度分配和中期分配的方案进度、每股股利税前、分红方案、股权登记日、除权除息日、派息日"
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

def load_part2_config():
    path=ROOT/'data/part2-config.json'
    if not path.exists(): return {'groups':[],'extraStocks':[]}
    try:
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else {'groups':[],'extraStocks':[]}
    except (OSError,json.JSONDecodeError):
        return {'groups':[],'extraStocks':[]}

def merge_market_stocks(holdings, config):
    """Part 1 标的单向进入 Part 2；Part 2 独立标的不反写 Part 1。"""
    result=[]; seen=set()
    for item in [*holdings,*(config.get('extraStocks') or [])]:
        code=str(item.get('code','')); name=str(item.get('name','')).strip()
        if re.fullmatch(r'\d{6}',code) and name and code not in seen:
            result.append({'code':code,'name':name}); seen.add(code)
    return result

def weekly_boll_from_daily(rows, sample_count=20):
    """用前复权日K按ISO周锚定周收盘，计算BOLL(20,2)样本标准差。"""
    by_week={}
    for row in rows:
        d=row['date']; by_week[d.isocalendar()[:2]]=row
    weekly=[by_week[key] for key in sorted(by_week)]
    if len(weekly)<sample_count: return None
    sample=weekly[-sample_count:]; closes=[row['close'] for row in sample]
    middle=statistics.mean(closes); stddev=statistics.stdev(closes)
    return {'asOf':sample[-1]['date'].isoformat(),'basis':'前复权周K','period':20,'multiplier':2,'stddev':'sample','sampleCount':sample_count,'upper':round(middle+2*stddev,3),'middle':round(middle,3),'lower':round(middle-2*stddev,3)}

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

def fetch_weekly_boll(stocks, previous):
    """并发读取前复权日K并聚合周收盘；失败时保留最近一次有效BOLL。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'}
    old={s.get('code'):s.get('weeklyBoll') for s in previous.get('stocks',[])}
    def one(stock):
        code=stock['code']; secid=('1.' if code.startswith(('5','6','9')) else '0.')+code
        try:
            r=requests.get(KLINE_API,params={'secid':secid,'klt':'101','fqt':'1','lmt':'180','end':'20500101','fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56','ut':'fa5fd1943c7b386f172d6893dbfba10b'},headers=headers,timeout=20)
            r.raise_for_status(); lines=(r.json().get('data') or {}).get('klines') or []
        except (requests.RequestException,ValueError,TypeError):
            lines=[]
        # 东方财富前复权K线偶发断连时，用腾讯前复权周K补齐。
        if not lines:
            try:
                market_prefix='sh' if code.startswith(('5','6','9')) else 'sz'; symbol=market_prefix+code
                r=requests.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',params={'param':f'{symbol},week,,,40,qfq'},headers=headers,timeout=20)
                r.raise_for_status(); data=(r.json().get('data') or {}).get(symbol) or {}; raw=data.get('qfqweek') or []
                rows=[]
                for values in raw:
                    if len(values)<3: continue
                    d=date.fromisoformat(str(values[0])); close=number(values[2])
                    if close>0: rows.append({'date':d,'close':close})
                return code,weekly_boll_from_daily(rows) or old.get(code)
            except (requests.RequestException,ValueError,TypeError,statistics.StatisticsError):
                return code,old.get(code)
        try:
            rows=[]
            for line in lines:
                cells=line.split(',')
                if len(cells)<5: continue
                d=date.fromisoformat(cells[0]); close=number(cells[2])
                if close>0: rows.append({'date':d,'close':close})
            return code,weekly_boll_from_daily(rows) or old.get(code)
        except (ValueError,TypeError,statistics.StatisticsError):
            return code,old.get(code)
    result={}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for future in as_completed(pool.submit(one,stock) for stock in stocks):
            code,value=future.result()
            if value: result[code]=value
    return result

def dividend_query_batches(stocks):
    """Return every current symbol once; query-size batching is not scan rotation.

    A batch is only an upstream transport bound.  A successful market refresh
    must cover every symbol, otherwise it preserves prior dividend values and
    marks the dividend section degraded instead of claiming a fresh snapshot.
    """
    return [stocks[index:index+DIVIDEND_QUERY_BATCH_SIZE] for index in range(0,len(stocks),DIVIDEND_QUERY_BATCH_SIZE)]

def result_dtos(payload):
    return payload.get('data',{}).get('data',{}).get('searchDataResultDTO',{}).get('dataTableDTOList',[])


def table_values(dto, *labels):
    """Read a named MX table column from either literal or coded keys."""
    table=dto.get('table') or {}
    for label in labels:
        value=table.get(label)
        if isinstance(value,list): return value
    name_map=dto.get('nameMap') or {}
    if isinstance(name_map,list): name_map={str(i):value for i,value in enumerate(name_map)}
    if isinstance(name_map,dict):
        wanted={str(label).strip() for label in labels}
        for key,mapped in name_map.items():
            if str(mapped).strip() in wanted and key in table and isinstance(table[key],list):
                return table[key]
    return []


def code_from_label(label, stocks):
    for s in stocks:
        if s['name'] in str(label) or s['code'] in str(label): return s['code']
    return None


def assert_dividend_dto_coverage(dtos, stocks):
    """Fail closed unless upstream returned an identifiable DTO per symbol.

    A zero future dividend is valid; a missing stock response is not proof of
    zero.  Callers must therefore verify source coverage before rebuilding any
    forward-dividend snapshot.
    """
    expected={str(stock['code']) for stock in stocks}
    observed=set()
    for dto in dtos:
        if not isinstance(dto,dict):
            continue
        code=code_from_label(dto.get('title',''),stocks) or code_from_label(dto.get('code',''),stocks)
        if code:
            observed.add(code)
    if observed != expected:
        raise RuntimeError('dividend_source_coverage_incomplete')


def dividend_period(label):
    """Normalize MX period labels such as 2025年度分配/2025年报/2026年中期分配."""
    text=re.sub(r'\s+','',str(label or ''))
    match=re.search(r'(20\d{2})(?:年)?(年度分配|年报|年度|中期分配|中报|半年度|中期)',text)
    if not match: return None
    period_year=int(match.group(1)); suffix=match.group(2)
    period_kind='annual' if suffix in ('年度分配','年报','年度') else 'interim'
    return period_year,period_kind


def parse(payload, stocks, year, previous, prices, positions, weekly_boll):
    old={s['code']:s for s in previous.get('stocks',[])}
    by={s['code']:{**s,'price':prices.get(s['code'],old.get(s['code'],{}).get('price',0)),'positions':positions.get(s['code'],old.get(s['code'],{}).get('positions')),'weeklyBoll':weekly_boll.get(s['code'],old.get(s['code'],{}).get('weeklyBoll')),'fiscalYear':year,'annualDividend':old.get(s['code'],{}).get('annualDividend',0),'interimDividend':old.get(s['code'],{}).get('interimDividend',0),'futureDividend':old.get(s['code'],{}).get('futureDividend',0),'futureDividendStatus':old.get(s['code'],{}).get('futureDividendStatus'),'source':'东方财富公开行情/前复权日K + mx-data'} for s in stocks}
    events=[]
    for dto in result_dtos(payload):
        table=dto.get('table') or {}; field=(dto.get('field') or {}).get('returnName',''); title=dto.get('title') or ''
        heads=table.get('headName') or []
        pretax=table_values(dto,'每股股利(税前,元)','每股股利(税前)')
        plans=table_values(dto,'分红方案')
        if not isinstance(pretax,list): continue
        progress=table_values(dto,'方案进度','分红方案进度') or ['']*len(heads)
        reg=table_values(dto,'股权登记日') or ['']*len(heads)
        exd=table_values(dto,'除权除息日') or ['']*len(heads)
        pay=table_values(dto,'派息日') or ['']*len(heads)
        code=code_from_label(title,stocks) or code_from_label(dto.get('code',''),stocks)
        if not code: continue
        for i,label in enumerate(heads):
            label=str(label); period=dividend_period(label)
            if not period: continue
            period_year,period_kind=period
            # Keep the formal Part 1—3 numerator on the previous complete
            # fiscal year, while retaining the current-year implemented
            # distribution events for the Part 4 calendar.
            if period_year < year: continue
            value=number(pretax[i] if i<len(pretax) else 0)
            # 分红方案中的“10派X元”通常比展示型每股字段保留更多精度，优先使用。
            if i<len(plans):
                pm=re.search(r'10派\s*([0-9]+(?:\.[0-9]+)?)\s*元',str(plans[i]))
                if pm: value=float(pm.group(1))/10
            progress_value=str(progress[i] if i<len(progress) else '').strip()
            # An implementation event needs an explicit non-negated source
            # status.  A disclosed future plan is recorded separately for the
            # Part 2/3 forward grid and never changes formal dividends.
            implemented=implementation_status(progress_value)
            future_plan=period_year > year and planned_status(progress_value)
            if period_year == year and implemented:
                if period_kind=='annual': by[code]['annualDividend']=value
                else: by[code]['interimDividend']=value
            if future_plan and value>0:
                by[code]['futureDividend']=round(float(by[code].get('futureDividend') or 0)+value,6)
                by[code]['futureDividendStatus']='已公告待实施'
            if implemented and value>0:
                for datev,typ in ((reg[i] if i<len(reg) else '','股权登记日'),(exd[i] if i<len(exd) else '','除权除息日'),(pay[i] if i<len(pay) else '','派息日')):
                    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(datev)):
                        events.append({'id':f'mx-implementation:{code}:{period_year}-{period_kind}:{typ}:{datev}','date':datev,'code':code,'name':by[code]['name'],'type':typ,'amount':None,'description':f'{label} · 每股税前 {value:g}元'})
    def event_key(event):
        # Preserve two different annual/interim distributions that legitimately
        # share a stock, date and event type; only merge exact semantic copies.
        return '|'.join(str(event.get(key,'')) for key in ('date','code','type','description'))
    merged_events={event_key(e):e for e in previous.get('events',[])}
    for event in events: merged_events[event_key(event)]=event
    return list(by.values()),list(merged_events.values())

def main():
    key=os.getenv('MX_APIKEY');
    if not key: raise SystemExit('缺少 MX_APIKEY')
    holdings=json.loads((ROOT/'data/stocks.json').read_text())
    part2_config=load_part2_config(); stocks=merge_market_stocks(holdings,part2_config)
    out=ROOT/'data/market.json'; previous=json.loads(out.read_text()) if out.exists() else {}
    now=datetime.now(BJ); year=now.year-1
    prices=fetch_prices(stocks)
    positions=fetch_positions(stocks,prices,previous)
    weekly_boll=fetch_weekly_boll(stocks,previous)
    dividend_batches=dividend_query_batches(stocks)
    dividend_warning=None
    payloads=[]
    failed_batches=[]
    for batch in dividend_batches:
        try:
            payloads.append(api_query('、'.join(s['name'] for s in batch),year))
        except (requests.RequestException,RuntimeError,ValueError,KeyError) as error:
            failed_batches.append(type(error).__name__)
    if failed_batches:
        # Never publish a partially refreshed dividend universe as complete.
        # Quotes/BOLL may still advance, but all formal and forward dividend
        # values remain from the last full coverage snapshot.
        payload={'result':[]}
        dividend_warning=f'分红明细覆盖不完整，已保留上次完整快照：{len(failed_batches)}/{len(dividend_batches)}批失败'
    else:
        merged_dtos=[]
        for payload_item in payloads:
            merged_dtos.extend(result_dtos(payload_item))
        try:
            assert_dividend_dto_coverage(merged_dtos, stocks)
        except RuntimeError as error:
            failed_batches.append(str(error))
            payload={'result':[]}
            dividend_warning='分红明细覆盖不完整，已保留上次完整快照：源结果未覆盖全部股票'
        else:
            payload={'data':{'data':{'searchDataResultDTO':{'dataTableDTOList':merged_dtos}}}}
    previous_for_parse=previous
    if not failed_batches:
        # Full coverage means both formal and future dividend values are rebuilt
        # from the current structured source, rather than silently retaining a
        # stale value that disappeared or was corrected upstream.
        previous_for_parse={**previous,'stocks':[{**item,'annualDividend':0,'interimDividend':0,'futureDividend':0,'futureDividendStatus':None} for item in previous.get('stocks',[])]}
    parsed,events=parse(payload,stocks,year,previous_for_parse,prices,positions,weekly_boll)
    result={'updatedAt':now.isoformat(timespec='seconds'),'source':'东方财富公开批量行情 + 东方财富/腾讯公开日K + mx-data','strategy':'正式口径：上一完整年度已实施的年报与中报税前每股股利之和；Part 2/3 另列已公告待实施分红，仅用于未来测算','positionStrategy':'当前价在最近交易日、当前交易周、当前交易月最高最低价区间的位置；下部<33.33%，中部<66.67%，其余为上部','weeklyBollStrategy':'前复权日K按ISO周取周收盘；最近20周；BOLL(20,2)；样本标准差(n-1)','dividendWarning':dividend_warning,'dividendCoverage':{'expected':len(stocks),'covered':0 if failed_batches else len(stocks),'complete':not failed_batches,'queryBatches':len(dividend_batches)},'stocks':parsed,'events':events}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'ok':True,'updatedAt':result['updatedAt'],'stocks':len(parsed),'prices':len(prices),'positions':len(positions),'weeklyBoll':len(weekly_boll),'dividendCoverage':result['dividendCoverage'],'events':len(events)},ensure_ascii=False))

if __name__=='__main__': main()
