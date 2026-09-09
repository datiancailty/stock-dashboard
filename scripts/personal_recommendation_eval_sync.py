#!/usr/bin/env python3
"""Refresh outcomes of EXISTING recommendations only; AI/profile evolution stays paused.

The AST allowlist loads the original evaluator/statistics, not the legacy module:
no legacy main, environment reads, model, learning, trades or new recommendations.
Only an explicitly invoked CLI adapter may authenticate/read market data/publish.
"""
from __future__ import annotations

import argparse
import ast
import copy
import importlib.util
import json
import sys
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BJ = ZoneInfo('Asia/Shanghai')
SOURCE = 'legacy_eastmoney_tencent_unadjusted_daily'
METHOD = 'legacy_30d_plus5_v1'


def legacy_namespace(cutoff, fetch_rows):
    """Execute ONLY reviewed pure functions with a fixed observation clock."""
    names = {'number', 'position_item', 'recommendation_stats', 'evaluate_recommendations'}
    tree = ast.parse((ROOT / 'scripts/process_trade_records.py').read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != names:
        raise RuntimeError('recommendation_legacy_contract_changed')

    class ObservationClock:
        @staticmethod
        def now(tz=None):
            return datetime.combine(cutoff, datetime.min.time(), tzinfo=BJ)

    namespace = {'date': date, 'datetime': ObservationClock, 'BJ': BJ, 'kline_rows': fetch_rows}
    module = ast.Module(body=[], type_ignores=[])
    module.body.extend(nodes)
    exec(compile(module, '<legacy-outcome-only>', 'exec'), namespace)
    return namespace


class EvalError(RuntimeError):
    """Only fixed internal categories may be emitted to logs."""


def observation_rows(fetch_rows, record, cutoff, calendar):
    if not isinstance(calendar, dict):
        raise EvalError('recommendation_calendar_required')
    try:
        start = date.fromisoformat(str(record.get('recommendedAt') or record.get('date'))[:10])
        week_start = start - timedelta(days=start.weekday())
        dates = [date.fromisoformat(value) for value in calendar['tradingDates']]
        if dates != sorted(set(dates)) or not dates:
            raise ValueError
        if date.fromisoformat(calendar['validFrom']) > week_start or date.fromisoformat(calendar['validThrough']) < cutoff:
            raise ValueError
        if not date.fromisoformat(calendar['validFrom']) <= dates[0] <= dates[-1] <= date.fromisoformat(calendar['validThrough']):
            raise ValueError
        if cutoff not in dates:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise EvalError('recommendation_calendar_invalid') from None
    forward = [day for day in dates if start < day <= cutoff][:30]
    if not forward:
        raise EvalError('recommendation_observations_unavailable')
    required = [day for day in dates if week_start <= day <= forward[-1]]
    try:
        raw = fetch_rows(str(record['code']), start, cutoff)
    except Exception:
        raise EvalError('recommendation_observations_unavailable') from None
    rows = []
    seen = set()
    try:
        for row in raw:
            day = row['date'] if type(row['date']) is date else date.fromisoformat(row['date'])
            if day in seen:
                raise ValueError
            seen.add(day)
            values = [row[key] for key in ('close', 'high', 'low')]
            if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0 for v in values):
                raise ValueError
            close, high, low = values
            if not low <= close <= high:
                raise ValueError
            if week_start <= day <= forward[-1]:
                rows.append({'date': day, 'close': close, 'high': high, 'low': low})
        rows.sort(key=lambda row: row['date'])
        if [row['date'] for row in rows] != required:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise EvalError('recommendation_observations_unavailable') from None
    return rows, forward[-1]


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed


