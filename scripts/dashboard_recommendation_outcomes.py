"""Deterministic historical recommendation outcomes; no AI, files or transport.

Callers supply observations and an explicit clock. The historical mutation and
(changed, statistics) return contract is preserved, including 30-session limits.
Range-position rounding here remains the historical evaluator's contract, not
BOLL and not the VPS strategy's indicator implementation.
"""
from datetime import date


def number(value):
    try:return float(value)
    except (TypeError,ValueError):return 0.0

def position_item(current,rows):
    if not rows:return None
    low=min(x['low'] for x in rows);high=max(x['high'] for x in rows)
    percent=50.0 if high<=low else max(0,min(100,(current-low)/(high-low)*100))
    zone='下部' if percent<100/3 else ('中部' if percent<200/3 else '上部')
    return {'zone':zone,'percent':round(percent,1),'low':round(low,3),'high':round(high,3)}

def recommendation_stats(records):
    buys=[r for r in records if r.get('action')=='分批买入']
    successes=[r for r in buys if (r.get('evaluation') or {}).get('status')=='success']
    failures=[r for r in buys if (r.get('evaluation') or {}).get('status')=='failed']
    pending=[r for r in buys if (r.get('evaluation') or {}).get('status') in ('pending','no_data',None)]
    resolved=len(successes)+len(failures)
    hit_days=[number((r.get('evaluation') or {}).get('tradingDaysToHit')) for r in successes]
    calendar_days=[number((r.get('evaluation') or {}).get('calendarDaysToHit')) for r in successes]
    weekly_hits=[r for r in buys if (r.get('evaluation') or {}).get('weeklyUpperFirstAt')]
    weekly_days=[number((r.get('evaluation') or {}).get('tradingDaysToWeeklyUpper')) for r in weekly_hits]
    return {'criterion':'分批买入后30个交易日内，盘中最高价达到指令价+5%','targetGainPct':5,'windowTradingDays':30,'totalCommands':len(records),'buyCommands':len(buys),'resolved':resolved,'successes':len(successes),'failures':len(failures),'pending':len(pending),'successRate':round(len(successes)/resolved*100,1) if resolved else None,'avgTradingDaysToHit':round(sum(hit_days)/len(hit_days),1) if hit_days else None,'avgCalendarDaysToHit':round(sum(calendar_days)/len(calendar_days),1) if calendar_days else None,'weeklyUpperHits':len(weekly_hits),'weeklyUpperRate':round(len(weekly_hits)/len(buys)*100,1) if buys else None,'avgTradingDaysToWeeklyUpper':round(sum(weekly_days)/len(weekly_days),1) if weekly_days else None}

def evaluate_recommendations(payload, *, fetch_rows, now):
    today=now().date();changed=False
    for record in payload.get('records') or []:
        if record.get('action')!='分批买入':
            evaluation=record.get('evaluation') or {}
            if evaluation.get('status')!='not_scored':
                record['evaluation']={'status':'not_scored','reason':'非明确买入指令，不计入买入命中率'};changed=True
            continue
        try:start=date.fromisoformat(str(record.get('recommendedAt') or record.get('date'))[:10])
        except ValueError:continue
        entry=number((record.get('snapshot') or {}).get('price'))
        if not entry:continue
        all_rows=fetch_rows(str(record.get('code') or ''),start,today)
        rows=[x for x in all_rows if start<x['date']<=today][:30]
        target=entry*1.05;hit=next((x for x in rows if x['high']>=target),None);weekly_hit=None
        for index,bar in enumerate(rows):
            iso=bar['date'].isocalendar();week=[x for x in all_rows if x['date']<=bar['date'] and x['date'].isocalendar()[:2]==iso[:2]]
            item=position_item(bar['close'],week)
            if item and item['zone']=='上部' and bar['close']>entry:weekly_hit=bar;break
        max_high=max((x['high'] for x in rows),default=entry);latest_close=rows[-1]['close'] if rows else entry
        evaluation={'status':'success' if hit else ('failed' if len(rows)>=30 else ('pending' if rows else 'no_data')),'criterion':'30个交易日内最高价达到指令价+5%','entryPrice':round(entry,3),'targetPrice':round(target,3),'observedTradingDays':len(rows),'maxGainPct':round((max_high/entry-1)*100,3),'latestReturnPct':round((latest_close/entry-1)*100,3)}
        if hit:evaluation.update({'firstHitAt':hit['date'].isoformat(),'tradingDaysToHit':rows.index(hit)+1,'calendarDaysToHit':(hit['date']-start).days})
        if weekly_hit:evaluation.update({'weeklyUpperFirstAt':weekly_hit['date'].isoformat(),'tradingDaysToWeeklyUpper':rows.index(weekly_hit)+1,'calendarDaysToWeeklyUpper':(weekly_hit['date']-start).days})
        previous=record.get('evaluation') or {};previous_core={k:v for k,v in previous.items() if k!='evaluatedAt'}
        if previous_core!=evaluation:
            evaluation['evaluatedAt']=now().isoformat(timespec='seconds');record['evaluation']=evaluation;changed=True
    return changed,recommendation_stats(payload.get('records') or [])
