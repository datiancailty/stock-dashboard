"""Source routing and write/readback guard; fixtures only."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo
import personal_market_snapshot_sync as market

class MarketSourceTests(unittest.TestCase):
    def test_default_dryrun_uses_verified_quotes_without_writer(self):
        self.assertTrue(callable(getattr(market, 'sync', None)), 'safe source integration missing')
        adapter=SimpleNamespace(load_private_session=lambda: (None, {}, 'fixture'),
            private_watchlist=lambda *a: [SimpleNamespace(code='600000', name='合成股票')],
            part4_writer_secret=lambda *a: self.fail('dry run writer'), private_rpc=lambda *a: self.fail('dry run RPC write'))
        now=datetime(2026,9,9,18,10,tzinfo=ZoneInfo('Asia/Shanghai'))
        quotes={'quotes':[{'code':'600000','price':10.0}], 'quoteAsOf':now.isoformat(timespec='seconds'),
                'source':'hithink_snapshot','fallbackReasons':[],'mxInvoked':False}
        with patch('dashboard_data_sources.quote_trading_day',return_value='2026-09-09'), patch('dashboard_data_sources.fetch_quotes',return_value=quotes):
            result=market.sync(adapter=adapter, now=now)
        self.assertFalse(result['published']);self.assertEqual(result['quoteCount'],1)
        self.assertEqual(result['source'],'hithink_snapshot')

    def test_intraday_primary_rate_limit_accepts_current_fallback_quote(self):
        import dashboard_data_sources as source
        now = datetime.fromisoformat('2026-09-10T10:00:00+08:00')
        calendar = {'code': 0, 'data': {'timestamp': int(now.timestamp()*1000), 'item': [
            {'date': d, 'date_ms': int(datetime.strptime(d, '%Y%m%d').replace(tzinfo=source.BJ).timestamp()*1000)}
            for d in ['20260909', '20260910']]}}
        calls = []
        def request(url, params, headers):
            calls.append(url)
            if url.endswith('/calendar/trading-days'): return calendar
            if url.endswith('/prices/snapshot'): raise source.DataSourceError('data_rate_limited')
            return {'rc': 0, 'data': {'diff': [{'f12': '600000', 'f2': 10, 'f124': int(now.timestamp())}]}}
        adapter = SimpleNamespace(load_private_session=lambda: (None, {}, 'synthetic'),
            private_watchlist=lambda *a: [SimpleNamespace(code='600000')],
            private_rpc=lambda *a: self.fail('no real write'),
            part4_writer_secret=lambda *a: self.fail('no writer credential'))
        with patch.object(source, 'request_json', side_effect=request), patch.object(source, 'read_hithink_key', return_value='synthetic'):
            result = market.sync(adapter=adapter, now=now)
        self.assertEqual(result['tradingDate'], '2026-09-10')
        self.assertEqual(result['source'], 'eastmoney_public_snapshot')
        self.assertEqual(result['quoteAsOf'], now.isoformat(timespec='seconds'))
        self.assertEqual(result['fallbackReasons'], ['data_rate_limited'])
        self.assertFalse(result['published'])
        self.assertEqual(len(calls), 3)

    def test_publish_requires_matching_authenticated_readback_and_never_retries_write(self):
        import dashboard_data_sources as source
        now=datetime(2026,9,9,18,10,tzinfo=ZoneInfo('Asia/Shanghai'))
        calls=[]
        def rpc(worker, config, token, name, body):
            calls.append(name)
            if name=='personal_sync_market_snapshot': return {'stored': 1}
            return {'stocks':[{'code':'600000','price':99.0}]}
        adapter=SimpleNamespace(load_private_session=lambda:(None,{},'fixture'),
            private_watchlist=lambda *a:[SimpleNamespace(code='600000',name='合成')],
            part4_writer_secret=lambda *a:'fixture',private_rpc=rpc)
        quotes={'quotes':[{'code':'600000','price':10.0}], 'quoteAsOf':now.isoformat(timespec='seconds'),
                'source':'hithink_snapshot','fallbackReasons':[],'mxInvoked':False}
        with patch.object(source,'quote_trading_day',return_value='2026-09-09'),patch.object(source,'fetch_quotes',return_value=quotes):
            with self.assertRaisesRegex(source.DataSourceError, 'data_publish_readback_mismatch'):
                market.sync(adapter=adapter, now=now, publish=True)
        self.assertEqual(calls,['personal_sync_market_snapshot','personal_get_part4'])

if __name__=='__main__':unittest.main()