def validate_context(context, as_of, data_as_of):
    try:
        captured = timestamp(as_of)
        cutoff = date.fromisoformat(data_as_of)
        if not 0 <= (captured.astimezone(BJ).date() - cutoff).days <= 10:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise EvalError('recommendation_as_of_invalid') from None
    try:
        if not isinstance(context['meta'], dict) or not isinstance(context['records'], list) or not 1 <= len(context['records']) <= 5000:
            raise ValueError
        seen = set()
        for row in context['records']:
            sid = row['sourceId']
            if not isinstance(sid, str) or not 1 <= len(sid) <= 300 or sid in seen:
                raise ValueError
            seen.add(sid)
            record = row['payload']
            if not isinstance(record, dict) or not isinstance(record.get('action'), str) or not record['action']:
                raise ValueError
            old = record.get('evaluation') or {}
            if not isinstance(old, dict):
                raise ValueError
            if record['action'] == '分批买入':
                if not isinstance(record.get('code'), str) or not re.fullmatch(r'[0-9]{6}', record['code']):
                    raise ValueError
                start = date.fromisoformat(str(record.get('recommendedAt') or record.get('date'))[:10])
                price = (record.get('snapshot') or {}).get('price')
                if start > cutoff or isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or not 0 < price <= 1000000:
                    raise ValueError
            if old.get('evaluatedAt') and timestamp(old['evaluatedAt']) > captured:
                raise EvalError('recommendation_context_stale')
            if old.get('observationAsOf') and date.fromisoformat(old['observationAsOf']) > cutoff:
                raise EvalError('recommendation_context_stale')
        meta = context['meta']
        if meta.get('asOf') and timestamp(meta['asOf']) >= captured:
            raise EvalError('recommendation_context_stale')
        performance = meta.get('performance') or {}
        if performance.get('dataAsOf') and date.fromisoformat(performance['dataAsOf']) > cutoff:
            raise EvalError('recommendation_context_stale')
    except (ValueError, TypeError, KeyError, AttributeError):
        raise EvalError('recommendation_context_invalid') from None
    return cutoff


def build_refresh(context, fetch_rows, calendar, *, as_of, data_as_of):
    cutoff = validate_context(context, as_of, data_as_of)
    previous = copy.deepcopy(context)
    patches, evaluated = [], []
    retained = 0
    for base in previous['records']:
        record = copy.deepcopy(base['payload'])
        old = record.get('evaluation') or {}
        settled = (record.get('action') == '分批买入' and old.get('status') in ('success', 'failed')
                   and old.get('observedTradingDays') == 30)
        if settled:
            retained += 1
        else:
            rows, observed_at = [], None
            if record.get('action') == '分批买入':
                rows, observed_at = observation_rows(fetch_rows, record, cutoff, calendar)
            namespace = legacy_namespace(cutoff, lambda *args: rows)
            namespace['evaluate_recommendations']({'records': [record]})
            evaluation = record.get('evaluation')
            if not evaluation or evaluation.get('status') == 'no_data':
                raise EvalError('recommendation_observations_unavailable')
            if evaluation.get('status') != 'not_scored':
                assert observed_at is not None
                if (old.get('status') in ('success', 'failed') and old['status'] != evaluation['status']
                        or old.get('observedTradingDays', 0) > evaluation['observedTradingDays']
                        or any(old.get(key) and old[key] != evaluation.get(key) for key in
                               ('firstHitAt', 'tradingDaysToHit', 'calendarDaysToHit',
                                'weeklyUpperFirstAt', 'tradingDaysToWeeklyUpper', 'calendarDaysToWeeklyUpper'))):
                    raise EvalError('recommendation_outcome_regression')
                evaluation['evaluatedAt'] = as_of
                evaluation['observationAsOf'] = observed_at.isoformat()
                evaluation['dataSource'] = SOURCE
        patches.append({'sourceId': base['sourceId'], 'previousPayload': base['payload'],
                        'evaluation': record.get('evaluation')})
        evaluated.append(record)
    namespace = legacy_namespace(cutoff, lambda *args: [])
    performance = namespace['recommendation_stats'](evaluated)
    performance.update({'asOf': as_of, 'dataAsOf': data_as_of, 'dataSource': SOURCE, 'method': METHOD,
                        'retainedSettled': retained,
                        'calendarSource': (calendar or {}).get('source', 'provided_exchange_calendar' if calendar else 'retained_only')})
    return {'asOf': as_of, 'dataAsOf': data_as_of, 'records': patches,
            'performance': performance, 'previousMeta': previous['meta']}


