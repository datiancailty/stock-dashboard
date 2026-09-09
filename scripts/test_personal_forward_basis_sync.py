"""Offline synthetic DTO fixtures only; no credentials/provider/Hosted access."""
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).with_name('personal_forward_basis_sync.py')
STOCKS = [{'code': '600000', 'name': '合成甲'}]
AS_OF = '2026-09-09T12:00:00+08:00'


def dto(heads=None, plans=None, progress=None, **columns):
    return {'title': '合成甲(600000)分红明细', 'code': '600000',
            'field': {'returnName': '分红明细'},
            'table': {'headName': heads or ['2025年度分配', '2025中报', '2026中报'],
                      '分红方案': plans or ['本次年度末期分配10派8.8元(含税，不含已派中期)', '10派5元(含税)', '10派5元(含税)'],
                      '方案进度': progress or ['实施分配', '实施分配', '董事会预案'],
                      **columns}}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATH.exists(), 'forward DTO adapter not implemented')
        spec = importlib.util.spec_from_file_location('personal_forward_test', PATH)
        assert spec is not None and spec.loader is not None
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def test_dto_to_record_latest_interim_replaces_old(self):
        raw = dto()
        before = json.dumps(raw, ensure_ascii=False)
        result = self.mod.build_records([raw], STOCKS, [], AS_OF)[0]
        self.assertEqual(set(result), {'code', 'amount', 'status', 'reason', 'components', 'asOf'})
        self.assertEqual((result['amount'], result['status'], result['reason']), (1.38, 'ready', None))
        annual, interim = (result['components'][k] for k in ('annual', 'interim'))
        self.assertEqual((annual['year'], interim['year'], interim['status']), (2025, 2026, 'announced'))
        self.assertEqual(annual['amount_scope'], 'distribution')
        self.assertEqual(annual['source']['header'], '2025年度分配')
        self.assertEqual(annual['source']['raw_plan'], raw['table']['分红方案'][0])
        self.assertEqual(annual['source']['dto_title'], raw['title'])
        self.assertIsNone(annual['published_at'])
        self.assertEqual(result['asOf'], AS_OF)
        self.assertEqual(json.dumps(raw, ensure_ascii=False), before)
        json.dumps(result, allow_nan=False)

    def test_scope_and_unquantified_latest_fail_closed(self):
        for plan, status, expected, scope in [
            ('10派8.8元', '实施分配', 'ambiguous', 'unknown'),
            ('本次全年累计10派13.8元（含中期）', '实施分配', 'ambiguous', 'full_year'),
            ('本次分红政策预计10派8.8元', '董事会预案', 'missing', 'distribution'),
            ('本次分红比例不低于30%', '董事会预案', 'missing', 'distribution'),
            ('本次10派0元', '董事会预案', 'missing', 'distribution'),
            ('本次10派-8.8元', '董事会预案', 'missing', 'distribution'),
            ('本次不分配', '董事会通过', 'ready', 'distribution'),
            ('本次10派8.8元', '取消', 'negated', 'distribution'),
            ('本次10派8.8元', '未实施', 'missing', 'distribution'),
        ]:
            with self.subTest(plan=plan):
                raw = dto()
                raw['table']['分红方案'][0] = plan
                raw['table']['方案进度'][0] = status
                r = self.mod.build_records([raw], STOCKS, [], AS_OF)[0]
                self.assertEqual(r['status'], expected)
                self.assertEqual(r['components']['annual']['amount_scope'], scope)
                self.assertEqual(r['amount'], .5 if expected == 'ready' else None)
                if expected != 'ready':
                    self.assertTrue(r['reason'])
        raw = dto()
        raw['table']['分红方案'][2] = '-'
        r = self.mod.build_records([raw], STOCKS, [], AS_OF)[0]
        self.assertIsNone(r['amount'])
        self.assertEqual(r['components']['interim']['year'], 2026)

    def test_literal_and_mapped_pretax_columns_no_plan_guess(self):
        raw = dto()
        raw['table']['每股股利(税前,元)'] = ['0.9', '.5', '.5']
        r = self.mod.build_records([raw], STOCKS, [], AS_OF)[0]
        self.assertEqual(r['amount'], 1.38)  # plan precision beats display rounding
        self.assertEqual(r['components']['annual']['source']['raw_pretax'], '0.9')
        raw['nameMap'] = {'p': '分红方案', 's': '方案进度', 'n': '每股股利(税前,元)'}
        for key, name in raw['nameMap'].items():
            raw['table'][key] = raw['table'].pop(name)
        r = self.mod.build_records([raw], STOCKS, [], AS_OF)[0]
        self.assertEqual(r['amount'], 1.38)
        self.assertEqual(r['components']['annual']['source']['name_map'], raw['nameMap'])
        raw['table']['p'][2] = ''
        r = self.mod.build_records([raw], STOCKS, [], AS_OF)[0]
        self.assertEqual(r['amount'], 1.38)  # explicitly pretax per-share numeric
        raw['table']['n'][2] = '约0.5'
        self.assertIsNone(self.mod.build_records([raw], STOCKS, [], AS_OF)[0]['amount'])

    def test_dates_conflicts_and_latest_invalid_row_do_not_fallback(self):
        raw = dto(['2025年度分配', '2025年度分配', '2026中报'],
                  ['本次10派8元', '本次10派8.8元', '10派5元'],
                  ['实施分配', '董事会预案', '董事会预案'],
                  **{'公告日期': ['2026-03-01', '2026-04-01', '2026-08-01']})
        r = self.mod.build_records([raw], STOCKS, [], AS_OF)[0]
        self.assertEqual(r['amount'], 1.38)
        self.assertEqual(r['components']['annual']['published_at'], '2026-04-01')
        raw['table']['公告日期'][0] = '2026-04-01'
        r = self.mod.build_records([raw], STOCKS, [], AS_OF)[0]
        self.assertEqual(r['status'], 'conflict')
        self.assertIsNone(r['amount'])
        self.assertEqual(len(r['components']['annual']['evidence']), 2)
        raw['table']['公告日期'][0] = '2026-02-30'
        with self.assertRaisesRegex(ValueError, 'publication_date_invalid'):
            self.mod.build_records([raw], STOCKS, [], AS_OF)
        raw['table']['公告日期'][0] = '2026-10-01'
        with self.assertRaisesRegex(ValueError, 'publication_after_as_of'):
            self.mod.build_records([raw], STOCKS, [], AS_OF)
        raw = dto()
        raw['table']['headName'][2] = '2026未知期'
        r = self.mod.build_records([raw], STOCKS, [], AS_OF)[0]
        self.assertEqual(r['status'], 'missing')
        self.assertTrue(r['reason'])

    def test_pre_disclosure_requires_exact_official_period_and_plan(self):
        raw = dto()
        raw['table']['方案进度'][2] = '预披露'
        official = {'id': 'eastmoney:AN2026080100000001', 'code': '600000',
                    'date': '2026-08-01', 'type': '中期分红预披露', 'stage': 'pre_disclosure',
                    'title': '合成甲: 2026中期分红预披露',
                    'description': '结构化分红核对 · 10派5元(含税)',
                    'sourceUrl': 'https://data.eastmoney.com/notices/detail/600000/AN2026080100000001.html'}
        for changes in (None, {'code': '000001'}, {'title': '合成甲: 2025中期分红预披露'},
                        {'description': '结构化分红核对 · 现金分红政策比例30%'},
                        {'description': '结构化分红核对 · 10派6元(含税)'}):
            events = [] if changes is None else [dict(official, **changes)]
            r = self.mod.build_records([raw], STOCKS, events, AS_OF)[0]
            self.assertEqual(r['status'], 'missing')
            self.assertEqual(r['reason'], 'pre_disclosure_not_exactly_corroborated')
        r = self.mod.build_records([raw], STOCKS, [official], AS_OF)[0]
        self.assertEqual(r['amount'], 1.38)
        self.assertEqual(r['components']['interim']['published_at'], '2026-08-01')
        self.assertEqual(r['components']['interim']['source']['official_notice']['id'], official['id'])

    def test_announced_pretax_column_is_used_before_implementation(self):
        stocks=[{'code':'000001','name':'合成甲'}]
        dto={'code':'000001.SZ','title':'合成甲的分红方案',
             'table':{'headName':['2025年度末期','2026中报'],
                      '分红方案':['10派8.8元',''],
                      '分红方案进度':['实施分配','股东大会预案'],
                      '每股股利(税前)':['.88','-'],
                      '每股股利(税前,已宣告)':['.88','.5']}}
        row=self.mod.build_records([dto],stocks,[],AS_OF)[0]
        self.assertEqual(row['amount'],1.38)
        self.assertEqual(row['components']['interim']['status'],'announced')

    def test_cross_sectional_batch_rows_are_stock_identities_not_periods(self):
        stocks = [{'code':'000001','name':'合成甲'},{'code':'000002','name':'合成乙'}]
        def dto(year,kind,heads,amounts):
            return {'code':'000001.SZ','title':'合成甲(000001.SZ)等的股权登记日、派息日等',
                    'dataPositionEnum':'MetricLeftEntityTop',
                    'field':{'returnSourceCode':'DAT_ARIGHTREGDATE','startDate':f'{year}-01-01 00:00:00',
                             'endDate':f'{year}-12-31 00:00:00' if kind=='YEAR' else f'{year}-06-30 00:00:00','dateGranularity':kind},
                    'table':{'headName':heads,'每股股利(税前)':amounts,'分红方案进度':['实施分配']*len(heads),
                             '股权登记日':['2026-06-18']*len(heads),'派息日':['2026-06-22']*len(heads),'除权除息日':['2026-06-22']*len(heads)}}
        dtos=[dto(2025,'YEAR',['合成甲(000001.SZ)','合成甲(00001.HK)(最新年结日12-31)','合成乙(000002.SZ)'],['.88','999','.4']),
              dto(2026,'HALF_YEAR',['合成甲(000001.SZ)'],['.5'])]
        rows=self.mod.build_records(dtos,stocks,[],AS_OF)
        self.assertEqual(rows[0]['amount'],1.38)
        self.assertEqual(rows[1]['status'],'missing')
        self.assertIsNone(rows[1]['amount'])
        self.assertEqual(rows[0]['components']['annual']['year'],2025)
        self.assertEqual(rows[0]['components']['interim']['year'],2026)

    def test_annual_auxiliary_matrix_is_not_first_issuer_distribution(self):
        # SFCFJCXG AssignType=1 is an annual declared scalar, not a payment row.
        auxiliary = {
            'code': '600000.SH', 'title': '合成甲(600000.SH)等的每股股利(税前,已宣告)',
            'dataPositionEnum': 'EntityLeftTimeTop',
            'field': {'returnSourceCode': 'SFCFJCXG', 'fixedParamValue': 'AssignType=1,CurType=2',
                      'returnName': '每股股利(税前,已宣告)'},
            'table': {'headName': ['2025'], '合成甲(600000.SH)': ['.88']}}
        result = self.mod.build_records([dto(), auxiliary], STOCKS, [], AS_OF)[0]
        self.assertEqual((result['status'], result['amount']), ('ready', 1.38))
        with self.assertRaisesRegex((ValueError, RuntimeError), 'coverage'):
            self.mod.build_records([auxiliary], STOCKS, [], AS_OF)

    def test_mx_weekly_limit_is_not_missing_dividend_evidence(self):
        from unittest.mock import patch, Mock
        payload = {'status': 0, 'data': {'data': {
            'message': '您本周的妙想金融数据skill请求的数据量已达到上限，额度将于次周恢复',
            'searchDataResultDTO': {'dataTableDTOList': []}}}}
        with patch.dict(self.mod.market.os.environ, {'MX_APIKEY': 'synthetic-key'}), \
             patch.object(self.mod.market.requests, 'post', return_value=Mock(json=lambda: payload)):
            with self.assertRaisesRegex(ValueError, '^forward_basis_provider_weekly_quota_exhausted$'):
                self.mod.query_forward('合成甲(600000)', 2025)

    def test_successful_mx_empty_response_is_transport_contract_failure(self):
        from unittest.mock import patch, Mock
        payload = {'status': 0, 'data': {'data': {'searchDataResultDTO': {'dataTableDTOList': []}}}}
        with patch.dict(self.mod.market.os.environ, {'MX_APIKEY': 'synthetic-key'}), \
             patch.object(self.mod.market.requests, 'post', return_value=Mock(json=lambda: payload)):
            with self.assertRaisesRegex(ValueError, '^forward_basis_provider_empty_result$'):
                self.mod.query_forward('合成甲(600000)', 2025)

    def test_coverage_identity_and_column_alignment_fail_closed(self):
        for dtos, stocks in [([], STOCKS), ([dto()], STOCKS + [{'code': '000001', 'name': '合成乙'}])]:
            with self.assertRaisesRegex((ValueError, RuntimeError), 'coverage'):
                self.mod.build_records(dtos, stocks, [], AS_OF)
        raw = dto()
        raw['table']['方案进度'].pop()
        with self.assertRaisesRegex(ValueError, 'column_length'):
            self.mod.build_records([raw], STOCKS, [], AS_OF)
        raw = dto()
        raw['code'] = '000001'
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.mod.build_records([raw], STOCKS, [], AS_OF)

    def test_worker_defaults_dry_run_and_publish_is_verified(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from datetime import datetime
        stocks = [{'code': f'{600000 + i:06}', 'name': f'合成证券{i}'} for i in range(6)]
        responses = []
        for batch in self.mod.market.dividend_query_batches(stocks):
            values = []
            for s in batch:
                row = dto()
                row.update(code=s['code'], title=s['name'] + s['code'])
                values.append(row)
            responses.append({'data': {'data': {'searchDataResultDTO': {'dataTableDTOList': values}}}})
        calls, written = [], []
        def rpc(worker, config, token, name, body):
            calls.append(name)
            if name == 'personal_get_part4':
                return {'stocks': stocks, 'events': []}
            if name == 'personal_sync_forward_basis':
                written.extend(body['p_records'])
                return {'stored': len(written), 'as_of': AS_OF}
            if name == 'personal_get_part4_v2':
                return {'stocks': [{'code': r['code'], 'forwardBasis': {k: v for k, v in r.items() if k != 'code'}} for r in written]}
            raise AssertionError(name)
        bridge = SimpleNamespace(load_private_session=lambda: (None, {}, 'synthetic-token'),
                                 private_watchlist=lambda *a: [SimpleNamespace(**s) for s in stocks],
                                 private_rpc=rpc, part4_writer_secret=lambda *a: 'synthetic-secret')
        for publish in (False, True):
            calls.clear()
            with patch.object(self.mod.market, 'api_query', side_effect=responses) as query:
                summary = self.mod.run(bridge, publish=publish, now=datetime.fromisoformat(AS_OF), query_fn=self.mod.market.api_query)
            self.assertEqual(query.call_count, 2)
            self.assertEqual(summary['coveredCount'], 6)
            self.assertEqual(summary['published'], publish)
            self.assertEqual(summary['readyCount'], 6)
            self.assertEqual('personal_sync_forward_basis' in calls, publish)
            self.assertEqual('personal_get_part4_v2' in calls, publish)
            self.assertNotIn('600000', json.dumps(summary))
        self.assertFalse(self.mod.parse_args([]).publish)
        self.assertFalse(self.mod.parse_args(['--dry-run']).publish)
        self.assertTrue(self.mod.parse_args(['--publish']).publish)

    def test_staged_sql_contract_does_not_replace_legacy_functions(self):
        import re
        staged = PATH.parents[1] / 'supabase' / 'staged'
        migration = staged / '20260909020000_personal_forward_basis.sql'
        self.assertTrue(migration.exists(), 'separate staged storage not implemented')
        sql = migration.read_text()
        functions = re.findall(r'create(?: or replace)? function public\.([a-z0-9_]+)', sql.lower())
        self.assertEqual(set(functions), {'personal_sync_forward_basis', 'personal_get_part4_v2'})
        self.assertIn('public.personal_get_part4()', sql)
        self.assertIn('forwardBasis', sql)
        self.assertIn('forward_basis_time_regression', sql)
        self.assertIn('forward_basis_same_time_conflict', sql)
        self.assertIn('forward_basis_coverage_incomplete', sql)
        self.assertIn('personal_part4_sync_writer_credentials', sql)
        self.assertIn('enable row level security', sql)
        self.assertNotRegex(sql.lower(), r'(?:update|insert into|delete from) public\.personal_documents')
        self.assertTrue((staged / '20260909020000_personal_forward_basis_postflight.sql').exists())

    def test_actual_dated_entitlement_dto_shape_announced_before_payment(self):
        # User-provided read-only sample; only fixture contains this stock/amount.
        mapped = {'100000000005311': '股权登记日', '100000000000916': '派息日',
                  '100000000000915': '除权除息日', '100000000004698': '方案进度',
                  '100000000004233': '每股股利(税前)'}
        values = []
        for header, amount, reg, pay in [('2025年报', '0.88', '2026-06-18', '2026-06-22'),
                                          ('2026中报', '0.5', '2026-09-10', '2026-09-11')]:
            values.append({'code': '600750.SH', 'title': '华润江中(600750.SH)的股权登记日、派息日等',
                           'field': {'returnSourceCode': 'DAT_ARIGHTREGDATE'}, 'nameMap': mapped,
                           'table': {'headName': [header], '100000000005311': [reg],
                                     '100000000000916': [pay], '100000000000915': [pay],
                                     '100000000004698': ['实施分配'], '100000000004233': [amount]}})
        stocks = [{'code': '600750', 'name': '华润江中'}]
        r = self.mod.build_records(values, stocks, [], AS_OF)[0]
        self.assertEqual((r['status'], r['amount']), ('ready', 1.38))
        self.assertEqual(r['components']['annual']['amount_scope'], 'distribution')
        self.assertEqual(r['components']['annual']['status'], 'implemented')
        self.assertEqual(r['components']['interim']['status'], 'announced')
        self.assertEqual(r['components']['interim']['source']['distribution_dates'],
                         {'registration': '2026-09-10', 'ex_dividend': '2026-09-11', 'payment': '2026-09-11'})
        self.assertEqual(r['components']['interim']['source']['field']['returnSourceCode'], 'DAT_ARIGHTREGDATE')
        after = self.mod.build_records(values, stocks, [], '2026-09-12T12:00:00+08:00')[0]
        self.assertEqual(after['components']['interim']['status'], 'implemented')


if __name__ == '__main__':
    unittest.main()
