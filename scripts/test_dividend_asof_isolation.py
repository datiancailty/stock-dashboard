"""As-of quarantine regressions: synthetic inputs, no network or credentials."""
import copy
import unittest
from test_public_forward_basis import row, report
import personal_dividend_refresh_sync as sync

ASOF='2026-10-08T18:10:00+08:00'
STOCKS=[{'code':'600000','name':'合成甲'},{'code':'600001','name':'合成乙'}]

def inputs():
    rows=[row('2026-06-30','5','董事会决议通过','2026-10-09'),
          {**row(),'SECURITY_CODE':'600001','SECUCODE':'600001.SH'},
          {**row('2025-06-30','5'),'SECURITY_CODE':'600001','SECUCODE':'600001.SH'}]
    return rows

class FutureNoticeIsolationTests(unittest.TestCase):
    def test_future_notice_is_null_for_affected_stock_not_a_batch_outage(self):
        rows=inputs();before=copy.deepcopy(rows)
        # The strict parser must still reject future evidence.
        with self.assertRaisesRegex(ValueError,'forward_basis_public_notice_future'):
            sync.forward_basis.build_records(rows,[],STOCKS,ASOF)
        try:
            records,isolated=sync.isolated_forward_records(rows,[],STOCKS,ASOF)
        except ValueError as error:
            self.fail(f'future-only evidence still aborts whole batch: {error}')
        self.assertEqual(isolated,['600000'])
        self.assertEqual(rows,before)
        blocked=next(r for r in records if r['code']=='600000')
        self.assertEqual((blocked['status'],blocked['amount'],blocked['reason']),
                         ('missing',None,'public_notice_after_asof'))
        self.assertTrue(all(c['source'] is None and c['amount'] is None and not c['evidence']
                            for c in blocked['components'].values()))
        valid=copy.deepcopy(rows);valid[0]['NOTICE_DATE']='2026-10-08 00:00:00'
        oracle=sync.forward_basis.build_records(valid,[],STOCKS,ASOF)
        self.assertEqual(next(r for r in records if r['code']=='600001'),
                         next(r for r in oracle if r['code']=='600001'))

    def test_one_observation_time_is_used_when_collection_crosses_midnight(self):
        import tempfile
        from pathlib import Path
        from datetime import datetime
        from unittest.mock import patch,Mock
        from test_personal_dividend_refresh_sync import DividendWriterTests
        m,a,_,f,c,fc,ac=DividendWriterTests().fixture()
        start=datetime.fromisoformat('2026-10-08T23:59:59+08:00')
        later=datetime.fromisoformat('2026-10-09T00:00:05+08:00')
        ac.return_value[2]['asOf']=start.isoformat(timespec='seconds')
        clock=Mock();clock.now.side_effect=[start,later,later]
        with tempfile.TemporaryDirectory() as d,patch.object(m,'datetime',clock),\
             patch.object(m.forward_basis,'build_records',return_value=f),\
             patch.object(m.confirmed_basis,'build_confirmed_records',return_value=c):
            result=m.sync(adapter=a,collect_forward=fc,collect_actual=ac,evidence_root=Path(d).resolve())
        self.assertEqual(fc.call_args.args[1],start)
        self.assertEqual(ac.call_args.args[1],start)
        self.assertEqual(result['asOf'],start.isoformat(timespec='seconds'))
        a.part4_writer_secret.assert_not_called();a.private_rpc.assert_not_called()

    def test_date_boundary_recovers_automatically_without_mutating_source(self):
        rows=inputs();before=copy.deepcopy(rows)
        for stamp,isolated in [('2026-10-08T23:59:59+08:00',['600000']),
                               ('2026-10-08T16:00:00+00:00',[]),
                               ('2026-10-09T00:00:00+08:00',[]),
                               ('2026-10-12T18:05:00+08:00',[])]:
            with self.subTest(asof=stamp):
                result,blocked=sync.isolated_forward_records(rows,[],STOCKS,stamp)
                self.assertEqual(blocked,isolated)
                if not isolated:
                    self.assertEqual(result,sync.forward_basis.build_records(rows,[],STOCKS,stamp))
        self.assertEqual(rows,before)

    def test_unreviewed_errors_are_not_hidden_by_isolation(self):
        cases=[('NOTICE_DATE','2026-13-09 00:00:00','date_invalid'),
               ('REPORT_DATE','2027-06-30 00:00:00','future_period'),
               ('SECUCODE','600099.SH','identity_invalid')]
        for field,value,error in cases:
            rows=inputs();rows[0][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,error):
                sync.isolated_forward_records(rows,[],STOCKS,ASOF)

    def test_future_report_is_never_accepted_or_relabelled_as_current(self):
        doc=report('本报告期每10股派发现金红利999元（含税）。')
        doc['date']='2026-10-09'
        rows=inputs();rows[0]['NOTICE_DATE']='2026-10-08 00:00:00'
        out,isolated=sync.isolated_forward_records(rows,[doc],STOCKS,ASOF)
        self.assertEqual(isolated,['600000'])
        self.assertIsNone(out[0]['amount'])
        self.assertNotIn('999',str(out))

    def test_cash_gate_and_exact_readback_apply_to_future_notice(self):
        import tempfile
        from pathlib import Path
        from datetime import datetime
        from unittest.mock import patch
        from test_personal_dividend_refresh_sync import DividendWriterTests
        for ready in [True,False]:
            m,a,_,f,c,fc,ac=DividendWriterTests().fixture()
            stamp=datetime.fromisoformat(ASOF)
            fc.return_value=([inputs()[0]],[],{'coverageComplete':True})
            c[0].update(asOf=ASOF,status='ready' if ready else 'missing',amount=1 if ready else None)
            saved={}
            def rpc(w,config,token,name,body):
                if name.startswith('personal_sync_'):
                    saved['forwardBasis' if name.endswith('forward_basis') else 'confirmedBasis']={k:v for k,v in body['p_records'][0].items() if k!='code'}
                    return {'stored':1}
                return {'stocks':[{'code':'600000',**saved}]}
            a.private_rpc.side_effect=rpc
            with tempfile.TemporaryDirectory() as d,patch.object(m.confirmed_basis,'build_confirmed_records',return_value=c):
                args=dict(adapter=a,now=stamp,publish=True,collect_forward=fc,collect_actual=ac,evidence_root=Path(d).resolve())
                if not ready:
                    with self.assertRaisesRegex(ValueError,'quarantine_without_confirmed_cash'):m.sync(**args)
                    a.part4_writer_secret.assert_not_called();a.private_rpc.assert_not_called()
                else:
                    out=m.sync(**args)
                    self.assertTrue(out['readbackVerified']);self.assertEqual(out['category'],'fallback_used')
                    self.assertEqual(saved['forwardBasis']['reason'],'public_notice_after_asof')
                    self.assertIsNone(saved['forwardBasis']['amount']);self.assertEqual(saved['confirmedBasis']['amount'],1)

