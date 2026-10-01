"""Differential tests against frozen reviewed functions, never legacy mains.

The JSON fixture contains only source definitions from commit 7e378a41.
AST/exec is deliberately test-only; production uses ordinary domain imports.
"""
import copy
import hashlib
import html
import importlib
import importlib.util
import json
import re
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

BJ = ZoneInfo('Asia/Shanghai')
FROZEN = json.loads((Path(__file__).parent / 'fixtures/shared_runtime_7e378a41.json').read_text())['modules']


def reference(module, **bindings):
    scope = dict(hashlib=hashlib, html=html, re=re, date=date, datetime=datetime,
                 timedelta=timedelta, BJ=BJ)
    exec('\n\n'.join(FROZEN[module]['functions'].values()), scope)
    scope.update(bindings)
    return SimpleNamespace(**scope)


def outcome(fn, *args):
    try:
        return 'value', json.dumps(fn(*copy.deepcopy(args)), ensure_ascii=False, sort_keys=True)
    except Exception as error:
        return 'error', type(error).__name__, str(error)


class SharedDomainTests(unittest.TestCase):
    def test_position_consumer_uses_pure_module_with_exact_old_results(self):
        import dashboard_technical_indicators as domain
        import personal_technical_snapshot_sync as consumer
        self.assertTrue(hasattr(domain, 'position_item'), 'range position is still borrowed from legacy main')
        self.assertIs(consumer.position_item, domain.position_item)
        old = reference('update_market')
        fixtures = [(None, []), (0, None), (None, [{'low': 1, 'high': 3}]),
                    (1, [{}]), (1, [{'low': None, 'high': 3}]),
                    (3, [{'low': 2, 'high': 2}])]
        fixtures += [(value, [{'low': 0, 'high': 3}]) for value in (-1, 0, 0.999, 1, 1.001, 2, 3, 4)]
        for current, rows in fixtures:
            with self.subTest(current=current, rows=rows):
                self.assertEqual(outcome(domain.position_item, current, rows),
                                 outcome(old.position_item, current, rows))

    def test_news_normalization_is_shared_without_legacy_writer(self):
        self.assertIsNotNone(importlib.util.find_spec('dashboard_news_normalization'), 'missing pure news module')
        domain = importlib.import_module('dashboard_news_normalization')
        import personal_news_sync as consumer
        old = reference('update_news')
        self.assertIs(consumer.news, domain)
        stocks = [{'code': '600000', 'name': '合成甲'}, {'code': '000001', 'name': '合成乙'}]
        cases = [(None, ''), ('', ''), ('每10股派发现金红利2元', '合成甲实施公告'),
                 ('归母净利润2亿元，分红比例50%，总股本1亿股', '合成甲股东回报规划'),
                 ('现金分红总额2亿元；总股本4亿股', '合成甲利润分配预案'),
                 ('营业收入1, 234. 56万元，尚需提交', '合成甲公告')]
        for content, title in cases:
            with self.subTest(title=title):
                self.assertEqual(domain.clean_text(content), old.clean_text(content))
                cleaned = old.clean_text(content)
                self.assertEqual(domain.extract_estimate(cleaned, title), old.extract_estimate(cleaned, title))
        raw = [{'code': '600000', 'title': '<b>合成甲公告</b>', 'content': None,
                'date': '2026-09-01', 'jumpUrl': None}]
        raw += [dict(raw[0], content='每10股派2元'), {'code': '000001', 'title': '合成乙公告', 'date': '2026-09-01'},
                {'title': '不在自选范围', 'date': '2026-09-01'}]
        now = datetime(2026, 9, 4, 18, tzinfo=BJ)
        for item in raw:
            self.assertEqual(domain.identify_stock(item, stocks), old.identify_stock(item, stocks))
            self.assertEqual(domain.item_id(stocks[0], item), old.item_id(stocks[0], item))
        current = consumer.map_items(raw, stocks, [], now)
        with patch.object(consumer, 'news', old):
            self.assertEqual(current, consumer.map_items(raw, stocks, [], now))
        self.assertEqual(len(current), 2)
        self.assertIsNone(current[0]['estimatedDividendPerShare'])
        self.assertEqual(consumer.map_items(raw, stocks, current, now), [])

    def test_recommendation_outcomes_keep_30_session_and_missing_semantics(self):
        self.assertIsNotNone(importlib.util.find_spec('dashboard_recommendation_outcomes'), 'missing outcome domain')
        domain = importlib.import_module('dashboard_recommendation_outcomes')
        import personal_recommendation_eval_sync as consumer
        self.assertIs(consumer.evaluate_recommendations, domain.evaluate_recommendations)
        self.assertIs(consumer.recommendation_stats, domain.recommendation_stats)
        start = date(2026, 7, 1)
        days = [start + timedelta(days=i) for i in range(1, 60) if (start + timedelta(days=i)).weekday() < 5]
        now = datetime.combine(days[35], datetime.min.time(), tzinfo=BJ)
        clock = SimpleNamespace(now=lambda tz=None: now)
        base = dict(id='original-id', recommendationId='original-rec', recommendedAt=start.isoformat(),
                    code='600000', action='分批买入', snapshot={'price': 100}, evaluation=None)
        for size, hit_at in [(0, None), (1, None), (29, None), (30, None), (31, 31), (30, 30), (31, 1)]:
            rows = [{'date': day, 'close': 102, 'low': 99, 'high': 106 if i == hit_at else 104}
                    for i, day in enumerate(days[:size], 1)]
            cases = [base, {**base, 'snapshot': None}, {**base, 'snapshot': {}},
                     {**base, 'recommendedAt': 'invalid'}, {**base, 'action': '当前不买'},
                     {**base, 'action': '当前不买', 'evaluation': {'status': 'not_scored', 'extra': None}}]
            old = reference('process_trade_records', datetime=clock, kline_rows=lambda *args: rows)
            for record in cases:
                a, b = {'records': [copy.deepcopy(record)]}, {'records': [copy.deepcopy(record)]}
                with self.subTest(size=size, hit=hit_at, action=record['action']):
                    expected = old.evaluate_recommendations(a)
                    actual = domain.evaluate_recommendations(b, fetch_rows=lambda *args: rows, now=lambda: now)
                    self.assertEqual(actual, expected)
                    self.assertEqual(json.dumps(b, sort_keys=True), json.dumps(a, sort_keys=True))
                    self.assertEqual(domain.recommendation_stats(b['records']), old.recommendation_stats(a['records']))
                    self.assertEqual(domain.evaluate_recommendations(b, fetch_rows=lambda *args: rows, now=lambda: now),
                                     old.evaluate_recommendations(a))

    def test_history_fetcher_uses_normal_module_with_exact_transport_contract(self):
        self.assertIsNotNone(importlib.util.find_spec('dashboard_recommendation_history'), 'missing history adapter')
        domain = importlib.import_module('dashboard_recommendation_history')
        import personal_recommendation_eval_sync as consumer
        self.assertIs(consumer.legacy_fetcher, domain.history_fetcher)

        class OfflineHTTP:
            class RequestException(Exception):
                pass
            def __init__(self, bodies):
                self.bodies = list(bodies)
                self.calls = []
            def get(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                body = self.bodies.pop(0)
                if body == 'error':
                    raise self.RequestException('synthetic transport failure')
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: copy.deepcopy(body))

        start, end = date(2026, 9, 1), date(2026, 9, 4)
        for code in ('600000', '000001', '110001'):
            market = 'sh' if code.startswith(('5', '6', '9', '11')) else 'sz'
            primary = {'data': {'klines': ['2026-09-01,100,101,102,99,500', '2026-09-02,100,,0,0,500']}}
            fallback = {'data': {market + code: {'day': [['2026-09-01', '100', '101', '102', '99', '500']]}}}
            for bodies in ([primary], [{'data': None}, fallback], ['error', 'error'],
                           [{'data': {'klines': ['bad-date,1,2,3,4']}}, fallback]):
                before, after = OfflineHTTP(bodies), OfflineHTTP(bodies)
                old = reference('process_trade_records', requests=before, KLINE_CACHE={},
                                HEADERS={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
                fetch = domain.history_fetcher(after)
                self.assertEqual(after.calls, [], 'factory must not fetch')
                with self.subTest(code=code, responses=len(bodies)):
                    expected = old.kline_rows(code, start, end)
                    self.assertEqual(fetch(code, start, end), expected)
                    self.assertEqual(fetch(code, start, end), old.kline_rows(code, start, end))
                    self.assertEqual(after.calls, before.calls)

    def test_refresh_matches_frozen_envelope_for_settled_and_active_history(self):
        import math
        import personal_recommendation_eval_sync as consumer
        from test_personal_recommendation_eval_sync import fixture
        def namespace(cutoff, fetch_rows):
            now = datetime.combine(cutoff, datetime.min.time(), tzinfo=BJ)
            clock = SimpleNamespace(now=lambda tz=None: now)
            return vars(reference('process_trade_records', datetime=clock, kline_rows=fetch_rows))
        old = reference('personal_recommendation_eval_sync', copy=copy, math=math,
                        EvalError=consumer.EvalError, SOURCE=consumer.SOURCE, METHOD=consumer.METHOD,
                        legacy_namespace=namespace)
        original, rows, calendar = fixture()
        settled = copy.deepcopy(original)
        for status in ('success', 'failed'):
            record = copy.deepcopy(original['records'][0])
            record['sourceId'] = 'settled-' + status
            record['payload'].update(date='2026-07-01', recommendedAt='2026-07-01', evaluation={
                'status': status, 'observedTradingDays': 30, 'evaluatedAt': '2026-08-31T18:00:00+08:00',
                'firstHitAt': '2026-07-02' if status == 'success' else None,
                'tradingDaysToHit': 1 if status == 'success' else None})
            settled['records'].append(record)
        mixed = copy.deepcopy(settled)
        settled['records'].pop(0)
        nonbuy = copy.deepcopy(original)
        nonbuy['records'][0]['payload'].update(action='当前不买', evaluation=None)
        for context, cal in [(original, calendar), (mixed, calendar), (settled, None), (nonbuy, None)]:
            captured = copy.deepcopy(context)
            def fetch(*args):
                if context in (settled, nonbuy):
                    self.fail('settled and non-buy history must not fetch')
                return rows
            kwargs = dict(as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
            with self.subTest(records=len(context['records']), calendar=cal is not None):
                self.assertEqual(consumer.build_refresh(context, fetch, cal, **kwargs),
                                 old.build_refresh(context, fetch, cal, **kwargs))
                self.assertEqual(context, captured)


if __name__ == '__main__':
    unittest.main()
