"""Offline read-only provider contracts: no credentials/network/private writes."""
import importlib.util
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

NOW = datetime(2026, 9, 9, 18, 10, tzinfo=ZoneInfo('Asia/Shanghai'))

class SourceTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('dashboard_data_sources'), 'provider router absent')
        import dashboard_data_sources
        return dashboard_data_sources

    def test_hithink_exact_batch_and_provider_timestamp(self):
        m = self.module()
        body = {'code': 0, 'data': {'timestamp': int(NOW.timestamp()*1000), 'item': [
            {'thscode': '600000.SH', 'last_price': 12.3}]}}
        result = m.parse_hithink_quotes(body, ['600000'], NOW, '2026-09-09')
        self.assertEqual(result['quotes'], [{'code': '600000', 'price': 12.3}])
        self.assertEqual(result['source'], 'hithink_snapshot')
        self.assertEqual(result['quoteAsOf'], NOW.isoformat(timespec='seconds'))

    def test_business_limit_retries_readonly_then_falls_back_full_batch(self):
        m = self.module()
        self.assertTrue(callable(getattr(m, 'request_json', None)), 'bounded transport absent')
        from types import SimpleNamespace
        calls, waits = [], []
        replies = [SimpleNamespace(status_code=200, headers={}, json=lambda: {'code': 4001}),
                   SimpleNamespace(status_code=200, headers={}, json=lambda: {'code': 0, 'data': {}})]
        def get(*args, **kwargs):
            calls.append((args, kwargs)); return replies.pop(0)
        result = m.request_json('https://fuyao.aicubes.cn/api/a-share/prices/snapshot', {}, {},
                                get=get, sleep=waits.append, monotonic=lambda: 0)
        self.assertEqual(result['code'], 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(waits, [2])
        self.assertFalse(calls[0][1]['allow_redirects'])

    def test_primary_failure_uses_timestamped_public_source_not_mx(self):
        m = self.module()
        self.assertTrue(callable(getattr(m, 'fetch_quotes', None)), 'fallback router absent')
        calls = []
        def request(url, params, headers):
            calls.append(url)
            if 'fuyao' in url: raise m.DataSourceError('data_retry_exhausted')
            self.assertNotIn('X-api-key', headers)
            return {'rc': 0, 'data': {'diff': [{'f12': '600000', 'f2': 12.3, 'f124': int(NOW.timestamp())}]}}
        result = m.fetch_quotes(['600000'], NOW, expected_day='2026-09-09',
                                request=request, key_reader=lambda: 'synthetic')
        self.assertEqual(result['source'], 'eastmoney_public_snapshot')
        self.assertEqual(result['fallbackReasons'], ['data_retry_exhausted'])
        self.assertEqual(len(calls), 2)
        self.assertFalse(result['mxInvoked'])

    def test_failed_envelope_and_bad_data_never_become_valid_quotes(self):
        m = self.module()
        import copy
        valid = {'code': 0, 'data': {'timestamp': int(NOW.timestamp()*1000),
                                    'item': [{'thscode': '600000.SH', 'last_price': 12.3}]}}
        for code in (False, '0', 2001, 4001):
            body = copy.deepcopy(valid);body['code'] = code
            with self.subTest(code=code), self.assertRaises(m.DataSourceError):
                m.parse_hithink_quotes(body, ['600000'], NOW, '2026-09-09')
        for price in (None, 0, -1, True, float('nan'), float('inf')):
            body = copy.deepcopy(valid);body['data']['item'][0]['last_price'] = price
            with self.subTest(price=price), self.assertRaises(m.DataSourceError):
                m.parse_hithink_quotes(body, ['600000'], NOW, '2026-09-09')
        for items in ([], valid['data']['item'] * 2, [{'thscode': '600001.SH', 'last_price': 2}]):
            body = copy.deepcopy(valid);body['data']['item'] = items
            with self.subTest(items=items), self.assertRaises(m.DataSourceError):
                m.parse_hithink_quotes(body, ['600000'], NOW, '2026-09-09')
        for delta in (-86400000, 3600000):
            body = copy.deepcopy(valid);body['data']['timestamp'] += delta
            with self.assertRaises(m.DataSourceError):
                m.parse_hithink_quotes(body, ['600000'], NOW, '2026-09-09')

    def test_retry_budget_and_auth_failure_do_not_loop(self):
        m = self.module()
        from types import SimpleNamespace
        for status, body, headers, expected in [
            (429, {}, {'Retry-After': '60'}, 'data_retry_budget_exhausted'),
            (200, {'code': 2001}, {}, 'hithink_business_failure'),
            (200, {'code': False}, {}, 'hithink_business_failure')]:
            calls=[]
            def get(*args, **kwargs):
                calls.append(1); return SimpleNamespace(status_code=status, headers=headers, json=lambda: body)
            with self.assertRaisesRegex(m.DataSourceError, expected):
                m.request_json(m.HITHINK+'/api/a-share/prices/snapshot', {}, {}, get=get,
                               sleep=lambda _: self.fail('must not sleep'), monotonic=lambda: 0)
            self.assertEqual(len(calls), 1)

    def test_exchange_calendar_uses_validated_dates_not_weekdays(self):
        m=self.module()
        self.assertTrue(callable(getattr(m, 'latest_trading_day', None)), 'exchange calendar absent')
        from datetime import timedelta
        rows=[{'date': d.strftime('%Y%m%d'), 'date_ms': int(d.replace(hour=0, minute=0).timestamp()*1000)}
              for d in [NOW-timedelta(days=2), NOW-timedelta(days=1)]]
        def request(*args): return {'code': 0, 'data': {'timestamp': int(NOW.timestamp()*1000), 'item': rows}}
        self.assertEqual(m.latest_trading_day(NOW, request=request, key_reader=lambda: 'synthetic'), '2026-09-08')
        rows[-1]['date']='20260910'
        with self.assertRaises(m.DataSourceError):
            m.latest_trading_day(NOW, request=request, key_reader=lambda: 'synthetic')

    def test_provider_ready_time_can_follow_request_start_boundedly(self):
        m=self.module()
        self.assertEqual(m.provider_time(int(NOW.timestamp()*1000)+2000, NOW, '2026-09-09'),
                         '2026-09-09T18:10:02+08:00')
        with self.assertRaises(m.DataSourceError):
            m.provider_time(int(NOW.timestamp()*1000)+301000, NOW, '2026-09-09')

    def test_legacy_dashboard_mx_entry_rejects_nested_quota_even_with_dtos(self):
        import update_market as legacy
        from unittest.mock import patch
        from types import SimpleNamespace
        body={'status':0,'data':{'data':{'message':'您本周金融数据量已达到上限',
                                       'result':{'dataTableDTOList':[{'code':'600000'}]}}}}
        response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:body)
        with patch.dict(legacy.os.environ,{'MX_APIKEY':'synthetic'}),patch.object(legacy.requests,'post',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'mx_provider_quota_exhausted'):
                legacy.api_query('合成',2025)

if __name__ == '__main__':
    unittest.main()
