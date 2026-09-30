"""Part0 saved-account projection. Standard library only; never runs a worker."""
from __future__ import annotations
import json
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path

SYMBOL = re.compile(r'^[0-9]{6}\.(SH|SZ)$')


def number(value, *, nullable=False, minimum=None):
    if value is None and nullable:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('part0_number_invalid')
    if minimum is not None and value < minimum:
        raise ValueError('part0_number_invalid')
    return value


def quantity(value):
    if type(value) is not int or not 0 <= value <= 1000000000:
        raise ValueError('part0_quantity_invalid')
    return value


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('part0_time_invalid')
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('part0_time_invalid')
    return value


def symbol(value):
    if not isinstance(value, str) or not SYMBOL.fullmatch(value):
        raise ValueError('part0_symbol_invalid')
    return value


def project_account(raw):
    rows = []
    seen = set()
    for p in raw['positions']:
        code = symbol(p['symbol'])
        if code in seen:
            raise ValueError('part0_duplicate_position')
        seen.add(code)
        q, available = quantity(p['quantity']), quantity(p['available_quantity'])
        if available > q:
            raise ValueError('part0_available_invalid')
        if not q:
            continue
        cost = number(p.get('cost_price'), nullable=True, minimum=0)
        value = number(p.get('market_value'), nullable=True, minimum=0)
        price = None if value is None else value / q
        pnl = None if cost is None or value is None else value - q * cost
        pct = None if pnl is None or not cost else pnl / (q * cost) * 100
        rows.append(dict(symbol=code, quantity=q, availableQuantity=available,
                         averageCost=cost, marketValue=value, price=price,
                         pnl=None if pnl is None else round(pnl, 6),
                         pnlPct=None if pct is None else round(pct, 6)))
    if len(rows) > 50:
        raise ValueError('part0_position_limit')
    rows.sort(key=lambda p: (p['marketValue'] is None, -(p['marketValue'] or 0), p['symbol']))
    return dict(asOf=timestamp(raw['observed_at_cn']), source='vps_saved_account',
                totalAssets=number(raw['total_assets'], minimum=0),
                availableCash=number(raw['available_cash'], minimum=0),
                positionValue=number(raw['total_position_value'], minimum=0),
                totalProfit=number(raw.get('total_profit'), nullable=True), positions=rows)


def read_ledger(path):
    # mode=ro refuses missing files; query_only denies DML even after refactors.
    from contextlib import closing
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as c:
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        row = c.execute('SELECT observed_at_cn,account_json,source FROM account_snapshot ORDER BY observed_at_cn DESC LIMIT 1').fetchone()
        account = None
        if row is not None:
            raw = json.loads(row['account_json'])['account']
            if raw['observed_at_cn'] != row['observed_at_cn'] or not row['source'].startswith('provider_success'):
                raise ValueError('part0_account_source_invalid')
            account = project_account(raw)
        trades = []
        for r in c.execute('SELECT symbol,decision_json,status,broker_trade_quantity,broker_trade_price,reconciled_trade_quantity,trade_date,updated_at_cn FROM intent WHERE broker_trade_quantity>0 ORDER BY trade_date DESC,updated_at_cn DESC,symbol LIMIT 100'):
            side = json.loads(r['decision_json']).get('side')
            if side not in ('buy', 'sell'):
                raise ValueError('part0_trade_side_invalid')
            q = quantity(r['broker_trade_quantity'])
            price = number(r['broker_trade_price'], minimum=0)
            if price <= 0:
                raise ValueError('part0_fill_price_missing')
            day = r['trade_date']
            if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                raise ValueError('part0_trade_date_invalid')
            state = 'reconciled' if r['status'] == 'completed' and r['reconciled_trade_quantity'] == q else 'fill_observed'
            trades.append(dict(symbol=symbol(r['symbol']), side=side, quantity=q, price=price,
                               amount=round(q * price, 6), tradeDate=day,
                               confirmedAt=timestamp(r['updated_at_cn']), state=state))
        meta = {k:json.loads(v) for k,v in c.execute('SELECT key,value_json FROM meta')}
        active = meta.get('active_symbols', [])
        if not isinstance(active, list) or len(active) > 50 or len(set(active)) != len(active):
            raise ValueError('part0_active_symbols_invalid')
        return dict(account=account, trades=trades, activeSymbols=[symbol(s) for s in active],
                    strategyCycleAt=meta.get('last_strategy_cycle_at_cn'),
                    quoteAsOf=meta.get('last_quote_snapshot_at_cn'))