SAFE_ERRORS = frozenset({
    'recommendation_calendar_required', 'recommendation_calendar_invalid', 'recommendation_calendar_unavailable',
    'recommendation_observations_unavailable', 'recommendation_as_of_invalid',
    'recommendation_context_invalid', 'recommendation_context_stale',
    'recommendation_outcome_regression', 'recommendation_readback_failed',
    'recommendation_legacy_contract_changed',
})


def proxy_calendar(context, fetch_rows, as_of, requested_cutoff):
    """Use observed SZ399001 index sessions, NOT a guessed weekday calendar.

    This is explicitly a proxy: exchange-specific closures, source gaps and
    suspended individual securities are not silently filled. A missing/truncated
    stock window still blocks the complete batch. Live correctness is unverified
    until the user authorizes the first read-only provider probe.
    """
    captured = timestamp(as_of).astimezone(BJ)
    complete_through = captured.date() - timedelta(days=1 if captured.hour < 16 else 0)
    end = date.fromisoformat(requested_cutoff) if requested_cutoff else complete_through
    if end > complete_through:
        raise EvalError('recommendation_as_of_invalid')
    starts = [date.fromisoformat(str(row['payload'].get('recommendedAt') or row['payload'].get('date'))[:10])
              for row in context['records'] if row['payload'].get('action') == '分批买入'
              and not ((row['payload'].get('evaluation') or {}).get('status') in ('success', 'failed')
                       and (row['payload'].get('evaluation') or {}).get('observedTradingDays') == 30)]
    start = min(starts) if starts else end - timedelta(days=14)
    week_start = start - timedelta(days=start.weekday())
    try:
        raw = fetch_rows('399001', week_start, end)
        days = [row['date'] if type(row['date']) is date else date.fromisoformat(row['date']) for row in raw]
        days = [day for day in days if day <= end]
        if not days or len(days) != len(set(days)):
            raise ValueError
        days.sort()
        if days[0] >= week_start or days[-1] < end - timedelta(days=10):
            raise ValueError
        if requested_cutoff and days[-1] != end:
            raise ValueError
    except Exception:
        raise EvalError('recommendation_calendar_unavailable') from None
    cutoff = days[-1].isoformat()
    return {'validFrom': days[0].isoformat(), 'validThrough': cutoff,
            'tradingDates': [day.isoformat() for day in days],
            'source': 'legacy_sz399001_daily_session_proxy'}, cutoff


