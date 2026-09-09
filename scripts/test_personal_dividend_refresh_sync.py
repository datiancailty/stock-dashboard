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
