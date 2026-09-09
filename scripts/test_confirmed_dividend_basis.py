"""Synthetic payment evidence; tests never access network/private state."""
import copy
import importlib
import unittest
from pathlib import Path

ASOF = '2026-09-10T00:00:00+08:00'
STOCKS = [{'code': '600000', 'name': '合成甲'}]
SOURCE = 'eastmoney_public_implemented_a_share'


def row(report='2025-12-31', payment='2026-07-10', amount='4.7881', notice='2026-07-03'):
    return dict(SECURITY_CODE='600000', SECUCODE='600000.SH', REPORT_DATE=report,
                NOTICE_DATE=notice, ASSIGN_PROGRESS='实施分配',
                IMPL_PLAN_PROFILE=f'10派{amount}元(含税)', PRETAX_BONUS_RMB=amount,
                EX_DIVIDEND_DATE=payment)


def notice(report='2025-12-31', payment='2026-07-10', amount='0.47881', published='2026-07-03'):
    year, period = report[:4], {'12-31': '末期', '06-30': '中期'}[report[5:]]
    aid = 'AN' + published.replace('-', '') + '00000001'
    title = f'合成甲:{year}年A股{period}股息分派实施公告'
    text = (f'证券代码：600000。{title}。重要内容提示：每股分配比例：'
            f'A股每股现金红利人民币{amount}元（含税）。相关日期：'
            '股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日\n'
            f'Ａ股 {payment} － {payment} {payment}\n差异化分红送转：否。'
            '一、通过方案的董事会。二、分配方案：本公司港股股东的分红派息事宜不适用本公告。')
    return {'code': '600000', 'id': aid, 'date': published, 'title': title,
            'sourceUrl': f'https://data.eastmoney.com/notices/detail/600000/{aid}.html',
            'pages': [text], 'pageCount': 1}


def coverage(rows, asof=ASOF):
    return {'complete': True, 'scope': 'all_distribution_history', 'source': SOURCE,
            'asOf': asof, 'codes': ['600000'], 'rowCount': len(rows)}


