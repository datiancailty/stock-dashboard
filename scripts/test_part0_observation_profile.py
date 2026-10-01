"""Explicit observation binding; no Hosted schema or trading capability changes."""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'ops'))
import part0_read_model as subject

class ObservationProfileTests(unittest.TestCase):
    def collect(self, profile='october_202610', **extra):
        calls = []
        def command(args):
            calls.append(args)
            if args[0] == 'journalctl':
                return ''
            name = args[2]
            if name.endswith('.timer'):
                return 'LoadState=loaded\nActiveState=active\nUnitFileState=enabled\nNextElapseUSecRealtime=Thu 2026-10-08 09:35:00 CST\n'
            return 'LoadState=loaded\nActiveState=inactive\nMainPID=0\nResult=success\n'
        with tempfile.TemporaryDirectory() as directory:
            daily = Path(directory)/'daily.json'
            daily.write_text('{}')
            cfg = dict(db_path='/synthetic/state.sqlite', daily_state=str(daily),
                       observation_profile=profile, **extra)
            with patch.object(subject, 'read_ledger', return_value=dict(
                    account=None, trades=[], activeSymbols=[],
                    strategyCycleAt=None, quoteAsOf=None)):
                result = subject.collect(cfg, now='2026-10-01T20:00:00+08:00', command=command)
        return result, calls

    def test_october_binds_only_the_approved_units_without_inventing_hosted_status(self):
        try:
            result, calls = self.collect()
        except ValueError as exc:
            self.fail(f'Approved October observation profile is not implemented: {exc}')
        self.assertIn(['systemctl', 'show', 'stock-sim-october-202610.timer'], [c[:3] for c in calls])
        self.assertIn(['systemctl', 'show', 'stock-sim-october-202610.service'], [c[:3] for c in calls])
        auth = result['runtime']['authorization']
        self.assertEqual(auth, dict(status='requires_review', expiresAt='2026-10-30T15:05:00+08:00', nextRunAt=None))
        journal = next(c for c in calls if c[0] == 'journalctl')
        self.assertIn('stock-sim-trial-20260929-30.service', journal)
        self.assertIn('stock-sim-october-202610.service', journal)
        self.assertTrue(all(c[:2] == ['systemctl', 'show'] or c[0] == 'journalctl' for c in calls))

    def test_arbitrary_unit_or_expiry_cannot_be_injected(self):
        for profile in ['other', 'stock-sim.service', '../new', None, True]:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                self.collect(profile)
        with self.assertRaises(ValueError):
            self.collect(authority_expires_at='2030-01-01T00:00:00+08:00')

    def test_publisher_sandbox_also_denies_the_october_control_directory(self):
        unit = (Path(__file__).resolve().parents[1]/'ops/systemd/stock-dashboard-part0-publish.service').read_text()
        denied = next(line for line in unit.splitlines() if line.startswith('InaccessiblePaths='))
        self.assertIn('-/var/lib/stock-sim-october-202610', denied)
        self.assertIn('-/var/lib/stock-sim-v31f-15m', denied)
        collector = (Path(__file__).resolve().parents[1]/'ops/systemd/stock-dashboard-part0-collect.service').read_text()
        self.assertIn('PrivateNetwork=true', collector)

    def test_old_success_is_history_not_an_october_cycle(self):
        try:
            result, events = subject.build_runtime(
                now='2026-10-01T20:00:00+08:00', expires='2026-10-30T15:05:00+08:00',
                units={}, daily={}, ledger=dict(activeSymbols=[], strategyCycleAt=None, quoteAsOf=None),
                journal=[{'_SYSTEMD_UNIT':'stock-sim-trial-20260929-30.service',
                          '__REALTIME_TIMESTAMP':'1790751002795039', 'MESSAGE':'{"ok":true}'}],
                active_unit='stock-sim-october-202610.service')
        except TypeError as exc:
            self.fail(f'Current versus historical unit provenance not implemented: {exc}')
        self.assertEqual(result['strategy']['status'], 'unknown')
        self.assertEqual(len(events), 1)

    def test_unknown_binding_is_rejected_before_reading_database(self):
        with patch.object(subject, 'read_ledger') as read:
            with self.assertRaises(ValueError):
                subject.collect(dict(db_path='/absent', daily_state='/absent', observation_profile='unknown'))
            read.assert_not_called()

