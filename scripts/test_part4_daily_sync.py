"""Offline daily orchestration tests; never read real credentials or run workers."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import part4_daily_sync as daily

OK = {"status": "ok", "coverageComplete": True, "published": True, "stored": 3}
NOTICE = ["scripts/part4_official_announcement_sync.py", "sync", "--include-structured-pre-disclosures"]
QUOTE = ["scripts/personal_market_snapshot_sync.py", "--publish"]


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.key = self.home / '.hermes/workspace/stock-dashboard-private-runtime/credentials/mx-apikey'
        self.key.parent.mkdir(parents=True, mode=0o700)
        self.key.write_text('synthetic-test-secret\n')
        self.key.chmod(0o600)
        self.enterContext(patch.object(Path, 'home', return_value=self.home))
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.child = self.enterContext(patch.object(daily.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(OK), '')))

    def test_private_credential_reaches_mx_child_only(self):
        daily.run_checked(NOTICE)
        self.assertEqual(self.child.call_args.kwargs.get('env', {}).get('MX_APIKEY'), 'synthetic-test-secret')
        self.assertNotIn('MX_APIKEY', os.environ)
        daily.run_checked(QUOTE)
        self.assertNotIn('MX_APIKEY', self.child.call_args.kwargs['env'])

    def test_unsafe_credentials_fail_closed_without_secret_output(self):
        for case in ('directory_mode', 'file_mode', 'file_symlink', 'directory_symlink', 'empty', 'missing', 'owner', 'directory_owner'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                key = home / '.hermes/workspace/stock-dashboard-private-runtime/credentials/mx-apikey'
                key.parent.mkdir(parents=True, mode=0o700)
                key.write_text('synthetic-test-secret')
                key.chmod(0o600)
                if case == 'directory_mode': key.parent.chmod(0o755)
                if case == 'file_mode': key.chmod(0o644)
                if case == 'empty': key.write_text(' \n')
                if case == 'missing': key.unlink()
                if case == 'file_symlink':
                    target = key.with_name('target'); key.rename(target); key.symlink_to(target)
                if case == 'directory_symlink':
                    target = key.parent.with_name('target'); key.parent.rename(target); key.parent.symlink_to(target, target_is_directory=True)
                real_fstat = os.fstat
                def fake_fstat(fd):
                    value = real_fstat(fd)
                    import stat
                    wrong = (case == 'owner' and stat.S_ISREG(value.st_mode)) or (case == 'directory_owner' and stat.S_ISDIR(value.st_mode))
                    if wrong:
                        fields = list(value); fields[4] = os.getuid() + 1
                        return os.stat_result(fields)
                    return value
                self.child.reset_mock()
                with patch.object(Path, 'home', return_value=home), patch.object(os, 'fstat', side_effect=fake_fstat):
                    with self.assertRaisesRegex(daily.DailySyncError, '^daily_mx_credential_invalid$'):
                        daily.run_checked(NOTICE)
                self.child.assert_not_called()

    def test_existing_environment_wins_even_with_missing_file(self):
        self.key.unlink()
        with patch.dict(os.environ, {'MX_APIKEY': 'existing-value'}):
            daily.run_checked(NOTICE)
        self.assertEqual(self.child.call_args.kwargs['env']['MX_APIKEY'], 'existing-value')

    def test_quote_does_not_read_missing_credential(self):
        self.key.unlink()
        self.assertEqual(daily.run_checked(QUOTE), OK)

    def run_sync(self, effects):
        output = io.StringIO()
        with patch.object(daily, 'load_state', return_value={'successfulDate': '2000-01-01'}), patch.object(daily, 'atomic_write') as write, patch.object(daily, 'run_checked', side_effect=effects) as run, contextlib.redirect_stdout(output):
            result = daily.sync_once(False)
        return result, json.loads(output.getvalue()), run, write

    def test_notice_failure_still_publishes_quotes_and_preserves_future(self):
        result, summary, run, write = self.run_sync([daily.DailySyncError('structured_dividend_corroboration_failed'), OK])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0], QUOTE)
        self.assertEqual(result, 2)
        self.assertEqual(summary['stages']['notices']['category'], 'structured_dividend_corroboration_failed')
        self.assertEqual(summary['stages']['quotes']['status'], 'ok')
        self.assertEqual(summary['stages']['future']['status'], 'skipped')
        self.assertEqual(summary['stages']['future']['category'], 'notice_dependency_failed_preserving')
        self.assertTrue(summary['quoteSucceeded'])
        write.assert_not_called()

    def test_each_stage_failure_isolated_including_timeouts_and_unexpected_errors(self):
        for index, name in enumerate(('notices', 'quotes', 'future')):
            for error, category in ((subprocess.TimeoutExpired('synthetic-test-secret', 600, stderr='synthetic-test-secret'), 'daily_child_timeout'), (OSError('synthetic-test-secret'), 'daily_child_execution_failed'), (ValueError('synthetic-test-secret'), 'daily_child_unexpected_error'), (daily.DailySyncError('coverage_failed'), 'coverage_failed')):
                with self.subTest(stage=name, category=category):
                    effects: list[object] = [dict(OK), dict(OK), dict(OK)]
                    effects[index] = error
                    result, summary, run, write = self.run_sync(effects)
                    self.assertEqual(result, 2)
                    self.assertEqual(summary['stages'][name], {'status': 'error', 'category': category})
                    self.assertEqual(run.call_count, 2 if index == 0 else 3)
                    self.assertEqual(summary['quoteSucceeded'], index != 1)
                    self.assertNotIn('synthetic-test-secret', json.dumps(summary))
                    self.assertFalse(summary['published'])
                    write.assert_not_called()

    def test_full_success_advances_state(self):
        result, summary, run, write = self.run_sync([OK, OK, OK])
        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 3)
        self.assertTrue(summary['quoteSucceeded'])
        self.assertTrue(summary['published'])
        self.assertEqual(json.loads(write.call_args.args[1])['successfulDate'], summary['date'])
        self.assertTrue(all(s == {'status': 'ok', 'category': 'complete'} for s in summary['stages'].values()))

    def test_missing_mx_credential_no_longer_blocks_default_official_quote_chain(self):
        self.key.unlink()
        output = io.StringIO()
        with patch.object(daily, 'load_state', return_value={}), patch.object(daily, 'atomic_write') as write, contextlib.redirect_stdout(output):
            result = daily.sync_once(False)
        summary = json.loads(output.getvalue())
        self.assertEqual(result, 2)
        self.assertTrue(summary['quoteSucceeded'])
        self.assertEqual(summary['stages']['notices']['status'], 'ok')
        self.assertEqual(summary['stages']['future']['category'], 'daily_mx_credential_invalid')
        self.assertEqual(self.child.call_count, 2)
        self.assertNotIn('--include-structured-pre-disclosures',self.child.call_args_list[0].args[0])
        write.assert_not_called()

    def test_invalid_json_shapes_are_sanitized(self):
        for stdout in ('[]', 'null', '42', '"synthetic-test-secret"', 'not-json'):
            with self.subTest(stdout=stdout):
                self.child.return_value = subprocess.CompletedProcess([], 0, stdout, '')
                with self.assertRaisesRegex(daily.DailySyncError, '^daily_child_response_invalid$'):
                    daily.run_checked(QUOTE)

    def test_coverage_and_publish_must_both_be_complete(self):
        for field in ('coverageComplete', 'published'):
            self.child.return_value = subprocess.CompletedProcess([], 0, json.dumps({**OK, field: False}), '')
            with self.assertRaisesRegex(daily.DailySyncError, '^daily_child_coverage_or_publish_incomplete$'):
                daily.run_checked(QUOTE)

    def test_child_error_category_never_exposes_injected_key(self):
        for category in ('synthetic-test-secret', 'error synthetic-test-secret', {'detail': 'synthetic-test-secret'}):
            self.child.return_value = subprocess.CompletedProcess([], 2, json.dumps({'status': 'error', 'category': category}), 'synthetic-test-secret')
            with self.assertRaisesRegex(daily.DailySyncError, '^daily_child_failed$'):
                daily.run_checked(NOTICE)

    def test_invalid_key_bytes_and_nonregular_file_are_rejected(self):
        for value in (b'\xff', b'abc\x00def'):
            self.key.write_bytes(value)
            with self.assertRaisesRegex(daily.DailySyncError, '^daily_mx_credential_invalid$'):
                daily.run_checked(NOTICE)
        self.key.unlink()
        os.mkfifo(self.key, 0o600)
        with self.assertRaisesRegex(daily.DailySyncError, '^daily_mx_credential_invalid$'):
            daily.run_checked(NOTICE)
        self.child.assert_not_called()

    def test_automatic_gates_do_not_run_children(self):
        from datetime import datetime
        for now, state, category in (
            (datetime(2026, 9, 12, 19, tzinfo=daily.BEIJING), {}, 'non_weekday'),
            (datetime(2026, 9, 9, 18, 4, tzinfo=daily.BEIJING), {}, 'not_due'),
            (datetime(2026, 9, 9, 19, tzinfo=daily.BEIJING), {'successfulDate': '2026-09-09'}, 'already_succeeded'),
        ):
            output = io.StringIO()
            with patch.object(daily, 'datetime') as clock, patch.object(daily, 'load_state', return_value=state), contextlib.redirect_stdout(output):
                clock.now.return_value = now
                self.assertEqual(daily.sync_once(True), 0)
            self.assertEqual(json.loads(output.getvalue())['category'], category)
        self.child.assert_not_called()

    def test_state_write_failure_returns_two_with_quote_success(self):
        output = io.StringIO()
        with patch.object(daily, 'load_state', return_value={}), patch.object(daily, 'atomic_write', side_effect=OSError('synthetic-test-secret')), patch.object(daily, 'run_checked', return_value=OK), contextlib.redirect_stdout(output):
            result = daily.sync_once(False)
        summary = json.loads(output.getvalue())
        self.assertEqual(result, 2)
        self.assertEqual(summary['status'], 'error')
        self.assertEqual(summary['category'], 'daily_state_write_failed')
        self.assertTrue(summary['quoteSucceeded'])
        self.assertNotIn('synthetic-test-secret', output.getvalue())


if __name__ == '__main__':
    unittest.main()
