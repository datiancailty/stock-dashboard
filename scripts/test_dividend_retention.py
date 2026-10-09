"""Last-good dividend retention: synthetic transport, real parsing, no network."""
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import personal_dividend_refresh_sync as m
from test_public_forward_basis import row

OLD = '2026-09-10T18:05:00+08:00'
NOW = datetime.fromisoformat('2026-10-09T18:05:00+08:00')
STOCKS = [{'code': '600000', 'name': '合成甲'}]


def stored():
    record = m.forward_basis.build_records([row(), row('2025-06-30', '5')], [], STOCKS, OLD)[0]
    assert record['status'] == 'ready'
    return {'stocks': [{'code': '600000', 'forwardBasis': {k: v for k, v in record.items() if k != 'code'},
                       'confirmedBasis': {'status': 'missing', 'amount': None, 'asOf': None}}]}


def cash_stored(as_of=OLD):
    from test_confirmed_dividend_basis import row as paid_row, notice, coverage
    rows = [paid_row()]
    record = m.confirmed_basis.build_confirmed_records(rows, STOCKS, as_of,
                                                      coverage=coverage(rows, as_of), notices=[notice()])[0]
    assert record['status'] == 'ready'
    previous = stored()
    previous['stocks'][0]['forwardBasis'].update(status='missing', amount=None)
    previous['stocks'][0]['confirmedBasis'] = {k: v for k, v in record.items() if k != 'code'}
    return previous