class StrategyResultTests(unittest.TestCase):
    def build(self, results):
        unit = 'stock-sim-october-202610.service'
        journal = [dict(
            _SYSTEMD_UNIT=unit,
            __REALTIME_TIMESTAMP=str(int(datetime.fromisoformat(at).timestamp() * 1000000)),
            MESSAGE=json.dumps(result)) for at, result in results]
        return subject.build_runtime(
            now='2026-10-08T10:00:00+08:00', expires='2026-10-30T15:05:00+08:00',
            units={}, daily={}, journal=journal,
            ledger=dict(activeSymbols=[], strategyCycleAt=None, quoteAsOf=None),
            active_unit=unit)

    def test_real_success_retains_success_event_and_time(self):
        at = '2026-10-08T09:35:00+08:00'
        runtime, events = self.build([(at, dict(ok=True, halted=False, cap=0))])
        self.assertEqual(runtime['strategy'], dict(status='ok', asOf=at))
        self.assertEqual(events, [dict(at=at, kind='strategy', status='ok', code='cycle_succeeded')])

    def test_real_failure_retains_failure_event_and_time(self):
        at = '2026-10-08T09:35:00+08:00'
        runtime, events = self.build([(at, dict(ok=False, cap=0))])
        self.assertEqual(runtime['strategy'], dict(status='error', asOf=at))
        self.assertEqual(events, [dict(at=at, kind='strategy', status='error', code='cycle_failed')])

    def test_halted_ok_result_is_a_failed_cycle_not_a_success(self):
        at = '2026-10-08T09:35:00+08:00'
        runtime, events = self.build([(at, dict(ok=True, halted=True, cap=0))])
        self.assertEqual(runtime['strategy'], dict(status='error', asOf=at))
        self.assertEqual(events, [dict(at=at, kind='strategy', status='error', code='cycle_failed')])


    def test_duplicate_slot_does_not_replace_a_real_result_or_time(self):
        at = '2026-10-08T09:35:00+08:00'
        duplicate_at = '2026-10-08T09:35:10+08:00'
        for ok, status, code in [(False, 'error', 'cycle_failed'), (True, 'ok', 'cycle_succeeded')]:
            with self.subTest(ok=ok):
                runtime, events = self.build([
                    (at, dict(ok=ok)),
                    (duplicate_at, dict(ok=True, duplicate_slot=True, cap=0))])
                self.assertEqual(runtime['strategy'], dict(status=status, asOf=at))
                self.assertEqual(events, [dict(at=at, kind='strategy', status=status, code=code)])

    def test_only_duplicate_slots_leave_strategy_unknown_without_events(self):
        runtime, events = self.build([
            ('2026-10-08T09:35:00+08:00', dict(ok=True, duplicate_slot=True, cap=0)),
            ('2026-10-08T09:35:10+08:00', dict(ok=True, duplicate_slot=True, cap=0))])
        self.assertEqual(runtime['strategy'], dict(status='unknown', asOf=None))
        self.assertEqual(events, [])

    def test_outside_window_still_cannot_replace_a_real_failure(self):
        at = '2026-10-08T09:35:00+08:00'
        runtime, events = self.build([
            (at, dict(ok=False)),
            ('2026-10-08T09:35:10+08:00', dict(ok=True, outside_window=True, cap=0))])
        self.assertEqual(runtime['strategy'], dict(status='error', asOf=at))
        self.assertEqual(events, [dict(at=at, kind='strategy', status='error', code='cycle_failed')])


if __name__ == '__main__':
    unittest.main()
