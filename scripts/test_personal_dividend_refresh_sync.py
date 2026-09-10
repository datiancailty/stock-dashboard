"""Transport-only synthetic tests; no provider/private network or credentials."""
import unittest, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from datetime import datetime
class DividendWriterTests(unittest.TestCase):
 def fixture(self):
  import personal_dividend_refresh_sync as m
  a=Mock();a.load_private_session.return_value=(None,{},'synthetic');a.private_watchlist.return_value=[SimpleNamespace(code='600000',name='合成甲')]
  now=datetime.fromisoformat('2026-09-10T01:00:00+08:00');stamp=now.isoformat(timespec='seconds')
  forward=[{'code':'600000','asOf':stamp,'amount':None,'status':'missing'}]
  actual=[{'code':'600000','asOf':stamp,'amount':1,'status':'ready'}]
  fc=Mock(return_value=([],[],{'coverageComplete':True}));ac=Mock(return_value=([],[],{'asOf':stamp,'confirmedCoverage':{'complete':True},'paymentRows':[]}))
  return m,a,now,forward,actual,fc,ac
 def test_strict_identity_conflict_is_quarantined_only_with_verified_cash(self):
  from test_public_forward_basis import row,report
  m,a,n,f,c,fc,ac=self.fixture();doc=report('公司计划不派发现金红利。证券代码000002。')
  fc.return_value=([row()],[doc],{'coverageComplete':True})
  saved={};names=[]
  def rpc(w,conf,t,name,body):
   names.append(name)
   if name.startswith('personal_sync_'):
    saved['forwardBasis' if name.endswith('forward_basis') else 'confirmedBasis']={k:v for k,v in body['p_records'][0].items() if k!='code'};return {'stored':1}
   return {'stocks':[{'code':'600000',**saved}]}
  a.private_rpc.side_effect=rpc
  with tempfile.TemporaryDirectory() as d,patch.object(m.confirmed_basis,'build_confirmed_records',return_value=c):
   out=m.sync(adapter=a,now=n,publish=True,collect_forward=fc,collect_actual=ac,evidence_root=Path(d).resolve())
  self.assertEqual(out['quarantinedCount'],1);self.assertEqual(out['category'],'fallback_used')
  self.assertTrue(out['published']);self.assertEqual(saved['forwardBasis']['status'],'missing')
  self.assertIsNone(saved['forwardBasis']['amount'])
  self.assertEqual(saved['forwardBasis']['reason'],'public_report_identity_quarantined')
  self.assertTrue(all(x['amount'] is None for x in saved['forwardBasis']['components'].values()))
  self.assertEqual(names,['personal_sync_forward_basis','personal_sync_confirmed_dividends','personal_get_part4_v4'])
  with self.assertRaisesRegex(ValueError,'body_identity_conflict'):
   m.forward_basis.build_records([row()],[doc],[{'code':'600000','name':'合成甲'}],n.isoformat(timespec='seconds'))

 def test_quarantine_never_publishes_without_independent_ready_cash(self):
  from test_public_forward_basis import row,report
  m,a,n,f,c,fc,ac=self.fixture();fc.return_value=([row()],[report('证券代码000002。')],{'coverageComplete':True});c[0].update(status='missing',amount=None)
  with tempfile.TemporaryDirectory() as d,patch.object(m.confirmed_basis,'build_confirmed_records',return_value=c),self.assertRaisesRegex(ValueError,'dividend_quarantine_without_confirmed_cash'):
   m.sync(adapter=a,now=n,publish=True,collect_forward=fc,collect_actual=ac,evidence_root=Path(d).resolve())
  a.part4_writer_secret.assert_not_called();a.private_rpc.assert_not_called()

 def test_quarantine_is_per_symbol_and_never_discards_foreign_input_rows(self):
  from test_public_forward_basis import row,report
  m,a,n,f,c,fc,ac=self.fixture();stocks=[{'code':'600000','name':'合成甲'},{'code':'600001','name':'合成乙'}]
  other={**row(),'SECURITY_CODE':'600001','SECUCODE':'600001.SH','SECURITY_NAME_ABBR':'合成乙'}
  records,quarantined=m.isolated_forward_records([row(),other],[report('证券代码000002。')],stocks,n.isoformat(timespec='seconds'))
  self.assertEqual(quarantined,['600000']);self.assertEqual({x['code'] for x in records},{'600000','600001'})
  kept=next(x for x in records if x['code']=='600001')
  expected=next(x for x in m.forward_basis.build_records([row(),other],[],stocks,n.isoformat(timespec='seconds')) if x['code']=='600001')
  self.assertEqual(kept,expected)
  with self.assertRaisesRegex(ValueError,'forward_basis_public_identity_invalid'):
   m.isolated_forward_records([row(),{**other,'SECURITY_CODE':'600999'}],[],stocks,n.isoformat(timespec='seconds'))

 def test_isolation_preserves_full_batch_source_indexes(self):
  from test_public_forward_basis import row,report
  m,a,n,f,c,fc,ac=self.fixture();stocks=[{'code':'600000','name':'合成甲'},{'code':'600001','name':'合成乙'}]
  rows=[row(),{**row(),'SECURITY_CODE':'600001','SECUCODE':'600001.SH','SECURITY_NAME_ABBR':'合成乙'},
        {**row('2025-06-30','5'),'SECURITY_CODE':'600001','SECUCODE':'600001.SH','SECURITY_NAME_ABBR':'合成乙'}]
  baseline=m.forward_basis.build_records(rows,[],stocks,n.isoformat(timespec='seconds'))
  records,quarantined=m.isolated_forward_records(rows,[],stocks,n.isoformat(timespec='seconds'))
  self.assertEqual(records,baseline);self.assertEqual(quarantined,[])
  records,quarantined=m.isolated_forward_records(rows,[report('证券代码000002。')],stocks,n.isoformat(timespec='seconds'))
  self.assertEqual(quarantined,['600000'])
  self.assertEqual(next(x for x in records if x['code']=='600001'),next(x for x in baseline if x['code']=='600001'))

 def test_non_identity_validation_failure_still_aborts_before_writes(self):
  m,a,n,f,c,fc,ac=self.fixture()
  with tempfile.TemporaryDirectory() as d,patch.object(m.forward_basis,'build_records',side_effect=ValueError('forward_basis_public_notice_future')),patch.object(m.confirmed_basis,'build_confirmed_records',return_value=c),self.assertRaisesRegex(ValueError,'forward_basis_public_notice_future'):
   m.sync(adapter=a,now=n,publish=True,collect_forward=fc,collect_actual=ac,evidence_root=Path(d).resolve())
  a.part4_writer_secret.assert_not_called();a.private_rpc.assert_not_called()

 def test_dryrun_never_loads_writer_or_publishes(self):
  m,a,n,f,c,fc,ac=self.fixture()
  with tempfile.TemporaryDirectory() as d,patch.object(m.forward_basis,'build_records',return_value=f),patch.object(m.confirmed_basis,'build_confirmed_records',return_value=c):
   out=m.sync(adapter=a,now=n,collect_forward=fc,collect_actual=ac,evidence_root=Path(d).resolve())
  self.assertFalse(out['published']);self.assertEqual(out['usableCount'],1);a.part4_writer_secret.assert_not_called();a.private_rpc.assert_not_called()
 def test_both_collections_finish_before_first_write_and_exact_readback(self):
  m,a,n,f,c,fc,ac=self.fixture();saved={};names=[]
  def rpc(w,conf,t,name,body):
   self.assertTrue(fc.called and ac.called);names.append(name)
   if name.startswith('personal_sync_'):
    saved['forwardBasis' if name.endswith('forward_basis') else 'confirmedBasis']={k:v for k,v in body['p_records'][0].items() if k!='code'};return {'stored':1}
   return {'stocks':[{'code':'600000',**saved}]}
  a.private_rpc.side_effect=rpc
  with tempfile.TemporaryDirectory() as d,patch.object(m.forward_basis,'build_records',return_value=f),patch.object(m.confirmed_basis,'build_confirmed_records',return_value=c):
   out=m.sync(adapter=a,now=n,publish=True,collect_forward=fc,collect_actual=ac,evidence_root=Path(d).resolve())
  self.assertTrue(out['published']);self.assertTrue(out['readbackVerified']);self.assertEqual(names,['personal_sync_forward_basis','personal_sync_confirmed_dividends','personal_get_part4_v4'])
 def test_unproven_actual_coverage_does_not_become_missing_cash(self):
  m,a,n,f,c,fc,ac=self.fixture();ac.return_value=([],[],{'asOf':n.isoformat(timespec='seconds'),'confirmedCoverage':{'complete':False}})
  with tempfile.TemporaryDirectory() as d,patch.object(m.forward_basis,'build_records',return_value=f),patch.object(m.confirmed_basis,'build_confirmed_records',return_value=c),self.assertRaisesRegex(ValueError,'dividend_actual_scan_incomplete'):
   m.sync(adapter=a,now=n,publish=True,collect_forward=fc,collect_actual=ac,evidence_root=Path(d).resolve())
  a.part4_writer_secret.assert_not_called();a.private_rpc.assert_not_called()
 def test_incomplete_actual_scan_prevents_either_write(self):
  m,a,n,f,c,fc,ac=self.fixture();ac.side_effect=ValueError('confirmed_public_pagination_incomplete')
  with tempfile.TemporaryDirectory() as d,patch.object(m.forward_basis,'build_records',return_value=f),self.assertRaisesRegex(ValueError,'confirmed_public_pagination_incomplete'):
   m.sync(adapter=a,now=n,publish=True,collect_forward=fc,collect_actual=ac,evidence_root=Path(d).resolve())
  a.part4_writer_secret.assert_not_called();a.private_rpc.assert_not_called()
if __name__=='__main__':unittest.main()
