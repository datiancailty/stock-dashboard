"""Release regression gates: completion dates, catch-up and safe status."""
import unittest,json
from datetime import datetime
from unittest.mock import Mock
import dashboard_data_sources as sources
import dashboard_refresh_daily as daily
class CompletionTests(unittest.TestCase):
 def test_historical_ready_time_may_be_cached_when_exact_last_session_is_current(self):
  import personal_technical_snapshot_sync as tech
  now=datetime.fromisoformat('2026-09-10T08:00:00+08:00');day=datetime.fromisoformat('2026-09-09T00:00:00+08:00')
  body={'code':0,'data':{'timestamp':int(day.replace(hour=23).timestamp()*1000),'item':[{'date_ms':int(day.timestamp()*1000),'open_price':10,'close_price':10,'high_price':10,'low_price':10}]}}
  self.assertEqual(len(tech.parse_history(body,'600000','none','2026-09-09','2026-09-09',now)),1)
 def test_auth_refresh_is_serialized_before_keychain_rotation(self):
  import part4_official_announcement_sync as a
  from unittest.mock import patch
  from contextlib import contextmanager
  active=[]
  @contextmanager
  def guard():
   active.append(True)
   try:yield
   finally:active.pop()
  worker=Mock();worker.load_config.return_value={}
  def refresh(config):
   self.assertTrue(active,'refresh must hold the shared session lock');return 'synthetic'
  worker.refresh_session.side_effect=refresh
  with patch.object(a,'load_private_worker_module',return_value=worker),patch('dashboard_private_session.session_lock',guard):
   self.assertEqual(a.load_private_session()[2],'synthetic')
 def test_completed_calendar_excludes_current_intraday_session(self):
  now=datetime.fromisoformat('2026-09-10T08:00:00+08:00')
  dates=['20260908','20260909','20260910']
  body={'code':0,'data':{'timestamp':int(now.timestamp()*1000),'item':[{'date':d,'date_ms':int(datetime.strptime(d,'%Y%m%d').replace(tzinfo=sources.BJ).timestamp()*1000)} for d in dates]}}
  self.assertEqual(sources.latest_trading_day(now,request=lambda *a:body,key_reader=lambda:'synthetic',completed=True),'2026-09-09')
  self.assertEqual(sources.latest_trading_day(now.replace(hour=18),request=lambda *a:body,key_reader=lambda:'synthetic',completed=True),'2026-09-10')
 def test_quote_date_uses_beijing_auction_boundary_and_verified_sessions(self):
  cases=[
   ('2026-09-10T09:14:59+08:00',['20260909','20260910'],'2026-09-09'),
   ('2026-09-10T09:15:00+08:00',['20260909','20260910'],'2026-09-10'),
   ('2026-09-10T01:15:00+00:00',['20260909','20260910'],'2026-09-10'),
   ('2026-09-09T18:14:59-07:00',['20260909','20260910'],'2026-09-09'),
   ('2026-09-12T10:00:00+08:00',['20260910','20260911'],'2026-09-11'),
   ('2026-10-01T10:00:00+08:00',['20260929','20260930'],'2026-09-30'),
   ('2026-09-10T15:59:59+08:00',['20260909','20260910'],'2026-09-10'),
   ('2026-09-10T16:00:00+08:00',['20260909','20260910'],'2026-09-10')]
  for stamp,dates,expected in cases:
   with self.subTest(now=stamp):
    now=datetime.fromisoformat(stamp)
    body={'code':0,'data':{'timestamp':int(now.timestamp()*1000),'item':[{'date':d,'date_ms':int(datetime.strptime(d,'%Y%m%d').replace(tzinfo=sources.BJ).timestamp()*1000)} for d in dates]}}
    opts={'request':lambda *a:body,'key_reader':lambda:'synthetic'}
    self.assertEqual(sources.quote_trading_day(now,**opts),expected)
    bj=now.astimezone(sources.BJ)
    self.assertEqual(sources.quote_trading_day(bj.replace(tzinfo=None),**opts),expected)
    completed=dates[-2] if dates[-1]==bj.strftime('%Y%m%d') and bj.hour<16 else dates[-1]
    self.assertEqual(sources.latest_trading_day(now,completed=True,**opts),datetime.strptime(completed,'%Y%m%d').date().isoformat())
    quoted=datetime.fromisoformat(expected).replace(hour=15,tzinfo=sources.BJ)
    if quoted>bj:quoted=bj
    fallback={'rc':0,'data':{'diff':[{'f12':'600000','f2':10,'f124':int(quoted.timestamp())}]}}
    self.assertEqual(sources.parse_eastmoney_quotes(fallback,['600000'],now,expected)['quotes'][0]['price'],10)
 def test_quote_ready_time_is_not_claimed_as_last_trade_date(self):
  now=datetime.fromisoformat('2026-09-10T08:00:00+08:00')
  body={'code':0,'data':{'timestamp':int(now.timestamp()*1000),'item':[{'thscode':'600000.SH','last_price':10}]}}
  got=sources.parse_hithink_quotes(body,['600000'],now,'2026-09-09')
  self.assertEqual(got['quoteTimestampKind'],'provider_ready_time')
  self.assertIsNone(got['exchangeQuoteDate'])
 def test_boot_after_multiple_days_catches_up_without_historical_replay(self):
  now=datetime.fromisoformat('2026-09-14T09:00:00+08:00')
  self.assertEqual(daily.target_slot(now),'2026-09-11')
  self.assertIsNone(daily.due_reason(now,{'attemptedDate':'2026-09-09'}))
  self.assertEqual(daily.due_reason(now,{'attemptedDate':'2026-09-11'}),'already_attempted')
 def test_weekend_boot_catches_up_to_friday_only(self):
  now=datetime.fromisoformat('2026-09-13T09:00:00+08:00')
  self.assertEqual(daily.target_slot(now),'2026-09-11')
  self.assertIsNone(daily.due_reason(now,{}))
