"""Offline contract tests; fixtures are synthetic, not provider evidence."""
import importlib.util
from datetime import date, timedelta
from pathlib import Path
import statistics
import unittest


class DashboardTechnicalIndicatorsTests(unittest.TestCase):
    def module(self):
        path = Path(__file__).with_name('dashboard_technical_indicators.py')
        self.assertTrue(path.is_file(), 'reusable Dashboard BOLL module is missing')
        spec = importlib.util.spec_from_file_location('dashboard_technical_indicators', path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_weekly_matches_dashboard_sample_standard_deviation(self):
        mod = self.module()
        rows = [{'date': date(2025, 1, 3) + timedelta(weeks=i), 'close': i + 1.0} for i in range(20)]
        result = mod.compute_boll(rows, timeframe='week', as_of=rows[-1]['date'], adjustment='forward', include_current_period=True, expected_trade_dates=None)
        values = [row['close'] for row in rows]
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['middle'], round(statistics.mean(values), 3))
        self.assertEqual(result['upper'], round(statistics.mean(values) + 2 * statistics.stdev(values), 3))
        self.assertEqual(result['stddev'], 'sample')
        self.assertEqual(result['basis'], '前复权周K')
        self.assertEqual(result['sampleCount'], 20)


    def test_daily_validation_rejects_ambiguous_or_corrupt_history(self):
        mod = self.module()
        self.assertTrue(callable(getattr(mod, 'validate_daily_closes', None)), 'daily validation boundary missing')
        rows = [{'date': '2026-09-07', 'close': 10}, {'date': '2026-09-08', 'close': 11}]
        kwargs = {'as_of': '2026-09-08', 'expected_trade_dates': ['2026-09-07', '2026-09-08']}
        self.assertEqual(len(mod.validate_daily_closes(rows, **kwargs)), 2)
        cases = [(rows[::-1], kwargs), (rows + [rows[-1]], kwargs),
                 ([{'date': '2026-09-09', 'close': 1}], kwargs),
                 (rows[:1], kwargs),
                 ([{'date': '2026-09-06', 'close': 1}], kwargs)]
        for bad in (0, -1, float('nan'), float('inf'), True, None, '10'):
            cases.append(([{'date': '2026-09-07', 'close': bad}], {'as_of': '2026-09-08', 'expected_trade_dates': None}))
        for history, options in cases:
            with self.subTest(history=history), self.assertRaises(ValueError):
                mod.validate_daily_closes(history, **options)


    def test_day_week_month_warmup_and_current_period_are_explicit(self):
        mod = self.module()
        rows = [{'date': date(2024 + i // 12, i % 12 + 1, 10), 'close': i + 10.0} for i in range(21)]
        for timeframe in ('day', 'week', 'month'):
            with self.subTest(timeframe=timeframe):
                kw = dict(timeframe=timeframe, as_of=rows[-1]['date'], adjustment='forward', expected_trade_dates=[r['date'] for r in rows])
                included = mod.compute_boll(rows, include_current_period=True, **kw)
                excluded = mod.compute_boll(rows, include_current_period=False, **kw)
                self.assertEqual(included.get('timeframe'), timeframe)
                self.assertEqual(included['middle'], round(statistics.mean([r['close'] for r in rows[-20:]]), 3))
                self.assertEqual(excluded['middle'], round(statistics.mean([r['close'] for r in rows[-21:-1]]), 3))
                self.assertTrue(included['currentPeriodIncluded'])
                self.assertFalse(excluded['currentPeriodIncluded'])
                self.assertEqual(included['calendarValidation'], 'exact_match')
        short = mod.compute_boll(rows[:19], timeframe='month', as_of=rows[18]['date'], adjustment='none', include_current_period=True, expected_trade_dates=None)
        self.assertEqual(short['status'], 'insufficient_history')
        self.assertIsNone(short['upper'])
        self.assertEqual(short['sampleCount'], 19)
        self.assertEqual(short['basis'], '未复权月K')
        self.assertEqual(short['calendarValidation'], 'not_checked')
        self.assertFalse(short['dashboardForwardBasis'])


    def test_compute_gate_rejects_bad_options_and_mixed_adjustment(self):
        mod = self.module()
        rows = [{'date': '2026-09-08', 'close': 10, 'adjustment': 'none'}]
        kw = dict(timeframe='day', as_of='2026-09-08', adjustment='forward', include_current_period=True, expected_trade_dates=None)
        with self.assertRaises(ValueError):
            mod.compute_boll(rows, **kw)
        for key, value in [('timeframe', 'year'), ('adjustment', 'qfq'), ('include_current_period', 'yes')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                mod.compute_boll([], **{**kw, key: value})
        with self.assertRaises(ValueError):
            mod.compute_boll([{'date': '2026-09-09', 'close': 11}], **kw)


    def test_regression_parity_with_existing_dashboard_source(self):
        # Execute only the pure function AST: no requests/env/import side effects.
        import ast
        mod = self.module()
        source = Path(__file__).with_name('update_market.py').read_text()
        function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == 'weekly_boll_from_daily')
        scope: dict = {'statistics': statistics}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<existing weekly BOLL>', 'exec'), scope)
        start = date(2023, 10, 2)
        rows = [{'date': start + timedelta(days=i), 'close': 10.0 + (i % 29) / 10} for i in range(801) if (start + timedelta(days=i)).weekday() < 5]
        for count in (150, 240, len(rows)):
            history = rows[:count]
            result = mod.compute_boll(history, timeframe='week', as_of=history[-1]['date'], adjustment='forward', include_current_period=True, expected_trade_dates=None)
            old = scope['weekly_boll_from_daily'](history)
            self.assertEqual({key: result[key] for key in old}, old)

    def test_constant_empty_and_period_last_close_regressions(self):
        mod = self.module()
        start = date(2024, 10, 7)
        rows = []
        for i in range(20):
            rows.extend([{'date': start + timedelta(weeks=i), 'close': 99.0}, {'date': start + timedelta(weeks=i, days=4), 'close': 10.0}])
        kwargs = dict(timeframe='week', as_of=rows[-1]['date'], adjustment='forward', include_current_period=True, expected_trade_dates=None)
        result = mod.compute_boll(rows, **kwargs)
        self.assertEqual((result['lower'], result['middle'], result['upper']), (10.0, 10.0, 10.0))
        self.assertEqual(mod.compute_boll([], **kwargs)['status'], 'insufficient_history')
        self.assertIsNone(mod.compute_boll([], **kwargs)['asOf'])


if __name__ == '__main__':
    unittest.main()