class RetentionTests(unittest.TestCase):
    def adapter(self, previous=None):
        previous = stored() if previous is None else previous
        a = SimpleNamespace(load_private_session=Mock(return_value=(None, {}, 'synthetic')),
                            private_watchlist=Mock(return_value=[SimpleNamespace(**s) for s in STOCKS]),
                            part4_writer_secret=Mock(side_effect=AssertionError('retention must not load writer')))
        def rpc(worker, config, token, name, body):
            self.assertEqual(name, 'personal_get_part4_v4')
            self.assertEqual(body, {})
            return copy.deepcopy(previous)
        a.private_rpc = Mock(side_effect=rpc)
        return a

    def test_cli_retains_verified_old_basis_on_source_timeout_without_writing(self):
        import part4_official_announcement_sync as real
        a = self.adapter(); before = stored(); output = io.StringIO()
        with patch.object(real, 'load_private_session', a.load_private_session), \
             patch.object(real, 'private_watchlist', a.private_watchlist), \
             patch.object(real, 'part4_writer_secret', a.part4_writer_secret), \
             patch.object(real, 'private_rpc', a.private_rpc), \
             patch.object(m.collector, 'collect', side_effect=TimeoutError('synthetic outage')), \
             patch.object(m.collector, 'collect_confirmed') as cash, \
             patch('sys.argv', ['personal_dividend_refresh_sync.py', '--publish']), redirect_stdout(output):
            code = m.main()
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0, result)
        self.assertEqual(result['status'], 'skipped')
        self.assertTrue(result['retainedPrevious'])
        self.assertTrue(result['readbackVerified'])
        self.assertFalse(result['published'])
        self.assertEqual(result['retainedAsOf'], OLD)
        self.assertEqual(before, stored())
        self.assertEqual(a.private_rpc.call_count, 1)
        a.part4_writer_secret.assert_not_called(); cash.assert_not_called()
    def test_public_transport_and_response_failures_retain_without_writers(self):
        import part4_official_announcement_sync as real
        errors = [real.SyncError('official_notice_transport_failed'),
                  real.SyncError('official_notice_response_shape_invalid'),
                  json.JSONDecodeError('synthetic malformed JSON', '', 0),
                  ValueError('forward_basis_public_page_incomplete'),
                  ValueError('confirmed_public_pagination_incomplete')]
        for error in errors:
            with self.subTest(error=type(error).__name__, message=str(error)):
                a = self.adapter()
                out = m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                             collect_forward=Mock(side_effect=error))
                self.assertTrue(out['retainedPrevious']); self.assertFalse(out['published'])
                self.assertIn(out['category'], {'provider_failed', 'invalid_data', 'timeout'})
                a.part4_writer_secret.assert_not_called()

    def test_partial_scan_and_incomplete_metadata_do_not_publish_partial_values(self):
        for phase in ('coverage', 'cash'):
            a = self.adapter()
            forward = Mock(return_value=([row()], [], {'coverageComplete':phase != 'coverage'}))
            cash = Mock(side_effect=TimeoutError('synthetic cash outage'))
            out = m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                         collect_forward=forward, collect_actual=cash)
            self.assertTrue(out['retainedPrevious']); self.assertEqual(out['retainedAsOf'], OLD)
            a.part4_writer_secret.assert_not_called()

    def test_unusable_or_future_previous_data_cannot_be_reported_as_retained(self):
        changes = [('amount', True), ('amount', -1), ('amount', float('nan')),
                   ('amount', None), ('asOf', None), ('asOf', '2026-09-10T18:05:00'),
                   ('asOf', '2026-10-10T18:05:00+08:00'), ('status', 'conflict')]
        for key, value in changes:
            previous = stored(); previous['stocks'][0]['forwardBasis'][key] = value
            a = self.adapter(previous)
            with self.subTest(key=key,value=value), self.assertRaisesRegex(ValueError, 'dividend_retained_'):
                m.sync(adapter=a,now=NOW,publish=True,retain_previous=True,
                       collect_forward=Mock(side_effect=TimeoutError()))
            a.part4_writer_secret.assert_not_called()

    def test_missing_duplicate_or_changed_watchlist_rejects_retention(self):
        for previous in ({}, {'stocks':[]}, {'stocks':stored()['stocks']*2},
                         {'stocks':[{**stored()['stocks'][0], 'code':'600001'}]}):
            a = self.adapter(previous)
            with self.assertRaisesRegex(ValueError, 'retained_coverage_invalid'):
                m.sync(adapter=a,now=NOW,publish=True,retain_previous=True,
                       collect_forward=Mock(side_effect=TimeoutError()))
            a.part4_writer_secret.assert_not_called()

    def test_semantic_errors_auth_and_evidence_failures_are_not_downgraded(self):
        for error in (ValueError('forward_basis_evidence_path_invalid'),
                      ValueError('private_auth_failed'), RuntimeError('synthetic bug')):
            a = self.adapter()
            with self.assertRaises(type(error)):
                m.sync(adapter=a,now=NOW,publish=True,retain_previous=True,
                       collect_forward=Mock(side_effect=error))
            a.private_rpc.assert_not_called(); a.part4_writer_secret.assert_not_called()

    def test_corrupt_ready_components_are_not_usable_previous_data(self):
        for mutate in (lambda b:b.pop('components'),
                       lambda b:b['components']['annual'].update(amount=99),
                       lambda b:b['components']['annual'].update(status='negated'),
                       lambda b:b.update(reason='invalidated')):
            previous = stored(); mutate(previous['stocks'][0]['forwardBasis'])
            a = self.adapter(previous)
            with self.assertRaisesRegex(ValueError,'dividend_retained_basis_invalid'):
                m.sync(adapter=a,now=NOW,retain_previous=True,collect_forward=Mock(side_effect=TimeoutError()))
            a.part4_writer_secret.assert_not_called()

    def test_forward_retention_rejects_invalid_component_metadata(self):
        mutations = [
            ('future_publication', lambda p: p.update(published_at='2099-01-01')),
            ('after_original_asof', lambda p: p.update(published_at='2026-10-01')),
            ('invalid_publication', lambda p: p.update(published_at='2026-02-30')),
            ('naive_publication', lambda p: p.update(published_at='2026-06-01T12:00:00')),
            ('missing_year', lambda p: p.pop('year')),
            ('boolean_year', lambda p: p.update(year=True)),
            ('future_year', lambda p: p.update(year=2099)),
            ('nonzero_no_distribution', lambda p: p.update(status='no_distribution')),
            ('missing_reason', lambda p: p.pop('reason')),
            ('empty_source_null_evidence', lambda p: p.update(source={}, evidence=[None])),
            ('bare_evidence', lambda p: p.update(evidence=[{}])),
            ('wrong_source_identity', lambda p: p['source'].update(dto_code='600001')),
            ('wrong_evidence_identity', lambda p: p['evidence'][0].update(dto_code='600001')),
            ('future_source_publication', lambda p: p['source'].update(published_at='2099-01-01')),
            ('invalid_source_dates', lambda p: p['source'].update(distribution_dates={})),
            ('source_row_boolean', lambda p: p['source'].update(row_index=True)),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                previous = stored(); mutate(previous['stocks'][0]['forwardBasis']['components']['annual'])
                a = self.adapter(previous)
                with self.assertRaisesRegex(ValueError, 'dividend_retained_'):
                    m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                           collect_forward=Mock(side_effect=TimeoutError()))
                self.assertEqual(a.private_rpc.call_count, 1)
                a.part4_writer_secret.assert_not_called()

    def test_valid_public_and_legacy_forward_dtos_keep_original_evidence(self):
        import personal_forward_basis_sync as legacy
        from test_personal_forward_basis_sync import dto
        from test_public_forward_basis import report
        records = [m.forward_basis.build_records([row()], [report('公司计划不派发现金红利。')], STOCKS, OLD)[0],
                   legacy.build_records([dto()], STOCKS, [], OLD)[0],
                   legacy.build_records([dto(plans=['本次不分配', '不分配', '不分配'],
                                             progress=['董事会通过'] * 3)], STOCKS, [], OLD)[0]]
        for record in records:
            with self.subTest(amount=record['amount'], source=record['components']['annual']['source']['field']):
                self.assertEqual(record['status'], 'ready')
                previous = stored(); previous['stocks'][0]['forwardBasis'] = {k: v for k, v in record.items() if k != 'code'}
                before = json.dumps(previous, sort_keys=True)
                a = self.adapter(previous)
                out = m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                             collect_forward=Mock(side_effect=TimeoutError()))
                self.assertTrue(out['retainedPrevious']); self.assertFalse(out['published'])
                self.assertEqual(out['retainedAsOf'], OLD)
                self.assertEqual(json.dumps(previous, sort_keys=True), before)
                a.part4_writer_secret.assert_not_called()

    def test_normalizer_multiversion_evidence_can_keep_distinct_source_dates(self):
        import personal_forward_basis_sync as legacy
        from test_personal_forward_basis_sync import dto
        from test_public_forward_basis import report
        dated = dto(**{'公告日期': ['2026-06-01', '2025-08-20', '2026-08-21']})
        # Both are valid writer DTOs: one source was undated, one has dates.
        multi = legacy.build_records([dto(), dated], STOCKS, [], OLD)[0]
        annual = multi['components']['annual']
        self.assertEqual(len(annual['evidence']), 2)
        self.assertNotEqual(annual['evidence'][0]['published_at'], annual['evidence'][1]['published_at'])
        annotated = copy.deepcopy(multi)
        # This source fact is not the component's semantic reason (SQL permits it).
        annotated['components']['annual']['evidence'][1]['normalization_reason'] = 'legacy_source_note'
        early = row(); early.update(NOTICE_DATE='2026-03-01', ASSIGN_PROGRESS='董事会决议通过')
        versions = m.forward_basis.build_records([early, row(), row('2025-06-30', '5')], [], STOCKS, OLD)[0]
        half = row('2026-06-30', '5')
        half.update(NOTICE_DATE='2026-08-22', EQUITY_RECORD_DATE='2026-08-27', EX_DIVIDEND_DATE='2026-08-28')
        with_report = m.forward_basis.build_records([row(), half],
                           [report('每10股派发现金红利5元（含税）。')], STOCKS, OLD)[0]
        for record in (multi, annotated, versions, with_report):
            with self.subTest(source=record['components']['annual']['source']['published_at']):
                self.assertEqual(record['status'], 'ready')
                previous = stored(); previous['stocks'][0]['forwardBasis'] = {k: v for k, v in record.items() if k != 'code'}
                before = json.dumps(previous, sort_keys=True)
                a = self.adapter(previous)
                try:
                    out = m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                                 collect_forward=Mock(side_effect=TimeoutError()))
                except ValueError as error:
                    self.fail('valid normalizer multi-source DTO rejected: ' + str(error))
                self.assertTrue(out['retainedPrevious']); self.assertEqual(out['retainedAsOf'], OLD)
                self.assertFalse(out['published']); a.part4_writer_secret.assert_not_called()
                self.assertEqual(before, json.dumps(previous, sort_keys=True))

    def test_manual_override_has_priority_over_corrupt_automatic_basis(self):
        previous = stored(); previous['stocks'][0].update(forwardBasis={'status': 'ready', 'amount': 99},
                                                        forwardBasisOverride=0, forwardOverrideAsOf=OLD)
        a = self.adapter(previous)
        out = m.sync(adapter=a, now=NOW, retain_previous=True, collect_forward=Mock(side_effect=TimeoutError()))
        self.assertTrue(out['retainedPrevious']); self.assertEqual(out['retainedAsOf'], OLD)
        a.part4_writer_secret.assert_not_called()

    def test_cash_retention_rejects_invalid_components_window_and_sources(self):
        mutations = [
            ('future_payment', lambda b: b['components'][0].update(paymentDate='2099-01-01')),
            ('after_original_asof', lambda b: b['components'][0].update(paymentDate='2026-10-01')),
            ('window_start_exclusive', lambda b: b['components'][0].update(paymentDate=b['windowStart'])),
            ('invalid_report_date', lambda b: b['components'][0].update(reportDate='2025-02-30')),
            ('report_after_payment', lambda b: b['components'][0].update(reportDate='2026-07-11')),
            ('noncanonical_payment', lambda b: b['components'][0].update(paymentDate='2026-07-10 00:00:00')),
            ('missing_window', lambda b: b.pop('windowStart')),
            ('bad_window_end', lambda b: b.update(windowEnd='2026-10-09')),
            ('bad_window_start', lambda b: b.update(windowStart='2025-09-11')),
            ('bare_numeric_component', lambda b: b.update(components=[{'amount': b['amount']}])),
            ('wrong_url_issuer', lambda b: b['components'][0].update(sourceUrl='https://data.eastmoney.com/yjfp/detail/600001.html')),
            ('untrusted_url', lambda b: b['components'][0].update(sourceUrl='https://synthetic.invalid/')),
            ('missing_plan', lambda b: b['components'][0].pop('plan')),
            ('empty_plan', lambda b: b['components'][0].update(plan='')),
            ('duplicate_component', lambda b: b.update(components=b['components'] * 2, amount=b['amount'] * 2)),
            ('zero_cash_component', lambda b: (b.update(amount=0), b['components'][0].update(amount=0))),
            ('cash_precision', lambda b: (b.update(amount=0.1234567890123), b['components'][0].update(amount=0.1234567890123))),
        ]
        for label, mutate in mutations:
            with self.subTest(label=label):
                previous = cash_stored(); mutate(previous['stocks'][0]['confirmedBasis'])
                a = self.adapter(previous)
                with self.assertRaisesRegex(ValueError, 'dividend_retained_'):
                    m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                           collect_forward=Mock(side_effect=TimeoutError()))
                a.part4_writer_secret.assert_not_called()

    def test_valid_cash_retains_original_window_without_rolling_it_forward(self):
        from test_confirmed_dividend_basis import row as paid_row, notice, coverage
        cases = [cash_stored()]
        # Actual producer supports both bound notices and structured F10 URLs.
        rows = [paid_row()]
        payments = [{**rows[0], 'ASSIGN_PROGRESS': '实施方案', 'PAY_CASH_DATE': '2026-07-10',
                     'sourceUrl': 'https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?type=web&code=SH600000'}]
        structured = m.confirmed_basis.build_confirmed_records(rows, STOCKS, OLD,
                            coverage=coverage(rows, OLD), payment_rows=payments)[0]
        for stamp in (OLD, '2024-02-29T00:00:00+08:00'):
            # Fully covered empty cash is valid zero, including leap-day windows.
            record = m.confirmed_basis.build_confirmed_records([], STOCKS, stamp, coverage=coverage([], stamp))[0]
            previous = cash_stored(); previous['stocks'][0]['confirmedBasis'] = {k: v for k, v in record.items() if k != 'code'}
            cases.append(previous)
        previous = cash_stored(); previous['stocks'][0]['confirmedBasis'] = {k: v for k, v in structured.items() if k != 'code'}
        cases.append(previous)
        for previous in cases:
            with self.subTest(asof=previous['stocks'][0]['confirmedBasis']['asOf']):
                before = json.dumps(previous, sort_keys=True)
                a = self.adapter(previous)
                out = m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                             collect_forward=Mock(side_effect=TimeoutError()))
                self.assertTrue(out['retainedPrevious']); self.assertFalse(out['published'])
                self.assertEqual(out['retainedAsOf'], previous['stocks'][0]['confirmedBasis']['asOf'])
                self.assertEqual(json.dumps(previous, sort_keys=True), before)
                a.part4_writer_secret.assert_not_called()

    def test_selected_manual_and_confirmed_fallback_keep_original_dates(self):
        cases=[]
        manual=stored();manual['stocks'][0].update(forwardBasisOverride=0,forwardOverrideAsOf=OLD);cases.append(manual)
        cash=stored();stock=cash['stocks'][0];stock['forwardBasis'].update(status='negated',amount=None)
        stock['confirmedBasis']={'status':'ready','reason':None,'amount':0,'asOf':OLD,
             'source':m.confirmed_basis.SOURCE,'components':[],
             'windowStart':'2025-09-10','windowEnd':'2026-09-10'};cases.append(cash)
        for previous in cases:
            before=copy.deepcopy(previous);a=self.adapter(previous)
            out=m.sync(adapter=a,now=NOW,retain_previous=True,collect_forward=Mock(side_effect=TimeoutError()))
            self.assertEqual(out['retainedAsOf'],OLD);self.assertEqual(previous,before)
            a.part4_writer_secret.assert_not_called()

    def test_fresh_zero_and_changed_values_replace_old_after_outage_recovers(self):
        import tempfile
        from pathlib import Path
        from test_personal_dividend_refresh_sync import DividendWriterTests
        for amount in (0,2.75):
            _,a,n,forward,cash,fc,ac=DividendWriterTests().fixture()
            forward[0].update(status='ready',amount=amount)
            saved={};names=[]
            def rpc(w,c,t,name,body):
                names.append(name)
                if name.startswith('personal_sync_'):
                    saved['forwardBasis' if name.endswith('forward_basis') else 'confirmedBasis']={k:v for k,v in body['p_records'][0].items() if k!='code'}
                    return {'stored':1}
                return {'stocks':[{'code':'600000',**copy.deepcopy(saved)}]}
            a.private_rpc.side_effect=rpc
            with tempfile.TemporaryDirectory() as folder,patch.object(m.forward_basis,'build_records',return_value=forward),patch.object(m.confirmed_basis,'build_confirmed_records',return_value=cash):
                out=m.sync(adapter=a,now=n,publish=True,retain_previous=True,collect_forward=fc,collect_actual=ac,evidence_root=Path(folder).resolve())
            self.assertTrue(out['published']);self.assertNotIn('retainedPrevious',out)
            self.assertEqual(saved['forwardBasis']['amount'],amount)
            self.assertEqual(names,['personal_sync_forward_basis','personal_sync_confirmed_dividends','personal_get_part4_v4'])

    def test_real_parser_recovery_uses_zero_interim_not_retained_previous(self):
        import tempfile
        from pathlib import Path
        from test_public_forward_basis import report
        from test_confirmed_dividend_basis import coverage
        a = self.adapter()
        retained = m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                          collect_forward=Mock(side_effect=TimeoutError()))
        self.assertTrue(retained['retainedPrevious']); a.part4_writer_secret.assert_not_called()
        saved = {}; names = []
        def rpc(w, c, token, name, body):
            names.append(name)
            if name.startswith('personal_sync_'):
                field = 'forwardBasis' if name.endswith('forward_basis') else 'confirmedBasis'
                saved[field] = {k: v for k, v in body['p_records'][0].items() if k != 'code'}
                return {'stored': 1}
            return {'stocks': [{'code': '600000', **copy.deepcopy(saved)}]}
        a.private_rpc.side_effect = rpc
        a.part4_writer_secret = Mock(return_value='synthetic-writer')
        with tempfile.TemporaryDirectory() as folder:
            out = m.sync(adapter=a, now=NOW, publish=True, retain_previous=True, evidence_root=Path(folder),
                         collect_forward=lambda *args: ([row()], [report('公司计划不派发现金红利。')], {'coverageComplete': True}),
                         collect_actual=lambda *args: ([], [], {'asOf': NOW.isoformat(),
                                                    'confirmedCoverage': coverage([], NOW.isoformat()), 'paymentRows': []}))
        self.assertTrue(out['published']); self.assertNotIn('retainedPrevious', out)
        self.assertEqual(saved['forwardBasis']['components']['interim']['status'], 'no_distribution')
        self.assertEqual(saved['forwardBasis']['components']['interim']['amount'], 0)
        self.assertEqual(saved['forwardBasis']['amount'], .88)
        self.assertEqual(saved['forwardBasis']['asOf'], NOW.isoformat())
        self.assertEqual(names, ['personal_sync_forward_basis', 'personal_sync_confirmed_dividends', 'personal_get_part4_v4'])

    def test_write_or_postwrite_readback_failure_never_enters_retention(self):
        import tempfile
        from pathlib import Path
        from test_personal_dividend_refresh_sync import DividendWriterTests
        for phase in ('first','second','readback'):
            _,a,n,forward,cash,fc,ac=DividendWriterTests().fixture();names=[]
            def rpc(w,c,t,name,body):
                names.append(name)
                index={'first':1,'second':2,'readback':3}[phase]
                if len(names)==index:raise TimeoutError('unknown writer or readback outcome')
                return {'stored':1}
            a.private_rpc.side_effect=rpc
            with tempfile.TemporaryDirectory() as folder,patch.object(m.forward_basis,'build_records',return_value=forward),patch.object(m.confirmed_basis,'build_confirmed_records',return_value=cash),self.assertRaises(TimeoutError):
                m.sync(adapter=a,now=n,publish=True,retain_previous=True,collect_forward=fc,collect_actual=ac,evidence_root=Path(folder).resolve())
            self.assertEqual(len(names),{'first':1,'second':2,'readback':3}[phase])

    def test_strict_business_parser_still_rejects_invalid_or_future_evidence(self):
        from test_personal_dividend_refresh_sync import DividendWriterTests
        for error in ('forward_basis_public_future_period','forward_basis_public_date_invalid'):
            _,a,n,forward,cash,fc,ac=DividendWriterTests().fixture()
            with patch.object(m.forward_basis,'build_records',side_effect=ValueError(error)),self.assertRaisesRegex(ValueError,error):
                m.sync(adapter=a,now=n,publish=True,retain_previous=True,collect_forward=fc,collect_actual=ac)
            a.private_rpc.assert_not_called();a.part4_writer_secret.assert_not_called()

    def test_postwrite_readback_does_not_confuse_boolean_with_number(self):
        import tempfile
        from pathlib import Path
        from test_personal_dividend_refresh_sync import DividendWriterTests
        _,a,n,forward,cash,fc,ac=DividendWriterTests().fixture();saved={}
        def rpc(w,c,t,name,body):
            if name.startswith('personal_sync_'):
                saved['forwardBasis' if name.endswith('forward_basis') else 'confirmedBasis']={k:v for k,v in body['p_records'][0].items() if k!='code'}
                return {'stored':1}
            altered=copy.deepcopy(saved);altered['confirmedBasis']['amount']=True
            return {'stocks':[{'code':'600000',**altered}]}
        a.private_rpc.side_effect=rpc
        with tempfile.TemporaryDirectory() as folder,patch.object(m.forward_basis,'build_records',return_value=forward),patch.object(m.confirmed_basis,'build_confirmed_records',return_value=cash),self.assertRaisesRegex(ValueError,'dividend_readback_mismatch'):
            m.sync(adapter=a,now=n,publish=True,retain_previous=True,collect_forward=fc,collect_actual=ac,evidence_root=Path(folder).resolve())
        self.assertEqual(a.private_rpc.call_count,3)

    def test_retained_run_does_not_advance_daily_success_and_recovery_does(self):
        import tempfile
        from pathlib import Path
        import dashboard_refresh_daily as daily
        import dashboard_refresh_sync as chain
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();path=root/'state.json'
            daily.save_state(path,{'successfulDate':'2026-10-08'})
            def stage(name,*args):
                if name=='forward':return {'status':'skipped','category':'timeout','published':False,'retainedPrevious':True,'readbackVerified':True,'retainedAsOf':OLD}
                return {'status':'ok','published':True,'readbackVerified':True}
            with patch.object(chain,'run_stage',side_effect=stage):
                result=daily.run_once(state_dir=root,publish=True,now=NOW,release_check=lambda:'synthetic',refresh_fn=lambda publish:chain.run_refresh(publish))
            self.assertEqual(result['status'],'error');self.assertFalse(result['published'])
            self.assertEqual(daily.read_state(path)['successfulDate'],'2026-10-08')
            self.assertTrue(result['stages']['forward']['retainedPrevious'])
            with patch.object(chain,'run_stage',return_value={'status':'ok','published':True,'readbackVerified':True}):
                result=daily.run_once(state_dir=root,publish=True,now=NOW.replace(day=12),release_check=lambda:'synthetic',refresh_fn=lambda publish:chain.run_refresh(publish))
            self.assertEqual(result['status'],'ok')
            self.assertEqual(daily.read_state(path)['successfulDate'],'2026-10-12')

    def test_unattested_skips_or_other_stage_skips_cannot_claim_retention(self):
        import subprocess
        import dashboard_refresh_sync as chain
        good={'status':'skipped','category':'timeout','published':False,'retainedPrevious':True,
              'readbackVerified':True,'retainedAsOf':OLD,'watchlistCount':1,'usableCount':1}
        for name,change in [('news',{}),('forward',{'readbackVerified':1}),('forward',{'usableCount':0}),
                            ('forward',{'published':True}),('forward',{'retainedAsOf':'2099-01-01T00:00:00+08:00'})]:
            payload={**good,**change}
            with patch.object(chain,'_run_child',return_value=subprocess.CompletedProcess([],0,json.dumps(payload),'')):
                out=chain.run_stage(name,['synthetic'],True)
            self.assertEqual(out['status'],'error');self.assertNotIn('retainedPrevious',out)

    def test_stage_reports_retention_as_skipped_not_fresh_publication(self):
        import subprocess
        import dashboard_refresh_sync as chain
        payload = {'status':'skipped','category':'timeout','published':False,
                   'retainedPrevious':True,'readbackVerified':True,'retainedAsOf':OLD,
                   'watchlistCount':1,'usableCount':1,'source':'eastmoney_public'}
        with patch.object(chain,'_run_child',return_value=subprocess.CompletedProcess([],0,json.dumps(payload),'')):
            out = chain.run_stage('forward',['synthetic'],True)
        self.assertEqual(out['status'],'skipped')
        self.assertTrue(out['retainedPrevious']); self.assertEqual(out['retainedAsOf'],OLD)
        self.assertFalse(out['published'])


if __name__ == '__main__':
    unittest.main()