class HealthTests(unittest.TestCase):
 def test_exhausted_provider_limit_retains_precise_reason(self):
  from types import SimpleNamespace
  get=Mock(return_value=SimpleNamespace(status_code=429,headers={},json=lambda:{}))
  with self.assertRaisesRegex(sources.DataSourceError,'data_rate_limited'):
   sources.request_json(sources.HITHINK+'/api/a-share/prices/snapshot',{}, {},get=get,sleep=lambda _:None,monotonic=lambda:0)
  self.assertEqual(get.call_count,3)
 def test_health_is_exact_read_back_and_does_not_touch_ai(self):
  import dashboard_refresh_health as h
  import copy
  a=Mock();a.load_private_session.return_value=(None,{},'synthetic');a.part4_writer_secret.return_value='synthetic'
  stored={};names=[]
  def rpc(w,c,t,name,body):
   names.append(name)
   if name=='personal_sync_refresh_health':stored.update(copy.deepcopy(body['p_health']));return {'status':stored['status']}
   return copy.deepcopy(stored)
  a.private_rpc.side_effect=rpc
  reporter=h.HealthReporter(adapter=a,release='synthetic-release');reporter.start()
  for stage in h.STAGES:reporter.stage(stage,{'status':'ok','published':True})
  self.assertTrue(reporter.finish(True));self.assertEqual(stored['status'],'ok')
  self.assertEqual(set(names),{'personal_sync_refresh_health','personal_get_refresh_health'})
 def test_errors_are_categories_not_exception_text(self):
  import dashboard_refresh_health as h
  self.assertEqual(h.error_category('data_rate_limited'),'rate_limited')
  self.assertEqual(h.error_category('stage_timeout'),'timeout')
  self.assertEqual(h.error_category('technical_history_empty'),'empty_response')
  self.assertEqual(h.error_category('https://user:SECRET@example.test/?key=secret'),'unknown_error')
 def test_safe_health_only_accepts_known_fields(self):
  import dashboard_refresh_health as h
  got=h.stage_health({'status':'error','category':'stage_timeout','published':False,'stderr':'SECRET','source':'https://secret'})
  self.assertEqual(set(got),{'status','category','published','source'})
  self.assertEqual(got['category'],'timeout');self.assertIsNone(got['source']);self.assertNotIn('SECRET',json.dumps(got))
if __name__=='__main__':unittest.main()