class ConfirmedBasisTests(unittest.TestCase):
    def build(self, rows, docs, asof=ASOF, proof=True, payments=None):
        path = Path(__file__).with_name('confirmed_dividend_basis.py')
        self.assertTrue(path.exists(), 'confirmed dividend normalizer not implemented')
        mod = importlib.import_module('confirmed_dividend_basis')
        self.assertTrue(callable(getattr(mod, 'build_confirmed_records', None)))
        return mod.build_confirmed_records(rows, STOCKS, asof,
                    coverage=coverage(rows, asof) if proof else None, notices=docs,
                    **({'payment_rows': payments} if payments is not None else {}))[0]

    def test_unproven_coverage_is_unknown_not_zero_or_partial_total(self):
        for rows, docs in [([], []), ([row()], [notice()])]:
            with self.subTest(rows=len(rows)):
                result = self.build(rows, docs, proof=False)
                self.assertEqual((result['status'], result['amount']), ('missing', None))
                self.assertEqual(result['reason'], 'confirmed_window_coverage_incomplete')
        import confirmed_dividend_basis as mod
        for changes in [{'rowCount': 2}, {'codes': []}, {'complete': 1},
                        {'scope': 'recent_report_dates'}, {'asOf': '2026-09-09T00:00:00+08:00'}]:
            with self.subTest(changes=changes):
                proof = {**coverage([row()]), **changes}
                result = mod.build_confirmed_records([row()], STOCKS, ASOF, coverage=proof, notices=[notice()])[0]
                self.assertIsNone(result['amount'])
        zero = self.build([], [])
        self.assertEqual((zero['status'], zero['amount']), ('ready', 0))
        self.assertIsNone(zero['reason']) # Installed RPC requires ready.reason=null.
        zero_event = self.build([row(amount='0')], [notice(amount='0')])
        self.assertEqual(zero_event['components'], [])
        self.assertEqual((zero_event['status'], zero_event['amount'], zero_event['reason']), ('ready', 0, None))

    def test_payment_evidence_not_ex_date_or_provider_stage_proves_payment(self):
        for docs in [[], [{**notice(), 'pages': ['证券代码：600000。仅除权除息日2026-07-10']}]]:
            with self.subTest(docs=len(docs)):
                try:
                    result = self.build([row()], docs)
                except (StopIteration, TypeError) as error:
                    self.fail(f'unverified payment must become missing: {type(error).__name__}')
                self.assertEqual((result['status'], result['amount']), ('missing', None))
        future = row(payment='2026-10-10')
        result = self.build([future], [notice(payment='2026-10-10')])
        self.assertEqual((result['status'], result['amount']), ('ready', 0))
        pending = {**row(), 'ASSIGN_PROGRESS': '董事会决议通过'}
        self.assertEqual(self.build([pending], [])['amount'], 0)
        for amount in ['NaN', '-1', '4.7881000000001', True]:
            with self.subTest(amount=amount):
                result = self.build([{**row(), 'PRETAX_BONUS_RMB': amount}], [notice()])
                self.assertEqual((result['status'], result['amount']), ('conflict', None))
        for change in [{'IMPL_PLAN_PROFILE': '10派5元(含税)'},
                       {'PRETAX_BONUS_RMB': '5', 'IMPL_PLAN_PROFILE': '10派5元(含税)'},
                       {'IMPL_PLAN_PROFILE': '10派4.7881港元(含税)'}]:
            with self.subTest(change=change):
                result = self.build([{**row(), **change}], [notice()])
                self.assertEqual((result['status'], result['amount']), ('conflict', None))

    def test_calendar_window_exclusive_start_inclusive_end_and_bj_clock(self):
        for asof, start in [('2024-02-29T01:00:00+08:00', '2023-02-28'),
                            ('2025-02-28T01:00:00+08:00', '2024-02-28'),
                            ('2026-09-09T16:00:00Z', '2025-09-10')]:
            with self.subTest(asof=asof):
                try:
                    result = self.build([], [], asof)
                except ValueError as error:
                    self.fail(f'calendar-day window must clamp leap day: {error}')
                self.assertEqual(result['windowStart'], start)
                self.assertTrue(result['asOf'].endswith('+08:00'))
        for paid, expected in [('2025-09-10', 0), ('2025-09-11', .47881),
                               ('2026-09-10', .47881), ('2026-09-11', 0)]:
            with self.subTest(payment=paid):
                result = self.build([row(report='2024-12-31', payment=paid, notice='2025-09-01')],
                                    [notice(report='2024-12-31', payment=paid, published='2025-09-01')])
                self.assertEqual(result['amount'], expected)
        for stamp in ['2026-09-10T00:00:00', 'not-a-date']:
            with self.subTest(asof=stamp), self.assertRaisesRegex(ValueError, 'confirmed_as_of_invalid'):
                self.build([], [], stamp)

    def test_notice_must_bind_a_share_identity_period_complete_pages_and_dates(self):
        base = notice()
        for change in [{'sourceUrl': 'https://evil.invalid/doc'}, {'id': 'ANinvalid'},
                       {'pageCount': 2}, {'title': base['title'].replace('2025年', '2024年')},
                       {'pages': [base['pages'][0].replace('证券代码：600000', '证券代码：000002')]},
                       {'pages': [base['pages'][0].replace('A股每股', 'H股每股')]},
                       {'title': '合成甲H股公告:2025年股息分派实施公告'},
                       {'pages': [base['pages'][0].replace('末期', '中期')]},
                       {'pages': [base['pages'][0] + '证券代码：000002。']},
                       {'pages': [base['pages'][0].replace('2026-07-10', '2026-02-30')]}]:
            with self.subTest(change=change):
                result = self.build([row()], [{**base, **change}])
                self.assertIsNone(result['amount'])
        for change in [{'SECUCODE': '600000.HK'}, {'EX_DIVIDEND_DATE': '2026-07-09'},
                       {'REPORT_DATE': '2027-12-31'}, {'NOTICE_DATE': '2026-10-10'}]:
            with self.subTest(row=change):
                self.assertIsNone(self.build([{**row(), **change}], [base])['amount'])
        # An older missing payment is not assumed outside the window by fiscal year.
        self.assertIsNone(self.build([row(report='2019-12-31')], [])['amount'])
        mixed = copy.deepcopy(base)
        mixed['pages'][0] += '公司以港元计值，股息每股0.55港元；A股每股现金红利人民币0.47881元（含税）。'
        self.assertEqual(self.build([row()], [mixed])['amount'], .47881)

    def test_duplicate_versions_do_not_double_count_and_conflicts_fail_closed(self):
        self.assertEqual(self.build([row(), row()], [notice(), notice()])['amount'], .47881)
        other = row(report='2025-06-30', payment='2025-10-17', amount='6.6612', notice='2025-10-10')
        doc = notice(report='2025-06-30', payment='2025-10-17', amount='.66612', published='2025-10-10')
        result = self.build([row(), other], [notice(), doc])
        self.assertEqual(result['amount'], 1.14493)
        self.assertEqual([c['paymentDate'] for c in result['components']], ['2025-10-17', '2026-07-10'])
        for rows, docs in [([row(), row(payment='2026-07-11', notice='2026-07-04')],
                            [notice(), notice(payment='2026-07-11', published='2026-07-04')]),
                           ([row()], [notice(), notice(amount='.5')])]:
            with self.subTest(versions=len(rows)):
                result = self.build(rows, docs)
                self.assertEqual((result['status'], result['amount']), ('conflict', None))

    def test_bare_yuan_header_requires_explicit_a_share_rmb_not_computed_fx(self):
        doc = notice()
        doc['pages'][0] = doc['pages'][0].replace('每股现金红利人民币', '每股现金红利')
        clause = ('公司股息每股0.55港元；其中A股股息将以人民币支付，折算汇率以宣派日前一周中间价计算，'
                  '金额为每股人民币0.47881元（含税，保留小数点后五位）。')
        doc['pages'][0] += clause
        self.assertEqual(self.build([row()], [doc])['amount'], .47881)
        bad = copy.deepcopy(doc)
        bad['pages'][0] = bad['pages'][0].replace('金额为每股人民币0.47881元', '金额为每股人民币0.48元')
        self.assertIsNone(self.build([row()], [bad])['amount'])

    def test_index_notice_omitted_from_table_cannot_produce_false_zero(self):
        self.assertIsNone(self.build([], [notice()])['amount'])
        old = notice(report='2024-12-31', payment='2025-07-11', published='2025-07-04')
        self.assertEqual(self.build([], [old])['amount'], 0)
        unreadable = copy.deepcopy(old)
        unreadable['pages'][0] = unreadable['pages'][0].replace('现金红利发放日', '现金红利日期不明')
        self.assertIsNone(self.build([], [unreadable])['amount'])
        # A previous fiscal period paid late still belongs to the current window.
        delayed = notice(report='2024-12-31', payment='2026-07-11', published='2025-07-04')
        self.assertIsNone(self.build([], [delayed])['amount'])

    def test_f10_explicit_payment_excludes_old_rows_without_fetching_old_text(self):
        old = row(report='1999-12-31', payment='2000-07-10', notice='2000-07-03')
        def payment(r):
            return {k: r[k] for k in ['SECUCODE', 'SECURITY_CODE', 'NOTICE_DATE', 'EX_DIVIDEND_DATE', 'IMPL_PLAN_PROFILE']} | {
                'ASSIGN_PROGRESS': '实施方案', 'PAY_CASH_DATE': r['EX_DIVIDEND_DATE'],
                'sourceUrl': 'https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?type=web&code=SH600000'}
        payments = [payment(old), payment(row())]
        try:
            result = self.build([old, row()], [notice()], payments=payments)
        except TypeError as error:
            self.fail(f'explicit F10 payment evidence adapter missing: {error}')
        self.assertEqual(result['amount'], .47881)
        self.assertEqual(len(result['components']), 1)
        result = self.build([row()], [], payments=[payment(row())])
        self.assertEqual(result['amount'], .47881)
        self.assertEqual(result['components'][0]['paymentDate'], '2026-07-10')
        self.assertEqual(result['components'][0]['sourceUrl'], 'https://data.eastmoney.com/yjfp/detail/600000.html')
        self.assertEqual(result['components'][0]['plan'], '10派4.7881元(含税)；公开分红表＋F10派息日核对')
        for field, value in [('SECUCODE', '600000.HK'), ('PAY_CASH_DATE', '2026-07-01'),
                             ('IMPL_PLAN_PROFILE', '10派9元'), ('sourceUrl', 'https://evil.invalid/')]:
            with self.subTest(field=field):
                bad = {**payment(row()), field: value}
                self.assertIsNone(self.build([row()], [], payments=[bad])['amount'])
        no_payment = {**payment(old), 'PAY_CASH_DATE': None}
        self.assertIsNone(self.build([old, row()], [notice()], payments=[no_payment, payment(row())])['amount'])

    def cypc_fixture(self):
        rows = [row(amount='2.1'), row(report='2025-06-30', payment='2025-10-17',
                                    amount='7.9', notice='2025-10-10')]
        rows = [{**r, 'SECURITY_CODE': '600900', 'SECUCODE': '600900.SH'} for r in rows]
        payments = [{**r, 'ASSIGN_PROGRESS': '实施方案', 'PAY_CASH_DATE': r['EX_DIVIDEND_DATE'],
                     'sourceUrl': 'https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?type=web&code=SH600900'} for r in rows]
        warrant: dict = dict(SECURITY_CODE='600900', SECUCODE='600900.SH',
                       sourceUrl=payments[0]['sourceUrl'], NOTICE_DATE='2006-05-16 00:00:00',
                       EQUITY_RECORD_DATE='2006-05-17 00:00:00', REPORT_DATE='2005其他分配',
                       ASSIGN_PROGRESS='实施方案', IMPL_PLAN_PROFILE=None, PAY_CASH_DATE=None,
                       EX_DIVIDEND_DATE=None, IS_PAYCASH='0')
        return rows, payments, warrant

    def build_cypc(self, rows, payments):
        import confirmed_dividend_basis as mod
        return mod.build_confirmed_records(rows, [{'code': '600900'}], ASOF,
                    coverage={**coverage(rows), 'codes': ['600900']}, payment_rows=payments)[0]

    def test_verified_warrant_f10_event_does_not_block_proven_cash(self):
        rows, payments, warrant = self.cypc_fixture()
        baseline = self.build_cypc(rows, payments)
        self.assertEqual((baseline['status'], baseline['amount']), ('ready', 1.0))
        before = copy.deepcopy((rows, payments, warrant))
        result = self.build_cypc(rows, payments + [warrant])
        self.assertEqual((result['status'], result['amount']), ('ready', 1.0))
        self.assertEqual(result['components'], baseline['components'])
        self.assertEqual((rows, payments, warrant), before)

    def test_warrant_classification_requires_every_identity_and_empty_cash_field(self):
        import confirmed_dividend_basis as mod
        rows, payments, warrant = self.cypc_fixture()
        for empty in (None, ''):
            good = {**warrant, 'NOTICE_DATE': '2006-05-16', 'EQUITY_RECORD_DATE': '2006-05-17',
                    **{f: empty for f in ('IMPL_PLAN_PROFILE', 'PAY_CASH_DATE', 'EX_DIVIDEND_DATE')}}
            self.assertTrue(mod._verified_noncash_f10(good, '600900'))
            self.assertEqual(self.build_cypc(rows, payments + [good])['amount'], 1.0)
        changes = [
            {'SECURITY_CODE': '600000'}, {'SECUCODE': '600000.SH'}, {'SECUCODE': '600900.SZ'},
            {'sourceUrl': 'https://evil.invalid/'},
            {'sourceUrl': warrant['sourceUrl'].replace('SH600900', 'SH600000')},
            {'sourceUrl': warrant['sourceUrl'] + '&extra=1'},
            {'NOTICE_DATE': '2006-05-15'}, {'NOTICE_DATE': 'bad-date'},
            {'EQUITY_RECORD_DATE': '2006-05-18'}, {'EQUITY_RECORD_DATE': None},
            {'REPORT_DATE': '2005-12-31'}, {'ASSIGN_PROGRESS': '实施分配'},
            {'IS_PAYCASH': '1'}, {'IS_PAYCASH': 0}, {'IS_PAYCASH': None},
            {'IMPL_PLAN_PROFILE': '10派1元'}, {'IMPL_PLAN_PROFILE': ' '},
            {'PAY_CASH_DATE': '2026-07-10'}, {'PAY_CASH_DATE': '2006-05-17'},
            {'EX_DIVIDEND_DATE': '2006-05-17'},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(mod._verified_noncash_f10({**warrant, **change}, '600900'))
        for field in warrant:
            with self.subTest(missing=field):
                self.assertFalse(mod._verified_noncash_f10({k: v for k, v in warrant.items() if k != field}, '600900'))
        self.assertFalse(mod._verified_noncash_f10(warrant, '600000'))
        with self.assertRaises(TypeError):
            mod.NONCASH_F10_EVENTS[0]['SECURITY_CODE'] = '600000'  # pyright: ignore[reportIndexIssue]

    def test_warrant_near_matches_and_other_stocks_still_fail_closed(self):
        rows, payments, warrant = self.cypc_fixture()
        for change in [
                {'IMPL_PLAN_PROFILE': '10派1元'}, {'PAY_CASH_DATE': '2026-07-10'},
                {'EX_DIVIDEND_DATE': '2006-05-17'}, {'SECUCODE': '600000.SH'},
                {'sourceUrl': 'https://evil.invalid/'}, {'NOTICE_DATE': '2006-05-15'},
                {'EQUITY_RECORD_DATE': '2006-05-18'}, {'REPORT_DATE': '2005年度分配'},
                {'IS_PAYCASH': '1'}]:
            with self.subTest(change=change):
                self.assertIsNone(self.build_cypc(rows, payments + [{**warrant, **change}])['amount'])
        other = {**warrant, 'SECURITY_CODE': '600000', 'SECUCODE': '600000.SH',
                 'sourceUrl': warrant['sourceUrl'].replace('SH600900', 'SH600000')}
        self.assertIsNone(self.build([row()], [notice()], payments=[other])['amount'])
        with self.assertRaisesRegex(ValueError, 'confirmed_public_identity_invalid'):
            self.build_cypc(rows, payments + [other])

    def test_warrant_never_excludes_same_day_positive_rpt_or_cash_conflicts(self):
        rows, payments, warrant = self.cypc_fixture()
        old_cash = {**row(report='2005-12-31', payment='2006-05-17', amount='1', notice='2006-05-16'),
                    'SECURITY_CODE': '600900', 'SECUCODE': '600900.SH'}
        result = self.build_cypc(rows + [old_cash], payments + [warrant])
        self.assertEqual((result['status'], result['amount'], result['reason']),
                         ('missing', None, 'confirmed_payment_evidence_missing'))
        conflict = self.build_cypc([{**rows[0], 'PRETAX_BONUS_RMB': '3'}, rows[1]], payments + [warrant])
        self.assertEqual((conflict['status'], conflict['amount'], conflict['reason']),
                         ('conflict', None, 'confirmed_amount_conflict'))

    def test_input_identity_and_finite_total_contract_fail_closed(self):
        import confirmed_dividend_basis as mod
        for stocks in [[], STOCKS * 2, [{'code': '00883'}], [{'code': True}]]:
            with self.subTest(stocks=stocks), self.assertRaisesRegex(ValueError, 'confirmed_watchlist_invalid'):
                mod.build_confirmed_records([], stocks, ASOF)
        with self.assertRaisesRegex(ValueError, 'confirmed_public_identity_invalid'):
            mod.build_confirmed_records([{**row(), 'SECURITY_CODE': '000002'}], STOCKS, ASOF, coverage=coverage([row()]))
        large = row(amount='5000000')
        other = row(report='2025-06-30', payment='2025-10-17', amount='5000000', notice='2025-10-10')
        docs = [notice(amount='500000'), notice(report='2025-06-30', payment='2025-10-17', amount='500000', published='2025-10-10')]
        result = self.build([large, other], docs)
        self.assertEqual((result['status'], result['amount']), ('conflict', None))

    def test_contradictory_payment_tables_or_rmb_claims_do_not_select_first(self):
        base = notice()
        for extra in ['A股每股现金红利人民币0.5元（含税）。',
                      '股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日 A股 2026-07-10 - 2026-07-10 2026-07-11']:
            with self.subTest(extra=extra):
                doc = {**base, 'pages': [base['pages'][0] + extra]}
                result = self.build([row()], [doc])
                self.assertEqual((result['status'], result['amount']), ('conflict', None))
        unknown = {**row(), 'ASSIGN_PROGRESS': '上游新状态'}
        self.assertEqual((self.build([unknown], [])['status'], self.build([unknown], [])['amount']), ('missing', None))

    def test_stock_only_distributions_are_not_missing_cash_payments(self):
        old = row(report='1996-12-31', payment='1997-07-10', notice='1997-07-03')
        for plan in ['10转10.00', '10送3.00', '10送3.00转3.00']:
            with self.subTest(plan=plan):
                item = {**old, 'IMPL_PLAN_PROFILE': plan, 'PRETAX_BONUS_RMB': None}
                payment = {**item, 'IMPL_PLAN_PROFILE': plan, 'ASSIGN_PROGRESS': '实施方案', 'PAY_CASH_DATE': None}
                self.assertEqual(self.build([item, row()], [notice()], payments=[payment])['amount'], .47881)
        self.assertIsNone(self.build([{**old, 'IMPL_PLAN_PROFILE': '10转3派1元', 'PRETAX_BONUS_RMB': '1'}], [])['amount'])

    def test_all_old_explicit_payment_dates_exclude_same_day_special_versions(self):
        old = row(report='2005-12-31', payment='2006-04-10', notice='2006-03-31')
        base = {**old, 'ASSIGN_PROGRESS': '实施方案', 'PAY_CASH_DATE': '2006-04-10',
                'sourceUrl': 'https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?type=web&code=SH600000'}
        other = {**base, 'IMPL_PLAN_PROFILE': '10派17.14元'}
        self.assertEqual(self.build([old, row()], [notice()], payments=[base, other])['amount'], .47881)

    def test_paid_a_share_cash_uses_exact_per_ten_amount(self):
        rows = [row()]
        docs = [notice()]
        before = copy.deepcopy((rows, docs))
        result = self.build(rows, docs)
        self.assertEqual((result['status'], result['amount']), ('ready', 0.47881))
        self.assertEqual(result['source'], SOURCE)
        self.assertEqual(result['windowStart'], '2025-09-10')
        self.assertEqual(result['windowEnd'], '2026-09-10')
        self.assertEqual(result['components'], [{'reportDate': '2025-12-31',
                         'paymentDate': '2026-07-10', 'amount': .47881,
                         'sourceUrl': docs[0]['sourceUrl'], 'plan': rows[0]['IMPL_PLAN_PROFILE']}])
        self.assertEqual((rows, docs), before)


if __name__ == '__main__':
    unittest.main()
