"""Offline regressions using public Yili rows, never private holdings/account data.

Fixture fields are projected verbatim from the public RPT_SHAREBONUS_DET and
RPT_F10_DIVIDEND_MAIN capture of 2026-09-30. The warrant identity is independently
reviewed against AN201202260003906600 (2006-11-03, signed 2006-11-02): 3 warrants
per 10 shares, issue price 0 yuan/warrant, free distribution, record 2006-11-08.
This is not a cash amount, an age cutoff, or a new forward-basis policy.
"""
import copy
import unittest

import confirmed_dividend_basis as cash

ASOF = '2026-09-30T17:42:32+08:00'
STOCKS = [{'code': '600887', 'name': '伊利股份'}]
F10_URL = 'https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?type=web&code=SH600887'


def public_rows():
    return [
        {'SECURITY_CODE': '600887', 'SECUCODE': '600887.SH',
         'REPORT_DATE': '2025-12-31 00:00:00', 'NOTICE_DATE': '2026-05-29 00:00:00',
         'ASSIGN_PROGRESS': '实施分配', 'PRETAX_BONUS_RMB': 9,
         'IMPL_PLAN_PROFILE': '10派9.00元(含税,扣税后8.10元)',
         'EQUITY_RECORD_DATE': '2026-06-04 00:00:00', 'EX_DIVIDEND_DATE': '2026-06-05 00:00:00'},
        {'SECURITY_CODE': '600887', 'SECUCODE': '600887.SH',
         'REPORT_DATE': '2025-09-30 00:00:00', 'NOTICE_DATE': '2025-12-09 00:00:00',
         'ASSIGN_PROGRESS': '实施分配', 'PRETAX_BONUS_RMB': 4.8,
         'IMPL_PLAN_PROFILE': '10派4.80元(含税,扣税后4.32元)',
         'EQUITY_RECORD_DATE': '2025-12-16 00:00:00', 'EX_DIVIDEND_DATE': '2025-12-17 00:00:00'},
    ]


def public_payments():
    return [
        {'SECURITY_CODE': '600887', 'SECUCODE': '600887.SH', 'sourceUrl': F10_URL,
         'REPORT_DATE': '2025年报', 'NOTICE_DATE': '2026-05-29 00:00:00',
         'ASSIGN_PROGRESS': '实施方案', 'IMPL_PLAN_PROFILE': '10派9元',
         'EQUITY_RECORD_DATE': '2026-06-04 00:00:00', 'EX_DIVIDEND_DATE': '2026-06-05 00:00:00',
         'PAY_CASH_DATE': '2026-06-05 00:00:00', 'IS_PAYCASH': '0'},
        {'SECURITY_CODE': '600887', 'SECUCODE': '600887.SH', 'sourceUrl': F10_URL,
         'REPORT_DATE': '2025三季报', 'NOTICE_DATE': '2025-12-09 00:00:00',
         'ASSIGN_PROGRESS': '实施方案', 'IMPL_PLAN_PROFILE': '10派4.8元',
         'EQUITY_RECORD_DATE': '2025-12-16 00:00:00', 'EX_DIVIDEND_DATE': '2025-12-17 00:00:00',
         'PAY_CASH_DATE': '2025-12-17 00:00:00', 'IS_PAYCASH': '0'},
    ]


def public_warrant():
    return {'SECURITY_CODE': '600887', 'SECUCODE': '600887.SH', 'sourceUrl': F10_URL,
            'REPORT_DATE': '2006其他分配', 'NOTICE_DATE': '2006-11-03 00:00:00',
            'EQUITY_RECORD_DATE': '2006-11-08 00:00:00', 'ASSIGN_PROGRESS': '实施方案',
            'IMPL_PLAN_PROFILE': None, 'PAY_CASH_DATE': None, 'EX_DIVIDEND_DATE': None,
            'IS_PAYCASH': '0', 'TOTAL_DIVIDEND': 86368960.84}


def coverage(rows):
    return {'complete': True, 'scope': 'all_distribution_history',
            'source': cash.SOURCE, 'asOf': ASOF, 'codes': ['600887'], 'rowCount': len(rows)}


def build(rows, payments, proof=None):
    return cash.build_confirmed_records(rows, STOCKS, ASOF,
                coverage=coverage(rows) if proof is None else proof, payment_rows=payments)[0]


