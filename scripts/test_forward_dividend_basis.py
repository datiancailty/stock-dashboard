"""Offline tests; fixtures are synthetic, not live dividend facts."""
import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any

PATH = Path(__file__).with_name('forward_dividend_basis.py')


def event(year, kind, amount, **kw) -> dict[str, Any]:
    return dict(code='600000', year=year, kind=kind, amount=amount,
                status=kw.pop('status', 'implemented'),
                amount_scope=kw.pop('amount_scope', 'distribution'),
                source={'dto_title': 'synthetic fixture', 'row': f'{year}-{kind}'}, **kw)


class ForwardBasisTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATH.exists(), 'pure forward dividend module is not implemented')
        spec = importlib.util.spec_from_file_location('forward_basis_under_test', PATH)
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_latest_interim_replaces_old_not_added_to_ttm(self):
        rows = [event(2025, 'interim', '.5'), event(2025, 'annual', '.88'),
                event(2026, 'interim', '.5', status='announced')]
        result = self.module.calculate_forward_basis(rows, codes=['600000'])['600000']
        self.assertEqual(result['amount'], Decimal('1.38'))
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['components']['interim']['year'], 2026)
        self.assertEqual(result['components']['interim']['status'], 'announced')
        self.assertEqual(len(result['audit']), 3)
        self.assertEqual(rows[0]['amount'], '.5')


    def test_unusable_components_are_explicit_not_zero_or_old_fallback(self):
        for status, amount, scope, expected in [
            ('announced', None, 'distribution', 'missing'),
            ('not_announced', '.5', 'distribution', 'negated'),
            ('cancelled', '.5', 'distribution', 'negated'),
            ('unknown', '.5', 'distribution', 'missing'),
            ('implemented', 'NaN', 'distribution', 'missing'),
            ('implemented', '-1', 'distribution', 'missing'),
            ('implemented', True, 'distribution', 'missing'),
            ('implemented', '.88', None, 'ambiguous'),
            ('implemented', '1.38', 'full_year', 'ambiguous'),
            ('no_distribution', '.5', 'distribution', 'conflict'),
        ]:
            with self.subTest(status=status, amount=amount, scope=scope):
                rows = [event(2024, 'annual', '.8'), event(2025, 'interim', '.5'),
                        event(2025, 'annual', amount, status=status, amount_scope=scope)]
                r = self.module.calculate_forward_basis(rows)['600000']
                self.assertIsNone(r['amount'])
                self.assertEqual(r['status'], expected)
                self.assertEqual(r['components']['annual']['year'], 2025)
                self.assertEqual(r['components']['annual']['raw']['amount'], amount)
        for rows in ([], [event(2025, 'annual', '.88')]):
            r = self.module.calculate_forward_basis(rows, codes=['600000'])['600000']
            self.assertEqual(r['status'], 'missing')
            self.assertIsNone(r['amount'])
        r = self.module.calculate_forward_basis([
            event(2025, 'annual', '.88'), event(2026, 'interim', None, status='no_distribution')])['600000']
        self.assertEqual(r['amount'], Decimal('.88'))
        self.assertEqual(r['components']['interim']['status'], 'no_distribution')


    def test_revisions_conflicts_and_stock_isolation(self):
        base = event(2025, 'annual', '.8', revision=1)
        revised = event(2025, 'annual', '.88', revision=2, status='announced')
        interim = event(2026, 'interim', '.5')
        other = dict(event(2025, 'annual', '9'), code='000001')
        for rows in ([base, revised, revised, interim, other], [other, interim, revised, base, revised]):
            r = self.module.calculate_forward_basis(rows)
            self.assertEqual(r['600000']['amount'], Decimal('1.38'))
            self.assertEqual(len(r['600000']['components']['annual']['evidence']), 2)
            self.assertIsNone(r['000001']['amount'])
        for overrides, expected in [({'revision': 2}, 'conflict'),
                                    ({'revision': None}, 'ambiguous')]:
            r = self.module.calculate_forward_basis([revised, dict(base, **overrides), interim])['600000']
            self.assertEqual(r['status'], expected)
            self.assertIsNone(r['amount'])
        for order in (False, True):
            rows = [event(2025, 'annual', '.8', published_at='2026-03-01'),
                    event(2025, 'annual', None, status='cancelled', published_at='2026-04-01'), interim]
            r = self.module.calculate_forward_basis(list(reversed(rows)) if order else rows)['600000']
            self.assertEqual(r['status'], 'negated')
            self.assertEqual(r['components']['annual']['raw']['published_at'], '2026-04-01')
        r = self.module.calculate_forward_basis([base, base, interim])['600000']
        self.assertEqual(r['amount'], Decimal('1.3'))
        r = self.module.calculate_forward_basis([
            event(2025, 'annual', '.8'), event(2025, 'annual', '.88'), interim])['600000']
        self.assertEqual(r['status'], 'ambiguous')


    def test_incomplete_identity_or_provenance_never_silently_uses_old_data(self):
        valid = [event(2025, 'annual', '.88'), event(2026, 'interim', '.5')]
        for changes in ({'year': None}, {'year': '2026'}, {'year': True},
                        {'kind': 'quarterly'}, {'source': None}, {'source': {}}):
            with self.subTest(changes=changes):
                r = self.module.calculate_forward_basis(valid + [dict(event(2026, 'annual', '1'), **changes)])['600000']
                self.assertEqual(r['status'], 'missing')
                self.assertIsNone(r['amount'])
                self.assertEqual(len(r['audit']), 3)
        for code in (None, '', 600000):
            with self.assertRaisesRegex(ValueError, 'code'):
                self.module.calculate_forward_basis([dict(valid[0], code=code)])
        r = self.module.calculate_forward_basis([
            event(2025, 'annual', None, status='no_distribution', amount_scope='full_year'), valid[1]])['600000']
        self.assertEqual(r['status'], 'ambiguous')


    def test_date_precision_and_timezones_fail_closed(self):
        interim = event(2026, 'interim', '.5')
        for first, second, expected in [
            ('2026-04-01T08:00:00+08:00', '2026-04-01T00:00:00Z', 'conflict'),
            ('2026-04-01T08:00:00+08:00', '2026-04-01T01:00:00Z', 'ready'),
            ('2026-04-01', '2026-04-01T01:00:00Z', 'ambiguous'),
            ('2026-04-01T00:00:00', '2026-04-02T00:00:00', 'ambiguous'),
            ('bad-date', '2026-04-01', 'ambiguous'),
            ('2026-02-30', '2026-04-01', 'ambiguous'),
        ]:
            with self.subTest(first=first, second=second):
                r = self.module.calculate_forward_basis([
                    event(2025, 'annual', '.8', published_at=first),
                    event(2025, 'annual', '.88', published_at=second), interim])['600000']
                self.assertEqual(r['status'], expected)
                self.assertEqual(r['amount'], Decimal('1.38') if expected == 'ready' else None)

    def test_latest_fiscal_year_not_latest_payment_or_announcement_date(self):
        r = self.module.calculate_forward_basis([
            event(2024, 'annual', '9', published_at='2026-05-01'),
            event(2025, 'annual', '.88', published_at='2026-03-01'),
            event(2025, 'interim', '.5')])['600000']
        self.assertEqual(r['amount'], Decimal('1.38'))
        self.assertEqual(r['components']['annual']['year'], 2025)

    def test_all_sources_and_input_snapshot_are_retained(self):
        from copy import deepcopy
        rows = [event(2025, 'annual', '.88'), event(2026, 'interim', '.5')]
        rows[0]['source'] = {'dto': {'table': {'headName': ['2025年度分配'],
                            '分红方案': ['10派8.8元']}}, 'row_index': 0,
                            'scope_evidence': '本次年度分配，不含已派中期'}
        original = deepcopy(rows)
        r = self.module.calculate_forward_basis(iter(rows))['600000']
        self.assertEqual(r['components']['annual']['source'], rows[0]['source'])
        self.assertEqual(r['components']['annual']['evidence'], [rows[0]])
        r['components']['annual']['source']['dto']['table']['headName'][0] = 'changed'
        r['audit'][0]['source']['row_index'] = 99
        self.assertEqual(rows, original)

    def test_explicit_zero_is_not_missing(self):
        r = self.module.calculate_forward_basis([
            event(2025, 'annual', '0', status='no_distribution'),
            event(2026, 'interim', None, status='no_distribution')])['600000']
        self.assertEqual(r['status'], 'ready')
        self.assertEqual(r['amount'], Decimal(0))
        self.assertEqual(self.module.calculate_forward_basis([], codes=['000001'])['000001']['status'], 'missing')

    def test_unrecognized_raw_status_never_substring_matches_implemented(self):
        for status in ('未实施', '未公告', '未通过', '不实施', '未分配', '预披露', 'not implemented'):
            with self.subTest(status=status):
                r = self.module.calculate_forward_basis([
                    event(2025, 'annual', '.88'), event(2026, 'interim', '.5', status=status)])['600000']
                self.assertEqual(r['status'], 'missing')
                self.assertIsNone(r['amount'])
                self.assertEqual(r['components']['interim']['raw']['status'], status)


if __name__ == '__main__':
    unittest.main()