class IsolationValidationTests(unittest.TestCase):
    def check_sync(self, rows, docs=None, *, error=None, reason='public_notice_after_asof'):
        """Real collectors/parsers; synthetic transport and in-memory RPC only."""
        import tempfile
        from pathlib import Path
        from datetime import datetime
        from types import SimpleNamespace
        from unittest.mock import Mock
        from test_confirmed_dividend_basis import row as cash_row
        from test_personal_public_forward_sync import offline_collect, index_note, index_page, content_page
        stocks=STOCKS[:1];moment=datetime.fromisoformat(ASOF)
        docs=docs if docs is not None else [report('公司计划不派发现金红利。')]
        before=copy.deepcopy((rows,docs));paid=cash_row()
        payment={**paid,'ASSIGN_PROGRESS':'实施方案','PAY_CASH_DATE':paid['EX_DIVIDEND_DATE']}
        def provider(url):
            table=rows if 'datacenter-web.' in url else [payment]
            return {'success':True,'result':{'data':copy.deepcopy(table),'pages':1,'count':len(table)}}
        public=SimpleNamespace(safe_curl_json=Mock(side_effect=provider),
                 fetch_notice_page=Mock(side_effect=AssertionError('no live notice transport')))
        def forward(requested, stamp):
            self.assertEqual(requested,stocks);self.assertEqual(stamp,moment)
            return offline_collect(sync.collector,table=rows,now=stamp.isoformat(),
                indexes=[index_page([index_note(d) for d in docs])],
                contents={d['id']:[content_page(d)] for d in docs})[0]
        def actual(requested, stamp):
            return sync.collector.collect_confirmed(requested,stamp,public_adapter=public)
        actual_rows,actual_docs,meta=actual(stocks,moment)
        cash=sync.confirmed_basis.build_confirmed_records(actual_rows,stocks,ASOF,
            coverage=meta['confirmedCoverage'],notices=actual_docs,payment_rows=meta['paymentRows'])
        self.assertEqual((cash[0]['status'],cash[0]['amount']),('ready',.47881))
        saved={}
        def rpc(worker,config,token,name,body):
            if name.startswith('personal_sync_'):
                field='forwardBasis' if name.endswith('forward_basis') else 'confirmedBasis'
                saved[field]={k:copy.deepcopy(v) for k,v in body['p_records'][0].items() if k!='code'}
                return {'stored':1}
            self.assertEqual(name,'personal_get_part4_v4')
            return {'stocks':[{'code':'600000',**copy.deepcopy(saved)}]}
        adapter=SimpleNamespace(load_private_session=Mock(return_value=(None,{},'synthetic')),
            private_watchlist=Mock(return_value=[SimpleNamespace(**s) for s in stocks]),
            private_rpc=Mock(side_effect=rpc),part4_writer_secret=Mock(return_value='synthetic'))
        observed=None;result=None
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            try:
                result=sync.sync(adapter=adapter,now=moment,publish=True,
                    collect_forward=forward,collect_actual=actual,evidence_root=root)
            except ValueError as exc:observed=str(exc)
            self.assertEqual((rows,docs),before)
            self.assertEqual(observed,error)
            if error is not None:
                adapter.part4_writer_secret.assert_not_called();adapter.private_rpc.assert_not_called()
                self.assertEqual(list(root.iterdir()),[],'blocked input must not write evidence or state')
            else:
                assert result is not None
                self.assertTrue(result['published']);self.assertTrue(result['readbackVerified'])
                self.assertEqual(saved['confirmedBasis'],{k:v for k,v in cash[0].items() if k!='code'})
                self.assertEqual((saved['forwardBasis']['status'],saved['forwardBasis']['amount'],
                                  saved['forwardBasis']['reason']),('missing',None,reason))
                self.assertTrue(all(c['year'] is None and c['amount'] is None and c['source'] is None
                                    and c['evidence']==[] and c['published_at'] is None
                                    for c in saved['forwardBasis']['components'].values()))
                self.assertEqual([c.args[3] for c in adapter.private_rpc.call_args_list],
                    ['personal_sync_forward_basis','personal_sync_confirmed_dividends','personal_get_part4_v4'])

    def test_future_notice_cannot_hide_later_row_errors_before_any_writer(self):
        from test_confirmed_dividend_basis import row as cash_row
        pending=row('2025-06-30','5','董事会决议通过','2025-08-21')
        cases=[('NOTICE_DATE','2025-13-21','forward_basis_public_date_invalid'),
               ('REPORT_DATE','2027-06-30','forward_basis_public_future_period'),
               ('SECUCODE','600099.SH','forward_basis_public_identity_invalid'),
               ('EQUITY_RECORD_DATE','2025-13-01','forward_basis_public_date_invalid'),
               ('PRETAX_BONUS_RMB','NaN','forward_basis_component_amount_invalid')]
        for field,value,error in cases:
            with self.subTest(field=field):
                bad={**pending,field:value}
                with self.assertRaises(ValueError) as strict:
                    sync.forward_basis.build_records([cash_row(),bad],[],STOCKS[:1],ASOF)
                self.assertEqual(str(strict.exception),error)
                self.check_sync([inputs()[0],cash_row(),bad],error=error)

    def test_future_notice_cannot_hide_errors_in_the_same_row(self):
        from test_confirmed_dividend_basis import row as cash_row
        cases=[({'EQUITY_RECORD_DATE':'2026-13-15'},'forward_basis_public_date_invalid'),
               ({'EX_DIVIDEND_DATE':'2026-13-16'},'forward_basis_public_date_invalid'),
               ({'EQUITY_RECORD_DATE':'2026-10-12','EX_DIVIDEND_DATE':'2026-10-11'},
                'forward_basis_public_distribution_date_conflict'),
               ({'PRETAX_BONUS_RMB':'NaN'},'forward_basis_component_amount_invalid')]
        for changes,error in cases:
            with self.subTest(changes=changes):
                bad={**inputs()[0],**changes}
                self.check_sync([bad,cash_row()],error=error)
                with self.assertRaises(ValueError) as strict:
                    sync.forward_basis.build_records([bad],[],STOCKS[:1],ASOF)
                self.assertEqual(str(strict.exception),error)

    def test_reviewed_quarantine_keeps_strict_null_and_real_ready_cash(self):
        from test_confirmed_dividend_basis import row as cash_row
        self.check_sync([inputs()[0],cash_row()])
        identity=report('本报告期每10股派发现金红利999元（含税）。证券代码000002。')
        self.check_sync([cash_row()],[identity],reason='public_report_identity_quarantined')
        self.check_sync([inputs()[0],cash_row()],[identity])

    def test_quarantine_cannot_hide_incomplete_report_before_any_writer(self):
        from test_confirmed_dividend_basis import row as cash_row
        incomplete=report('公司计划不派发现金红利。')
        incomplete.update(id='AN2026082100000002',
            sourceUrl='https://data.eastmoney.com/notices/detail/600000/AN2026082100000002.html')
        incomplete['pages'][0]='第1页 共2页。'+incomplete['pages'][0]
        identity=report('公司计划不派发现金红利。证券代码000002。')
        for rows,docs in [([inputs()[0],cash_row()],[incomplete]),
                          ([cash_row()],[identity,incomplete])]:
            with self.subTest(future_row=len(rows)==2):
                self.check_sync(rows,docs,error='forward_basis_public_notice_pdf_pages_incomplete')

    def test_quarantine_cannot_hide_errors_in_the_same_report(self):
        from test_confirmed_dividend_basis import row as cash_row
        cases=[({'pages':['第1页 共2页。'+report('无')['pages'][0]]},
                'forward_basis_public_notice_pdf_pages_incomplete'),
               ({'title':'合成甲:2027年半年度报告摘要'},'forward_basis_public_notice_period_invalid'),
               ({'pages':report('本报告期每10股派发现金红利10000000元（含税）。')['pages']},
                'forward_basis_component_amount_invalid')]
        for changes,error in cases:
            for trigger in ('future','identity','future_and_identity'):
                with self.subTest(trigger=trigger,error=error):
                    doc={**report('公司计划不派发现金红利。'),**changes}
                    if 'future' in trigger:doc['date']='2026-10-09'
                    if 'identity' in trigger:doc['pages']=[doc['pages'][0]+'证券代码000002。']
                    # The index collector excludes future documents; exercise
                    # its parser/isolation boundary directly without redating.
                    if trigger=='identity':self.check_sync([cash_row()],[doc],error=error)
                    with self.assertRaises(ValueError) as strict:
                        sync.forward_basis.build_records([cash_row()],[doc],STOCKS[:1],ASOF)
                    self.assertEqual(str(strict.exception),error)
                    before=copy.deepcopy(doc)
                    with self.assertRaisesRegex(ValueError,error):
                        sync.isolated_forward_records([cash_row()],[doc],STOCKS[:1],ASOF)
                    self.assertEqual(doc,before)

    def test_every_report_is_checked_despite_earlier_quarantinable_evidence(self):
        future=report('公司计划不派发现金红利。');future['date']='2026-10-09'
        identity=report('证券代码000002。')
        cases=[({'date':'2026-13-21'},'forward_basis_public_date_invalid'),
               ({'pageCount':2},'forward_basis_public_notice_invalid'),
               ({'title':'合成甲:2027年半年度报告摘要'},'forward_basis_public_notice_period_invalid'),
               ({'pages':['第1页 共2页。'+report('公司计划不派发现金红利。')['pages'][0]]},
                'forward_basis_public_notice_pdf_pages_incomplete')]
        for changes,error in cases:
            bad={**report('公司计划不派发现金红利。'),**changes}
            with self.assertRaises(ValueError) as strict:
                sync.forward_basis.build_records([row()],[bad],STOCKS[:1],ASOF)
            self.assertEqual(str(strict.exception),error)
            for trigger,rows,docs in [('future_row',[inputs()[0],row()],[bad]),
                                      ('future_report',[row()],[future,bad]),
                                      ('body_identity',[row()],[identity,bad])]:
                with self.subTest(trigger=trigger,error=error):
                    before=copy.deepcopy((rows,docs))
                    with self.assertRaises(ValueError) as blocked:
                        sync.isolated_forward_records(rows,docs,STOCKS[:1],ASOF)
                    self.assertEqual(str(blocked.exception),error)
                    self.assertEqual((rows,docs),before)

if __name__=='__main__':unittest.main()