class YiliWarrantTests(unittest.TestCase):
    def test_public_warrant_does_not_poison_two_verified_ttm_cash_payments(self):
        rows, payments = public_rows(), public_payments() + [public_warrant()]
        before = copy.deepcopy((rows, payments))
        result = build(rows, payments)
        self.assertEqual((result['status'], result['amount'], result['reason']),
                         ('ready', 1.38, None),
                         'Reviewed 2006 warrant must not suppress independently paid cash')
        self.assertEqual([(c['paymentDate'], c['amount']) for c in result['components']],
                         [('2025-12-17', 0.48), ('2026-06-05', 0.90)])
        self.assertEqual((result['windowStart'], result['windowEnd']),
                         ('2025-09-30', '2026-09-30'))
        self.assertEqual((rows, payments), before)

    def test_classification_is_bound_to_original_notice_content(self):
        events = [e for e in cash.NONCASH_F10_EVENTS if e['SECURITY_CODE'] == '600887']
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['announcementUrl'],
                         'https://data.eastmoney.com/notices/detail/600887/AN201202260003906600.html')
        self.assertEqual(event['contentSha256'],
                         '1e94703d65162811c60f55d7caf0d2e6433e1cd299c5df158a873e0df347836c')
        with self.assertRaises(TypeError):
            event['SECURITY_CODE'] = '600000'  # pyright: ignore[reportIndexIssue]

    def test_each_identity_and_empty_cash_field_is_required(self):
        warrant = public_warrant()
        for empty in (None, ''):
            exact = {**warrant, 'NOTICE_DATE': '2006-11-03', 'EQUITY_RECORD_DATE': '2006-11-08',
                     **{k: empty for k in ('IMPL_PLAN_PROFILE', 'PAY_CASH_DATE', 'EX_DIVIDEND_DATE')}}
            self.assertTrue(cash._verified_noncash_f10(exact, '600887'))
            self.assertEqual(build(public_rows(), public_payments() + [exact])['amount'], 1.38)
        required = ('SECURITY_CODE', 'SECUCODE', 'sourceUrl', 'REPORT_DATE', 'ASSIGN_PROGRESS',
                    'IS_PAYCASH', 'NOTICE_DATE', 'EQUITY_RECORD_DATE', 'IMPL_PLAN_PROFILE',
                    'PAY_CASH_DATE', 'EX_DIVIDEND_DATE')
        for field in required:
            with self.subTest(missing=field):
                other = {k: v for k, v in warrant.items() if k != field}
                self.assertFalse(cash._verified_noncash_f10(other, '600887'))
        for change in [
                {'SECURITY_CODE': '600000'}, {'SECUCODE': '600000.SH'}, {'SECUCODE': '600887.SZ'},
                {'sourceUrl': 'https://evil.invalid/'}, {'sourceUrl': F10_URL + '&extra=1'},
                {'sourceUrl': F10_URL.replace('SH600887', 'SH600000')},
                {'REPORT_DATE': '2006年报'}, {'ASSIGN_PROGRESS': '实施分配'},
                {'IS_PAYCASH': '1'}, {'IS_PAYCASH': 0}, {'IS_PAYCASH': None},
                {'NOTICE_DATE': '2006-11-02'}, {'NOTICE_DATE': 'invalid'},
                {'EQUITY_RECORD_DATE': '2006-11-09'}, {'EQUITY_RECORD_DATE': None},
                {'IMPL_PLAN_PROFILE': '10派1元'}, {'IMPL_PLAN_PROFILE': ' '},
                {'PAY_CASH_DATE': '2006-11-08'}, {'PAY_CASH_DATE': '2026-06-05'},
                {'EX_DIVIDEND_DATE': '2006-11-08'}]:
            with self.subTest(change=change):
                self.assertFalse(cash._verified_noncash_f10({**warrant, **change}, '600887'))
        self.assertFalse(cash._verified_noncash_f10(warrant, '600900'))

    def test_near_matches_cannot_make_cash_result_ready(self):
        for change in [{'SECUCODE': '600887.SZ'}, {'sourceUrl': F10_URL + '&extra=1'},
                       {'REPORT_DATE': '2006年报'}, {'NOTICE_DATE': '2006-11-02'},
                       {'EQUITY_RECORD_DATE': '2006-11-09'}, {'IS_PAYCASH': '1'},
                       {'IMPL_PLAN_PROFILE': '10派1元'}, {'PAY_CASH_DATE': '2026-06-05'},
                       {'EX_DIVIDEND_DATE': '2006-11-08'}]:
            with self.subTest(change=change):
                result = build(public_rows(), public_payments() + [{**public_warrant(), **change}])
                self.assertIsNone(result['amount'])
                self.assertIn(result['status'], ('missing', 'conflict'))
        other = {**public_warrant(), 'SECURITY_CODE': '600000', 'SECUCODE': '600000.SH',
                 'sourceUrl': F10_URL.replace('SH600887', 'SH600000')}
        with self.assertRaisesRegex(ValueError, 'confirmed_public_identity_invalid'):
            build(public_rows(), public_payments() + [other])
        isolated = cash.build_confirmed_records([], [{'code': '600000'}], ASOF,
                     coverage={**coverage([]), 'codes': ['600000']}, payment_rows=[other])[0]
        self.assertEqual((isolated['status'], isolated['amount']), ('missing', None))

    def test_classified_warrant_does_not_hide_another_unknown_historical_row(self):
        unknown = {**public_warrant(), 'NOTICE_DATE': '2001-11-03',
                   'EQUITY_RECORD_DATE': '2001-11-08', 'REPORT_DATE': '2001其他分配'}
        result = build(public_rows(), public_payments() + [public_warrant(), unknown])
        self.assertEqual((result['status'], result['amount'], result['reason']),
                         ('missing', None, 'confirmed_payment_evidence_missing'))
        self.assertEqual(len(result['components']), 2)

    def test_same_day_cash_table_claim_still_requires_payment_evidence(self):
        old_cash = {**public_rows()[0], 'REPORT_DATE': '2005-12-31',
                    'NOTICE_DATE': '2006-11-03', 'EQUITY_RECORD_DATE': '2006-11-08',
                    'EX_DIVIDEND_DATE': '2006-11-09', 'PRETAX_BONUS_RMB': 1,
                    'IMPL_PLAN_PROFILE': '10派1元(含税)'}
        result = build(public_rows() + [old_cash], public_payments() + [public_warrant()])
        self.assertEqual((result['status'], result['amount'], result['reason']),
                         ('missing', None, 'confirmed_payment_evidence_missing'))

    def test_real_cash_conflicts_and_incomplete_coverage_still_fail_closed(self):
        rows, payments = public_rows(), public_payments() + [public_warrant()]
        for proof in [{**coverage(rows), 'complete': False},
                      {**coverage(rows), 'rowCount': len(rows) + 1},
                      {**coverage(rows), 'scope': 'recent_report_dates'}]:
            with self.subTest(proof=proof):
                result = build(rows, payments, proof)
                self.assertEqual((result['status'], result['amount'], result['reason']),
                                 ('missing', None, 'confirmed_window_coverage_incomplete'))
        conflicting_rows = [{**rows[0], 'PRETAX_BONUS_RMB': 10}, rows[1]]
        result = build(conflicting_rows, payments)
        self.assertEqual((result['status'], result['amount'], result['reason']),
                         ('conflict', None, 'confirmed_amount_conflict'))
        no_payment = [{**payments[0], 'PAY_CASH_DATE': None}, *payments[1:]]
        self.assertIsNone(build(rows, no_payment)['amount'])
        second_version = {**payments[0], 'IMPL_PLAN_PROFILE': '10派10元'}
        result = build(rows, payments + [second_version])
        self.assertEqual((result['status'], result['amount'], result['reason']),
                         ('conflict', None, 'confirmed_payment_version_conflict'))

    def test_old_fiscal_cash_paid_inside_window_is_not_discarded_by_age(self):
        old_cash = {**public_rows()[0], 'REPORT_DATE': '2005-12-31',
                    'NOTICE_DATE': '2006-11-03', 'EQUITY_RECORD_DATE': '2006-11-08',
                    'EX_DIVIDEND_DATE': '2026-06-05', 'PRETAX_BONUS_RMB': 1,
                    'IMPL_PLAN_PROFILE': '10派1元(含税)'}
        late_paid = {**public_payments()[0], 'REPORT_DATE': '2005年报',
                     'NOTICE_DATE': '2006-11-03', 'EQUITY_RECORD_DATE': '2006-11-08',
                     'IMPL_PLAN_PROFILE': '10派1元'}
        result = build(public_rows() + [old_cash], public_payments() + [public_warrant(), late_paid])
        self.assertEqual((result['status'], result['amount']), ('ready', 1.48))
        self.assertIn({'reportDate': '2005-12-31', 'paymentDate': '2026-06-05',
                       'amount': 0.1, 'sourceUrl': 'https://data.eastmoney.com/yjfp/detail/600887.html',
                       'plan': '10派1元(含税)；公开分红表＋F10派息日核对'}, result['components'])

    def test_real_collector_retains_unfiltered_warrant_and_cash_rows(self):
        from datetime import datetime
        from urllib.parse import parse_qs, urlparse
        import personal_public_forward_sync as collector

        # Only the HTTP transport is replaced. Collector and normalizer are real.
        class PublicTransport:
            def __init__(self):
                self.calls = []

            def safe_curl_json(self, url):
                query = parse_qs(urlparse(url).query)
                self.calls.append(query)
                if query['reportName'] == ['RPT_SHAREBONUS_DET']:
                    records = public_rows()
                elif query['reportName'] == ['RPT_F10_DIVIDEND_MAIN']:
                    records = [{k: v for k, v in p.items() if k != 'sourceUrl'}
                               for p in public_payments() + [public_warrant()]]
                else:
                    raise AssertionError('Unexpected public request')
                return {'success': True, 'result': {'data': records, 'pages': 1, 'count': len(records)}}

            def fetch_notice_page(self, *args):
                raise AssertionError('Unnecessary notice scan')

        transport = PublicTransport()
        rows, docs, meta = collector.collect_confirmed(STOCKS, datetime.fromisoformat(ASOF),
                                                       public_adapter=transport)
        self.assertEqual(rows, public_rows())
        self.assertEqual(meta['paymentRows'], public_payments() + [public_warrant()])
        self.assertEqual(len(transport.calls), 2)
        for query in transport.calls:
            self.assertEqual(query['filter'], ['(SECURITY_CODE in ("600887"))'])
        self.assertEqual(meta['confirmedCoverage'], coverage(rows))
        result = cash.build_confirmed_records(rows, STOCKS, meta['asOf'],
                     coverage=meta['confirmedCoverage'], notices=docs, payment_rows=meta['paymentRows'])[0]
        self.assertEqual((result['status'], result['amount']), ('ready', 1.38))


if __name__ == '__main__':
    unittest.main()
