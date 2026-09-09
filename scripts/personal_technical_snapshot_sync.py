#!/usr/bin/env python3
"""HiThink forward daily -> Dashboard weekly BOLL; raw daily -> range positions.

Dry run by default. No MX, AI, VPS coupling or immutable-archive modification.
A new trusted RPC migration and separate authorization are required to publish.
"""
from __future__ import annotations
from datetime import datetime, date
import dashboard_data_sources as source
from dashboard_technical_indicators import compute_boll
from update_market import position_item

def parse_args(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--publish',action='store_true')
    group.add_argument('--dry-run',action='store_true')
    return parser.parse_args(argv)


def parse_history(body, code, adjustment, first_day, last_day, now):
    symbol=source.canonical(code)
    if not isinstance(body,dict) or type(body.get('code')) is not int or body['code']!=0:
        raise ValueError('technical_business_failure')
    data=body.get('data')
    if not isinstance(data,dict) or not isinstance(data.get('item'),list) or not data['item']:
        raise ValueError('technical_history_empty')
    source.history_ready_time(data.get('timestamp'),now,last_day)
    rows=[];previous=None
    for item in data['item']:
        if not isinstance(item,dict) or type(item.get('date_ms')) is not int:raise ValueError('technical_bar_invalid')
        if item.get('thscode',symbol)!=symbol or item.get('currency','CNY')!='CNY':raise ValueError('technical_identity_or_currency')
        if item.get('adjustment',adjustment)!=adjustment:raise ValueError('technical_adjustment_mismatch')
        d=datetime.fromtimestamp(item['date_ms']/1000,source.BJ)
        if d.hour or d.minute or d.second or not first_day<=d.date().isoformat()<=last_day:
            raise ValueError('technical_bar_date_invalid')
        if previous is not None and d.date()<=previous:raise ValueError('technical_date_order_invalid')
        row={'date':d.date()}
        for field in ('open','close','high','low'):row[field]=source.finite_price(item.get(field+'_price'))
        if not row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']:
            raise ValueError('technical_ohlc_invalid')
        rows.append(row);previous=d.date()
    return rows

def collect(stocks, now, *, request=None, key_reader=None, cache_dir=None, sleep=None):
    import json, os, hashlib, stat, tempfile, time
    from pathlib import Path
    request=request or source.request_json
    sleep=sleep or time.sleep
    codes=[s.code for s in stocks];source.universe(codes)
    key=(key_reader or source.read_hithink_key)()
    headers={'X-api-key':key}
    calendar=request(source.HITHINK+'/api/a-share/calendar/trading-days',{},headers)
    last=source.latest_trading_day(now,request=lambda *a:calendar,key_reader=lambda:key)
    dates=[datetime.strptime(r['date'],'%Y%m%d').date().isoformat() for r in calendar['data']['item']
           if datetime.strptime(r['date'],'%Y%m%d').date().isoformat()<=last]
    # Latest-complete session only. An intraday bar cannot masquerade as EOD.
    if last==now.astimezone(source.BJ).date().isoformat() and (now.hour,now.minute)<(16,0):
        raise ValueError('technical_eod_not_ready')
    first=dates[0]
    start=int(datetime.fromisoformat(first).replace(tzinfo=source.BJ).timestamp()*1000)
    end=int(datetime.fromisoformat(last).replace(hour=23,minute=59,second=59,tzinfo=source.BJ).timestamp()*1000)
    root=Path(cache_dir or Path.home()/'.hermes/workspace/stock-dashboard-private-runtime/technical-source-cache')
    if any(p.is_symlink() for p in (root,*root.parents)):raise ValueError('technical_cache_invalid')
    root.mkdir(parents=True,mode=0o700,exist_ok=True)
    info=root.stat()
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('technical_cache_invalid')
    records=[];cache_hits=0
    for code in codes:
        series={}
        for mode in ('forward','none'):
            params={'thscode':source.canonical(code),'interval':'1d','start':start,'end':end,'adjust':mode}
            identity={'provider':'hithink_daily','params':params,'captureDate':now.date().isoformat()}
            name=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()+'.json'
            path=root/name
            if path.exists() or path.is_symlink():
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                with os.fdopen(fd) as f:
                    st=os.fstat(f.fileno())
                    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>5000000:
                        raise ValueError('technical_cache_invalid')
                    saved=json.load(f)
                body=saved.get('body')
                digest=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                if saved.get('identity')!=identity or saved.get('sha256')!=digest:raise ValueError('technical_cache_hash_invalid')
                cache_hits+=1
            else:
                body=request(source.HITHINK+'/api/a-share/prices/historical',params,headers)
                # Cache only validated bars, never provider errors or partial windows.
                sample=parse_history(body,code,mode,first,last,now)
                if [r['date'].isoformat() for r in sample]!=dates:raise ValueError('technical_calendar_mismatch')
                saved={'identity':identity,'body':body,'sha256':hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
                fd,name_tmp=tempfile.mkstemp(prefix='.technical-',dir=root)
                try:
                    with os.fdopen(fd,'w') as f:json.dump(saved,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
                    os.replace(name_tmp,path)
                finally:
                    if os.path.exists(name_tmp):os.unlink(name_tmp)
                sleep(0.5)
            series[mode]=parse_history(body,code,mode,first,last,now)
        records.append(build_record(code,series['forward'],series['none'],dates))
    return records,{'source':'hithink_daily','mxInvoked':False,'cacheHits':cache_hits}

def sync(*, adapter=None, collect_fn=None, publish=False, now=None):
    import part4_official_announcement_sync as part4
    adapter=adapter or part4
    worker,config,token=adapter.load_private_session()
    stocks=adapter.private_watchlist(worker,config,token)
    now=now or datetime.now(source.BJ)
    records,meta=(collect_fn or collect)(stocks,now)
    if len(records)!=len(stocks) or {r['code'] for r in records}!={s.code for s in stocks}:
        raise ValueError('technical_coverage_incomplete')
    as_of=datetime.now(source.BJ).isoformat(timespec='seconds')
    if publish:
        secret=adapter.part4_writer_secret(worker,config)
        written=adapter.private_rpc(worker,config,token,'personal_sync_technical_snapshot',
                    {'p_as_of':as_of,'p_records':records,'p_writer_secret':secret})
        if not isinstance(written,dict) or type(written.get('stored')) is not int or written['stored']!=len(records):
            raise ValueError('technical_write_unconfirmed')
        back=adapter.private_rpc(worker,config,token,'personal_get_part4_v3',{})
        actual={s.get('code'):s for s in back.get('stocks',[])} if isinstance(back,dict) else {}
        if set(actual)!={r['code'] for r in records}:
            raise ValueError('technical_readback_mismatch')
        for r in records:
            if any(actual[r['code']].get(k)!=r[k] for k in ('weeklyBoll','positions')):
                raise ValueError('technical_readback_mismatch')
    return {'status':'ok','published':publish,'readbackVerified':publish,'watchlistCount':len(stocks),
            'recordCount':len(records),'coverageComplete':True,'asOf':as_of,
            'dataAsOf':max(r['asOf'] for r in records),'mxInvoked':False,'aiInvoked':False,
            'source':meta['source'],'private_payload_not_emitted':True}

def build_record(code, adjusted, raw, expected_dates):
    source.canonical(code)
    if not expected_dates or [r['date'].isoformat() for r in raw] != expected_dates:
        raise ValueError('technical_calendar_mismatch')
    latest=date.fromisoformat(expected_dates[-1])
    weekly=compute_boll(adjusted, timeframe='week', as_of=latest, adjustment='forward',
                        include_current_period=True, expected_trade_dates=expected_dates)
    if weekly['status']!='ok' or weekly['asOf']!=expected_dates[-1]:
        raise ValueError('technical_history_insufficient')
    for rows in (raw, adjusted):
        for r in rows:
            for field in ('open','close','low','high'):source.finite_price(r[field])
            if not r['low'] <= min(r['open'],r['close']) <= max(r['open'],r['close']) <= r['high']:
                raise ValueError('technical_ohlc_invalid')
    fields=('asOf','basis','period','multiplier','stddev','sampleCount','upper','middle','lower')
    current=raw[-1]['close']
    week=[r for r in raw if r['date'].isocalendar()[:2]==latest.isocalendar()[:2]]
    month=[r for r in raw if (r['date'].year,r['date'].month)==(latest.year,latest.month)]
    return {'code':code,'asOf':latest.isoformat(),'source':'hithink_daily',
            'weeklyBoll':{k:weekly[k] for k in fields},
            'positions':{'asOf':latest.isoformat(),'day':position_item(current,[raw[-1]]),
                         'week':position_item(current,week),'month':position_item(current,month)}}

if __name__=='__main__':
    import json,re
    try:
        result=sync(publish=parse_args().publish)
        print(json.dumps(result,ensure_ascii=False));raise SystemExit(0)
    except Exception as error:
        category=str(error) if isinstance(error,ValueError) and re.fullmatch(r'(technical|data|hithink)_[a-z_]+',str(error)) else 'technical_sync_failed'
        print(json.dumps({'status':'error','category':category,'published':False,'private_payload_not_emitted':True}));raise SystemExit(2)

