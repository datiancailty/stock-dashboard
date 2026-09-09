"""Synthetic-only runtime boundary tests for public forward sync."""
import unittest
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import Mock, patch, mock_open
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import copy, sys
from test_public_forward_basis import row, report, ASOF, STOCKS

def index_note(doc=None):
 doc=doc or report('公司计划不派发现金红利。')
 return {'art_code':doc['id'],'title':doc['title'],'notice_date':doc['date'],
         'codes':[{'stock_code':'600000','short_name':'合成甲'}]}

def index_page(notes=None, **changes):
 notes=[index_note()] if notes is None else notes
 return {'list':notes,'page_size':100,'total_hits':len(notes),'page_index':1,**changes}

def content_page(doc=None, **changes):
 doc=doc or report('公司计划不派发现金红利。')
 return {'success':1,'data':{'art_code':doc['id'],'notice_title':doc['title'],
         'notice_date':doc['date'],'security':[{'stock':'600000'}],
         'notice_content':doc['pages'][0],'page_size':1,'page_index':1,**changes}}

# Exercise collect itself but replace every network and evidence-file boundary.
# No real adapter import, private-runtime stat/mkdir, file write or credential read.
def offline_collect(mod, indexes=None, contents=None, table=None, now=ASOF):
 indexes=copy.deepcopy(indexes or [index_page()]);contents=copy.deepcopy(contents or [content_page()])
 table=table or [row()]
 def response(url):
  if 'datacenter-web.' in url:return {'success':True,'result':{'data':table,'pages':1,'count':len(table)}}
  params=parse_qs(urlparse(url).query);page=int(params['page_index'][0])
  return contents[params['art_code'][0]][page-1] if isinstance(contents,dict) else contents[page-1]
 adapter=SimpleNamespace(safe_curl_json=Mock(side_effect=response),fetch_notice_page=Mock(side_effect=indexes))
 root=Mock();root.is_symlink.return_value=False
 with patch.dict(sys.modules,{'part4_official_announcement_sync':adapter}), \
      patch.object(mod,'_private_evidence_root',return_value=root), \
      patch('tempfile.mkdtemp',return_value='/synthetic-only/evidence'), \
      patch.object(Path,'chmod'),patch.object(mod.os,'open',return_value=42), \
      patch.object(mod.os,'fdopen',mock_open()):
  result=mod.collect(STOCKS,datetime.fromisoformat(now))
 return result,adapter