def build_runtime(*, now, expires, units, daily, journal, ledger):
    from zoneinfo import ZoneInfo
    current = datetime.fromisoformat(timestamp(now))
    expiry = datetime.fromisoformat(timestamp(expires))
    timer = units.get('trading', {})
    service = units.get('strategy', {})
    service_stopped = service.get('LoadState') == 'loaded' and service.get('ActiveState') in ('inactive','failed') and service.get('MainPID') == '0'
    stopped = service_stopped and timer.get('LoadState') == 'loaded' and timer.get('UnitFileState') == 'disabled' and timer.get('ActiveState') == 'inactive' and not timer.get('NextElapseUSecRealtime')
    authorization = 'expired' if current >= expiry and stopped else 'paused' if stopped else 'requires_review'
    events = []
    for entry in journal:
        try:
            result = json.loads(entry.get('MESSAGE', ''))
            if not isinstance(result, dict) or type(result.get('ok')) is not bool or result.get('outside_window'):
                continue
            at = datetime.fromtimestamp(int(entry['__REALTIME_TIMESTAMP']) / 1000000, ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
            if datetime.fromisoformat(at) > current:
                continue
            status = 'ok' if result['ok'] else 'error'
            events.append(dict(at=at, kind='strategy', status=status, code='cycle_succeeded' if result['ok'] else 'cycle_failed'))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    events.sort(key=lambda e: e['at'], reverse=True)
    latest = events[0] if events else None
    running = units.get('strategy', {}).get('ActiveState') in ('active', 'activating')
    strategy = dict(status='running' if running else latest['status'] if latest else 'unknown',
                    asOf=now if running else latest['at'] if latest else None)
    d_running = units.get('dashboard', {}).get('ActiveState') in ('active', 'activating')
    status = daily.get('lastStatus')
    d_status = 'running' if d_running else 'ok' if status == 'ok' and units.get('dashboard', {}).get('Result') == 'success' else 'error' if status == 'error' or units.get('dashboard', {}).get('Result') in ('exit-code', 'timeout', 'signal') else 'unknown'
    d_time = daily.get('startedAt') if d_running else daily.get('finishedAt')
    if d_time:
        timestamp(d_time)
        events.insert(0, dict(at=d_time, kind='dashboard', status=d_status, code='daily_refresh'))
    # Keep errors independently from the short recent-success list.
    selected = sorted(events, key=lambda e:e['at'], reverse=True)[:20]
    for e in events:
        if e['status'] == 'error' and e not in selected and len(selected) < 40:
            selected.append(e)
    return dict(authorization=dict(status=authorization, expiresAt=expires, nextRunAt=None),
                strategy=strategy, dashboard=dict(status=d_status, asOf=d_time, targetDate=daily.get('attemptedDate')),
                activeSymbols=ledger['activeSymbols'], strategyCycleAt=ledger['strategyCycleAt'], quoteAsOf=ledger['quoteAsOf']), selected


def collect(config, *, now=None, command=None):
    import subprocess
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    if set(config) != {'db_path', 'daily_state', 'authority_expires_at'}:
        raise ValueError('part0_config_invalid')
    def run(args):
        return subprocess.run(args, capture_output=True, text=True, check=True, timeout=15).stdout
    command = command or run
    ledger = read_ledger(config['db_path'])
    daily = json.loads(Path(config['daily_state']).read_text())
    names = {'trading':'stock-sim-trial-20260929-30.timer',
             'strategy':'stock-sim-trial-20260929-30.service',
             'dashboard':'stock-dashboard-daily.service'}
    properties = ['LoadState', 'ActiveState', 'MainPID', 'UnitFileState', 'Result', 'NextElapseUSecRealtime']
    units = {}
    for key, name in names.items():
        args = ['systemctl','show',name]
        for p in properties:
            args.extend(['-p',p])
        units[key] = dict(line.split('=',1) for line in command(args).splitlines() if '=' in line)
    lines = command(['journalctl','-u',names['strategy'],'-n','1000','-o','json','--no-pager'])
    journal = [json.loads(line) for line in lines.splitlines() if line.strip()]
    runtime, events = build_runtime(now=now, expires=config['authority_expires_at'],
                                   units=units, daily=daily, journal=journal, ledger=ledger)
    return dict(schemaVersion=1, observedAt=now, account=ledger['account'],
                trades=ledger['trades'], runtime=runtime, events=events)


def write_snapshot(path, payload):
    import os
    import tempfile
    path = Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('part0_output_invalid')
    fd, tmp = tempfile.mkstemp(prefix='.part0-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),0o640)
            if os.getuid() == 0:
                os.fchown(f.fileno(),0,path.parent.stat().st_gid)
            json.dump(payload,f,ensure_ascii=False,allow_nan=False,sort_keys=True)
            f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    try:
        result = collect(json.loads(Path(args.config).read_text()))
        write_snapshot(args.output,result)
        print(json.dumps({'status':'ok','observedAt':result['observedAt'],'providerCalls':0,'tradingWrites':0}))
    except Exception:
        print(json.dumps({'status':'error','category':'part0_collection_failed','privatePayloadNotEmitted':True}))
        raise SystemExit(2)
