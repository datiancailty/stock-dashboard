"""Deterministic HiThink technical-snapshot contracts; synthetic bars only."""
import importlib.util
import unittest
from datetime import date,timedelta

class TechnicalSyncTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('personal_technical_snapshot_sync'),'technical writer adapter missing')
        import personal_technical_snapshot_sync
        return personal_technical_snapshot_sync

    def test_forward_weekly_and_raw_range_positions_are_not_confused(self):
        m=self.module()
        days=[date(2026,1,1)+timedelta(days=i) for i in range(251)]
        days=[d for d in days if d.weekday()<5]
        raw=[{'date':d,'open':10+i/100,'close':10+i/100,'low':9+i/100,'high':11+i/100} for i,d in enumerate(days)]
        adj=[dict(r,open=r['open']/2,close=r['close']/2,low=r['low']/2,high=r['high']/2) for r in raw]
        result=m.build_record('600000',adj,raw,[d.isoformat() for d in days])
        self.assertEqual(result['asOf'],days[-1].isoformat())
        self.assertEqual(result['weeklyBoll']['stddev'],'sample')
        self.assertEqual(result['weeklyBoll']['basis'],'前复权周K')
        self.assertGreater(result['positions']['day']['low'],result['weeklyBoll']['middle'])
        self.assertEqual(result['source'],'hithink_daily')
        with self.assertRaises(ValueError):m.build_record('600000',adj[:-1],raw,[d.isoformat() for d in days])

    def test_historical_contract_checks_identity_units_window_and_envelope(self):
        m=self.module()
        self.assertTrue(callable(getattr(m,'parse_history',None)), 'historical parser absent')
        from datetime import datetime
        now=datetime(2026,9,9,19,tzinfo=m.source.BJ)
        stamp=int(now.replace(hour=0).timestamp()*1000)
        body={'code':0,'data':{'timestamp':int(now.timestamp()*1000),'item':[
            {'date_ms':stamp,'open_price':10,'close_price':10,'high_price':11,'low_price':9}]}}
        rows=m.parse_history(body,'600000','forward','2026-09-09','2026-09-09',now)
        self.assertEqual(rows[0]['date'],date(2026,9,9))
        body['data']['item'][0]['thscode']='600001.SH'
        with self.assertRaises(ValueError):m.parse_history(body,'600000','forward','2026-09-09','2026-09-09',now)

    def test_dry_run_collects_all_symbols_without_obtaining_writer(self):
        m=self.module()
        self.assertTrue(callable(getattr(m,'sync',None)), 'technical sync absent')
        from unittest.mock import patch
        from types import SimpleNamespace
        from datetime import datetime
        adapter=SimpleNamespace(load_private_session=lambda:(None,{},'fixture'),
          private_watchlist=lambda *a:[SimpleNamespace(code='600000',name='合成')],
          part4_writer_secret=lambda *a:self.fail('no write secret'),private_rpc=lambda *a:self.fail('no write'))
        def collect(stocks,now):return ([{'code':'600000','asOf':'2026-09-09'}], {'source':'hithink_daily','mxInvoked':False})
        result=m.sync(adapter=adapter,collect_fn=collect,now=datetime(2026,9,9,19,tzinfo=m.source.BJ))
        self.assertFalse(result['published']);self.assertEqual(result['recordCount'],1)
        self.assertTrue(result['coverageComplete'])

    def test_collect_requests_adjusted_and_raw_series_separately(self):
        m=self.module()
        self.assertTrue(callable(getattr(m,'collect',None)), 'real collector missing')
        from datetime import datetime
        from types import SimpleNamespace
        from tempfile import TemporaryDirectory
        now=datetime(2026,9,9,19,tzinfo=m.source.BJ)
        days=[date(2026,1,1)+timedelta(days=i) for i in range(252)]
        days=[d for d in days if d.weekday()<5]
        calls=[]
        def request(url,params,headers):
            calls.append((url,params))
            if 'calendar' in url:
                items=[{'date':d.strftime('%Y%m%d'),'date_ms':int(datetime.combine(d,datetime.min.time(),m.source.BJ).timestamp()*1000)} for d in days]
            else:
                items=[{'date_ms':int(datetime.combine(d,datetime.min.time(),m.source.BJ).timestamp()*1000),
                  'open_price':10,'close_price':10,'high_price':11,'low_price':9} for d in days]
            return {'code':0,'data':{'timestamp':int(now.timestamp()*1000),'item':items}}
        with TemporaryDirectory() as folder:
            records,meta=m.collect([SimpleNamespace(code='600000')],now,request=request,
                                   key_reader=lambda:'synthetic',cache_dir=__import__('pathlib').Path(folder).resolve(),sleep=lambda _:None)
        self.assertEqual(len(records),1)
        self.assertEqual([p['adjust'] for url,p in calls if 'historical' in url],['forward','none'])
        self.assertFalse(meta['mxInvoked'])

    def test_cli_defaults_to_no_publication(self):
        m=self.module()
        self.assertTrue(callable(getattr(m,'parse_args',None)), 'safe CLI absent')
        self.assertFalse(m.parse_args([]).publish)
        self.assertFalse(m.parse_args(['--dry-run']).publish)
        self.assertTrue(m.parse_args(['--publish']).publish)

if __name__=='__main__':unittest.main()