from test_public_forward_basis import row, report, ASOF, STOCKS
class SyncTests(unittest.TestCase):
 def setUp(self):
  from pathlib import Path
  self.assertTrue(Path(__file__).with_name('personal_public_forward_sync.py').exists(),'public sync not implemented')
  import personal_public_forward_sync as mod
  self.mod=mod
 def test_dry_run_never_obtains_writer_and_publish_exactly_reads_back(self):
  calls=[];written=[]
  def rpc(w,c,t,name,body):
   calls.append(name)
   if name=='personal_sync_forward_basis':written.extend(body['p_records']);return {'stored':1}
   if name=='personal_get_part4_v2':return {'stocks':[{'code':r['code'],'forwardBasis':{k:v for k,v in r.items() if k!='code'}} for r in written]}
   raise AssertionError(name)
  secret=Mock(return_value='synthetic-capability')
  bridge=SimpleNamespace(load_private_session=lambda:(None,{},'synthetic-token'),private_watchlist=lambda *a:[SimpleNamespace(**s) for s in STOCKS],private_rpc=rpc,part4_writer_secret=secret)
  collector=Mock(return_value=([row()],[report('公司计划不派发现金红利，不送红股，不以公积金转增股本。')],{'source':'eastmoney_public','coverageComplete':True}))
  dry=self.mod.run(bridge,publish=False,now=datetime.fromisoformat(ASOF),collect_fn=collector)
  self.assertFalse(dry['published']);secret.assert_not_called();self.assertEqual(calls,[])
  done=self.mod.run(bridge,publish=True,now=datetime.fromisoformat(ASOF),collect_fn=collector)
  self.assertTrue(done['readbackVerified']);self.assertEqual(calls,['personal_sync_forward_basis','personal_get_part4_v2']);secret.assert_called_once()
 def test_transport_failure_cannot_obtain_writer(self):
  secret=Mock();bridge=SimpleNamespace(load_private_session=lambda:(None,{},''),private_watchlist=lambda *a:[SimpleNamespace(**s) for s in STOCKS],part4_writer_secret=secret)
  with self.assertRaises(ValueError):self.mod.run(bridge,publish=True,now=datetime.fromisoformat(ASOF),collect_fn=Mock(side_effect=ValueError('forward_basis_public_transport_failed')))
  secret.assert_not_called()
 def test_collector_requires_strict_bound_page_metadata(self):
  for change in [{'page_size':None},{'total_hits':2},{'page_index':2},{'page_size':True}]:
   with self.subTest(index=change),self.assertRaisesRegex(ValueError,'forward_basis_public_'):
    offline_collect(self.mod,indexes=[index_page(**change)])
  for change in [{'page_index':2},{'notice_title':'合成乙:2025年半年度报告摘要'},
                 {'security':[{'stock':'000001'}]}]:
   with self.subTest(content=change),self.assertRaisesRegex(ValueError,'forward_basis_public_'):
    offline_collect(self.mod,contents=[content_page(**change)])
  note=index_note();note['codes']=[{'stock_code':'000001'}]
  with self.assertRaisesRegex(ValueError,'forward_basis_public_'):
   offline_collect(self.mod,indexes=[index_page([note])])
 def test_collector_excludes_proven_other_issuer_not_body_conflicts(self):
  foreign=index_note();foreign.update(art_code='AN2026082100000009',title='合成子公司:2026年半年度报告摘要')
  subsidiary={'stock_code':'000002','short_name':'合成子公司'}
  for securities in [[subsidiary],[*index_note()['codes'],subsidiary]]:
   with self.subTest(securities=securities):
    foreign['codes']=securities
    try:
     (rows,docs,meta),adapter=offline_collect(self.mod,indexes=[index_page([foreign,index_note()])])
    except ValueError as error:self.fail(f'proven other issuer should be excluded: {error}')
    self.assertEqual([d['id'] for d in docs],[index_note()['art_code']])
    self.assertEqual(meta.get('excludedOtherIssuerIndexRows'),1)
    self.assertEqual(meta.get('excludedOtherIssuerNotices'),[
     {'code':'600000','id':foreign['art_code'],'issuerCode':'000002','reason':'index_title_identifies_other_issuer'}])
    self.assertEqual(adapter.safe_curl_json.call_count,2)
    self.assertEqual(self.mod.basis.build_records(rows,docs,STOCKS,ASOF)[0]['status'],'ready')
  with self.assertRaisesRegex(ValueError,'report_coverage_incomplete'):
   offline_collect(self.mod,indexes=[index_page([foreign])])
  # Target-bound index/content cannot be reclassified just to hide a bad body.
  doc=report('公司计划不派发现金红利。');doc['pages'][0]='证券代码：000002。'+doc['pages'][0]
  (rows,docs,_),_=offline_collect(self.mod,contents=[content_page(doc)])
  with self.assertRaisesRegex(ValueError,'body_identity_conflict'):
   self.mod.basis.build_records(rows,docs,STOCKS,ASOF)
 def test_collector_parent_prefix_does_not_relabel_embedded_issuer(self):
  for title in ['合成甲:合成子公司股份有限公司2026年半年度报告摘要',
                '合成甲:合成甲关于披露合成子公司2026年半年度报告摘要的公告']:
   with self.subTest(title=title):
    foreign=index_note();foreign.update(art_code='AN2026082100000009',title=title,
     codes=[*index_note()['codes'],{'stock_code':'000002','short_name':'合成子公司'}])
    try:(rows,docs,meta),adapter=offline_collect(self.mod,indexes=[index_page([foreign,index_note()])])
    except ValueError as error:self.fail(f'explicit embedded other issuer should be excluded: {error}')
    self.assertEqual([d['id'] for d in docs],[index_note()['art_code']])
    self.assertEqual(meta.get('excludedOtherIssuerIndexRows'),1)
    self.assertEqual(meta['excludedOtherIssuerNotices'][0]['issuerCode'],'000002')
    self.assertEqual(adapter.safe_curl_json.call_count,2)
 def test_parent_only_index_needs_explicit_legal_title_and_body_subject(self):
  own=report('公司计划不派发现金红利。')
  foreign=copy.deepcopy(own);foreign.update(id='AN2026082100000009',title='合成甲:合成子公司股份有限公司2026年半年度报告摘要')
  foreign['pages']=['合成子公司股份有限公司2026年半年度报告摘要。证券代码：000002。'+own['pages'][0]]
  contents={d['id']:[content_page(d)] for d in [own,foreign]}
  (rows,docs,meta),adapter=offline_collect(self.mod,indexes=[index_page([index_note(foreign),index_note(own)])],contents=contents)
  self.assertEqual([d['id'] for d in docs],[own['id']])
  self.assertEqual(adapter.safe_curl_json.call_count,3) # foreign body must be fetched, never guessed away
  self.assertEqual(meta['excludedOtherIssuerIndexRows'],1)
  exclusion=meta['excludedOtherIssuerNotices'][0]
  self.assertEqual((exclusion['issuerCode'],exclusion['reason']),('000002','index_legal_title_and_body_identify_other_issuer'))
  self.assertEqual(len(exclusion['contentSha256']),64)
  self.assertEqual(exclusion['collection']['index']['title'],foreign['title'])
  self.assertEqual(self.mod.basis.build_records(rows,docs,STOCKS,ASOF)[0]['status'],'ready')
  with self.assertRaisesRegex(ValueError,'report_coverage_incomplete'):
   offline_collect(self.mod,indexes=[index_page([index_note(foreign)])],contents=contents)
  # Without the legal title, matching cover, or a single foreign main code,
  # retain the document for fail-closed identity/coverage validation.
  for change in ['own_title','wrong_cover','mixed_codes','own_body_code','period_mismatch']:
   with self.subTest(change=change):
    bad=copy.deepcopy(foreign)
    if change=='own_title':bad['title']=own['title']
    if change=='wrong_cover':bad['pages'][0]=bad['pages'][0].replace('合成子公司股份有限公司','合成其他公司股份有限公司')
    if change=='mixed_codes':bad['pages'][0]+='证券代码：600000。'
    if change=='own_body_code':bad['pages'][0]=bad['pages'][0].replace('000002','600000')
    if change=='period_mismatch':bad['pages'][0]=bad['pages'][0].replace('2026年半年度报告摘要','2025年半年度报告摘要')
    altered={own['id']:[content_page(own)],bad['id']:[content_page(bad)]}
    (_,retained,metadata),_=offline_collect(self.mod,indexes=[index_page([index_note(bad),index_note(own)])],contents=altered)
    self.assertEqual(len(retained),2);self.assertEqual(metadata['excludedOtherIssuerIndexRows'],0)
 def test_collector_retains_contiguous_api_chunk_receipts(self):
  parts=[content_page(page_size=2,notice_content='第一段'),content_page(page_size=2,page_index=2,notice_content='第二段')]
  (rows,docs,meta),adapter=offline_collect(self.mod,contents=parts)
  self.assertEqual(docs[0]['pages'],['第一段','第二段'])
  receipt=docs[0].get('collection',{})
  self.assertEqual(receipt.get('apiTextPageCount'),2)
  self.assertEqual([p['requestedPage'] for p in receipt.get('pages',[])],[1,2])
  self.assertEqual(receipt.get('index',{}).get('title'),index_note()['title'])
  self.assertTrue(all(len(p['contentSha256'])==64 for p in receipt['pages']))
 def test_future_unrelated_index_row_is_excluded_not_evidence(self):
  future=index_note();future.update(art_code='AN2026091000000002',title='合成甲H股公告-翌日披露表格',notice_date='2026-09-10')
  (rows,docs,meta),adapter=offline_collect(self.mod,indexes=[index_page([future,index_note()])])
  self.assertEqual(meta.get('excludedFutureIndexRows'),1)
  self.assertEqual([d['id'] for d in docs],[index_note()['art_code']])
  self.assertEqual(adapter.safe_curl_json.call_count,2)
  doc=report('公司计划不派发现金红利。');doc['date']='2026-09-10'
  with self.assertRaisesRegex(ValueError,'forward_basis_public_notice_future'):
   self.mod.basis.build_records(rows,[doc],STOCKS,ASOF)
 def test_collector_midyear_report_alias_reaches_current_basis(self):
  doc=report('本次中期每股派发现金红利人民币0.37元（含税）。')
  doc['title']='合成甲:2026年中期报告摘要'
  doc['pages'][0]='证券代码：600000 合成甲2026年中期报告摘要。'+doc['pages'][0]
  (rows,docs,meta),_=offline_collect(self.mod,indexes=[index_page([index_note(doc)])],contents=[content_page(doc)])
  result=self.mod.basis.build_records(rows,docs,STOCKS,ASOF)[0]
  self.assertEqual(result['status'],'ready');self.assertEqual(result['components']['interim']['amount'],.37)
  self.assertEqual(meta['indexCoverage']['600000']['latestInterimSummaryYear'],2026)
  broken=copy.deepcopy(docs);broken[0]['pages'][0]=broken[0]['pages'][0].replace('合成甲2026年中期报告摘要','合成甲2025年中期报告摘要')
  with self.assertRaisesRegex(ValueError,'body_identity_conflict'):
   self.mod.basis.build_records(rows,broken,STOCKS,ASOF)
 def test_collector_proves_latest_known_without_calendar_rollover(self):
  doc=report('公司计划不派发现金红利。');doc.update(title='合成甲:2025年半年度报告摘要',date='2025-08-21')
  for now in ['2026-07-01T09:00:00+08:00','2027-01-01T09:00:00+08:00']:
   with self.subTest(now=now):
    (rows,docs,meta),_=offline_collect(self.mod,indexes=[index_page([index_note(doc)])],contents=[content_page(doc)],now=now)
    result=self.mod.basis.build_records(rows,docs,STOCKS,now)[0]
    self.assertEqual((result['status'],result['amount']),('ready',.88))
    self.assertEqual(result['components']['interim']['year'],2025)
    self.assertEqual(meta.get('basisPolicy'),'latest_known_annual_plus_latest_known_interim')
    self.assertTrue(meta.get('indexCoverage'))
 def test_collector_latest_period_proof_independent_of_table_success(self):
  annual=index_note();annual.update(art_code='AN2026032000000002',title='合成甲:2025年年度报告摘要',notice_date='2026-03-20')
  old=row('2024-12-31','8.8','董事会决议通过','2025-03-20')
  with self.assertRaisesRegex(ValueError,'forward_basis_public_annual_coverage_incomplete'):
   offline_collect(self.mod,indexes=[index_page([index_note(),annual])],table=[old])
  full=index_note();full.update(art_code='AN2026082100000002',title='合成甲:2026年半年度报告')
  historic=index_note();historic.update(title='合成甲:2025年半年度报告摘要',notice_date='2025-08-21')
  with self.assertRaisesRegex(ValueError,'forward_basis_public_report_coverage_incomplete'):
   offline_collect(self.mod,indexes=[index_page([full,historic])])
 def test_collection_clock_and_coverage_are_real_not_replay_asof(self):
  before=datetime.now(self.mod.BJ)
  (_,_,meta),_=offline_collect(self.mod)
  after=datetime.now(self.mod.BJ)
  self.assertIsNotNone(meta.get('startedAt'));self.assertIsNotNone(meta.get('completedAt'))
  started=datetime.fromisoformat(meta['startedAt']);completed=datetime.fromisoformat(meta['completedAt'])
  self.assertTrue(before<=started<=completed<=after)
  self.assertTrue(meta['startedAt'].endswith('+08:00'));self.assertTrue(meta['completedAt'].endswith('+08:00'))
  self.assertEqual(meta.get('sourceCoverage',{}).get('tableReportDateFrom'),'2024-01-01')
  self.assertEqual(meta.get('sourceCoverage',{}).get('asOf'),ASOF)
  self.assertEqual(meta.get('sourceCoverage',{}).get('securityCount'),len(STOCKS))
 def test_confirmed_collector_is_read_only_all_history_and_index_content_bound(self):
  self.assertTrue(callable(getattr(self.mod,'collect_confirmed',None)),'independent confirmed collector missing')
  from test_confirmed_dividend_basis import row as paid_row, notice as paid_notice
  import confirmed_dividend_basis as confirmed
  doc=paid_notice();table=[paid_row()]
  urls=[]
  def get(url):
   urls.append(url)
   if 'datacenter-web.' in url:return {'success':True,'result':{'data':table,'pages':1,'count':1}}
   if 'securities/api/' in url:return {'success':True,'result':{'data':[{**table[0],'ASSIGN_PROGRESS':'实施方案','PAY_CASH_DATE':table[0]['EX_DIVIDEND_DATE']}],'pages':1,'count':1}}
   return content_page(doc)
  bridge=SimpleNamespace(safe_curl_json=Mock(side_effect=get),fetch_notice_page=Mock(return_value=index_page([index_note(doc)])))
  with patch.object(self.mod.os,'open',side_effect=AssertionError('no evidence runtime writes')):
   rows,docs,meta=self.mod.collect_confirmed(STOCKS,datetime.fromisoformat(ASOF),public_adapter=bridge,official_codes=('600000',))
  self.assertNotIn('REPORT_DATE',parse_qs(urlparse(urls[0]).query)['filter'][0])
  self.assertEqual(meta['confirmedCoverage']['scope'],'all_distribution_history')
  self.assertEqual(meta['confirmedCoverage']['rowCount'],1)
  self.assertEqual(meta['confirmedCoverage']['asOf'],ASOF)
  self.assertEqual(docs[0]['collection']['boundary'],'eastmoney_index_and_content_response')
  self.assertEqual(confirmed.build_confirmed_records(rows,STOCKS,ASOF,coverage=meta['confirmedCoverage'],notices=docs)[0]['amount'],.47881)
 def test_confirmed_collection_does_not_fetch_old_text_when_payment_is_explicit(self):
  from test_confirmed_dividend_basis import row as paid_row, notice as paid_notice
  import confirmed_dividend_basis as confirmed
  current=paid_row();old=paid_row(report='1999-12-31',payment='2000-07-10',notice='2000-07-03')
  doc=paid_notice();old_doc=paid_notice(report='1999-12-31',payment='2000-07-10',published='2000-07-03')
  urls=[]
  def get(url):
   urls.append(url)
   if 'datacenter-web.' in url:return {'success':True,'result':{'data':[current,old],'pages':1,'count':2}}
   if 'securities/api/' in url:return {'success':True,'result':{'data':[{**r,'ASSIGN_PROGRESS':'实施方案','PAY_CASH_DATE':r['EX_DIVIDEND_DATE']} for r in [current,old]],'pages':1,'count':2}}
   self.assertNotIn(old_doc['id'],url,'old paid record must not trigger old full-text request')
   return content_page(doc)
  bridge=SimpleNamespace(safe_curl_json=Mock(side_effect=get),fetch_notice_page=Mock(return_value=index_page([index_note(doc),index_note(old_doc)])))
  rows,docs,meta=self.mod.collect_confirmed(STOCKS,datetime.fromisoformat(ASOF),public_adapter=bridge,official_codes=('600000',))
  result=confirmed.build_confirmed_records(rows,STOCKS,ASOF,coverage=meta['confirmedCoverage'],notices=docs,payment_rows=meta['paymentRows'])[0]
  self.assertEqual(result['amount'],.47881)
  self.assertEqual(len(urls),3)
  self.assertEqual(len(docs),1)
 def test_evidence_root_is_release_independent_and_rejects_unsafe_paths(self):
  self.assertEqual(getattr(self.mod,'EVIDENCE_ROOT',None),Path.home()/'.hermes/workspace/stock-dashboard-private-runtime')
  self.assertTrue(callable(getattr(self.mod,'_private_evidence_root',None)))
  import tempfile
  with tempfile.TemporaryDirectory() as tmp:
   parent=Path(tmp).resolve();root=parent/'private-runtime'
   with patch.object(self.mod,'EVIDENCE_ROOT',root):
    self.assertEqual(self.mod._private_evidence_root(),root)
    self.assertEqual(root.stat().st_mode & 0o777,0o700)
    root.chmod(0o755)
    with self.assertRaisesRegex(ValueError,'forward_basis_evidence_path_invalid'):self.mod._private_evidence_root()
    root.chmod(0o700)
   link=parent/'link';link.symlink_to(root,target_is_directory=True)
   for path in [link,link/'nested']:
    with patch.object(self.mod,'EVIDENCE_ROOT',path),self.assertRaisesRegex(ValueError,'forward_basis_evidence_path_invalid'):
     self.mod._private_evidence_root()
 def test_confirmed_payment_source_uses_full_paginated_f10_report_not_latest_ten(self):
  from test_confirmed_dividend_basis import row as paid_row, notice as paid_notice
  import confirmed_dividend_basis as confirmed
  current=paid_row();old=paid_row(report='1999-12-31',payment='2000-07-10',notice='2000-07-03');doc=paid_notice()
  payments=[{**r,'ASSIGN_PROGRESS':'实施方案','PAY_CASH_DATE':r['EX_DIVIDEND_DATE']} for r in [current,old]]
  urls=[]
  def get(url):
   urls.append(url)
   if 'datacenter-web.' in url:return {'success':True,'result':{'data':[current,old],'pages':1,'count':2}}
   if 'securities/api/data/' in url:return {'success':True,'result':{'data':payments,'pages':1,'count':2}}
   if 'emweb.securities.' in url:return {'fhyx':payments[:1]} # PageAjax is a truncated view.
   return content_page(doc)
  bridge=SimpleNamespace(safe_curl_json=Mock(side_effect=get),fetch_notice_page=Mock(return_value=index_page([index_note(doc)])))
  rows,docs,meta=self.mod.collect_confirmed(STOCKS,datetime.fromisoformat(ASOF),public_adapter=bridge,official_codes=('600000',))
  result=confirmed.build_confirmed_records(rows,STOCKS,ASOF,coverage=meta['confirmedCoverage'],notices=docs,payment_rows=meta['paymentRows'])[0]
  self.assertEqual(result['amount'],.47881)
  self.assertTrue(any('reportName=RPT_F10_DIVIDEND_MAIN' in u and 'securities/api/' in u for u in urls))
 def test_structured_confirmed_default_does_not_require_generic_notice_fulltext(self):
  from test_confirmed_dividend_basis import row as paid_row
  r=paid_row()
  def get(url):
   data=[r] if 'datacenter-web.' in url else [{**r,'ASSIGN_PROGRESS':'实施方案','PAY_CASH_DATE':r['EX_DIVIDEND_DATE']}]
   return {'success':True,'result':{'data':data,'pages':1,'count':1}}
  bridge=SimpleNamespace(safe_curl_json=Mock(side_effect=get),fetch_notice_page=Mock(side_effect=AssertionError('generic fulltext not required')))
  rows,docs,meta=self.mod.collect_confirmed(STOCKS,datetime.fromisoformat(ASOF),public_adapter=bridge)
  self.assertEqual(docs,[])
  self.assertEqual(meta['indexPages'],0)
 def test_parser_dry_run_is_default(self):self.assertFalse(self.mod.parse_args([]).publish)
if __name__=='__main__':unittest.main()