def run_refresh(rpc, writer_secret, fetch_rows, calendar=None, *, as_of, data_as_of=None, publish=False):
    """Inject transports for offline tests; perform at most ONE atomic write RPC."""
    attempted = False
    try:
        context = rpc('personal_get_recommendation_eval_context', {})
        if data_as_of is None or calendar is None:
            calendar, data_as_of = proxy_calendar(context, fetch_rows, as_of, data_as_of)
        plan = build_refresh(context, fetch_rows, calendar, as_of=as_of, data_as_of=data_as_of)
        summary = {'recordCount': len(plan['records']), 'asOf': as_of, 'dataAsOf': data_as_of,
                   'retainedSettled': plan['performance']['retainedSettled'],
                   'calendarSource': plan['performance']['calendarSource'],
                   'aiExecuted': False, 'private_payload_not_emitted': True}
        if not publish:
            return {'status': 'dry_run_ok', 'published': False, **summary}
        body = {'p_as_of': as_of, 'p_records': plan['records'], 'p_previous_meta': plan['previousMeta'],
                'p_performance': plan['performance'], 'p_writer_secret': writer_secret()}
        attempted = True
        written = rpc('personal_sync_recommendation_evaluations', body)
        body.pop('p_writer_secret', None)
        expected = {'records': [{'sourceId': patch['sourceId'],
                                'payload': {**patch['previousPayload'], 'evaluation': patch['evaluation']}}
                               for patch in plan['records']],
                    'meta': {**plan['previousMeta'], 'asOf': as_of, 'performance': plan['performance']}}
        actual = rpc('personal_get_recommendation_eval_context', {})
        if (not isinstance(written, dict) or written.get('stored') != len(plan['records'])
                or not isinstance(actual, dict) or actual.get('meta') != expected['meta']
                or sorted(actual.get('records', []), key=lambda r: r['sourceId'])
                != sorted(expected['records'], key=lambda r: r['sourceId'])):
            raise EvalError('recommendation_readback_failed')
        return {'status': 'ok', 'published': True, 'verified': True, **summary}
    except Exception as error:
        category = str(error) if isinstance(error, EvalError) and str(error) in SAFE_ERRORS else 'recommendation_refresh_failed'
        return {'status': 'error', 'category': category,
                'publishState': 'unconfirmed' if attempted else 'not_attempted',
                'private_payload_not_emitted': True, 'aiExecuted': False}


def legacy_fetcher(http=None):
    """Opt-in old read-only retrieval; no call occurs while constructing it.

    Eastmoney raw daily bars, falling back to Tencent raw daily bars. The old
    source does not report WHICH fallback supplied a row. Calendar/coverage gates
    reject truncation, missing sessions (including suspensions), and stale data.
    These remain unadjusted price observations, not total returns or PIT prices.
    """
    if http is None:
        import requests
        http = requests
    tree = ast.parse((ROOT / 'scripts/process_trade_records.py').read_text())
    names = {'number', 'kline_rows'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != names:
        raise EvalError('recommendation_legacy_contract_changed')
    namespace = {'date': date, 'timedelta': timedelta, 'requests': http, 'KLINE_CACHE': {},
                 'HEADERS': {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}}
    module = ast.Module(body=[], type_ignores=[])
    module.body.extend(nodes)
    exec(compile(module, '<legacy-history-only>', 'exec'), namespace)
    return namespace['kline_rows']


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='NON-AI existing recommendation outcomes; default read-only dryrun')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--publish', action='store_true', help='explicitly permit the single guarded private RPC')
    mode.add_argument('--dry-run', action='store_true', help='read/validate only (the default)')
    parser.add_argument('--data-as-of', help='optional completed trading date; default latest observed index date after the 16:00 Shanghai close gate')
    parser.add_argument('--calendar', type=Path, help='optional exchange calendar JSON; default read-only SZ399001 session proxy')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        # No arbitrary provider/plugin path; reuse only the existing private Part4
        # session and Keychain transport. No setup/login/Codex/model routines run.
        calendar = json.loads(args.calendar.read_text()) if args.calendar else None
        spec = importlib.util.spec_from_file_location('recommendation_part4_transport', ROOT / 'scripts/part4_official_announcement_sync.py')
        if spec is None or spec.loader is None:
            raise EvalError('recommendation_refresh_failed')
        adapter = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = adapter
        spec.loader.exec_module(adapter)
        worker, config, token = adapter.load_private_session()
        def rpc(name, body):
            return adapter.private_rpc(worker, config, token, name, body)
        result = run_refresh(rpc, lambda: adapter.part4_writer_secret(worker, config),
                             legacy_fetcher(), calendar,
                             as_of=datetime.now(BJ).isoformat(timespec='seconds'),
                             data_as_of=args.data_as_of, publish=args.publish)
    except Exception:
        result = {'status': 'error', 'category': 'recommendation_refresh_failed',
                  'publishState': 'not_attempted', 'private_payload_not_emitted': True, 'aiExecuted': False}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result['status'] != 'error' else 2


if __name__ == '__main__':
    raise SystemExit(main())
