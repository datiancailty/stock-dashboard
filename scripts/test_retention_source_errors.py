"""R1/R2 collector-boundary regressions; all HTTP/process calls are synthetic."""
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import personal_dividend_refresh_sync as m
import part4_official_announcement_sync as transport
import test_dividend_retention as retention
from test_dividend_retention import NOW, OLD
from test_public_forward_basis import row
from test_personal_public_forward_sync import offline_collect, index_note, index_page, content_page


class SourceClassificationTests(unittest.TestCase):
    def adapter(self):
        return retention.RetentionTests().adapter()

    def assert_rejected(self, **collectors):
        a = self.adapter()
        with self.assertRaises((ValueError, RuntimeError)):
            m.sync(adapter=a, now=NOW, publish=True, retain_previous=True, **collectors)
        a.private_rpc.assert_not_called()
        a.part4_writer_secret.assert_not_called()

    def test_real_confirmed_collector_wrong_issuer_is_not_retained(self):
        foreign = row(); foreign.update(SECURITY_CODE='600001', SECUCODE='600001.SH')
        public = SimpleNamespace(safe_curl_json=Mock(return_value={
            'success': True, 'result': {'data': [foreign], 'pages': 1, 'count': 1}}))
        self.assert_rejected(collect_forward=Mock(return_value=([row()], [], {'coverageComplete': True})),
                             collect_actual=lambda stocks, now: m.collector.collect_confirmed(stocks, now, public_adapter=public))

    def test_real_forward_collector_invalid_notice_date_is_not_retained(self):
        bad = index_note(); bad['notice_date'] = 'not-a-date'
        self.assert_rejected(collect_forward=lambda stocks, now: offline_collect(
            m.collector, indexes=[index_page([bad])], now=now.isoformat())[0])

    def test_real_forward_collector_wrong_index_issuer_is_not_retained(self):
        bad = index_note(); bad['codes'] = [{'stock_code': '600001', 'short_name': '合成乙'}]
        self.assert_rejected(collect_forward=lambda stocks, now: offline_collect(
            m.collector, indexes=[index_page([bad])], now=now.isoformat())[0])

    def test_real_mixed_coverage_or_notice_errors_cannot_hide_identity_conflict(self):
        foreign = row(); foreign.update(SECURITY_CODE='600001', SECUCODE='600001.SH')
        for kwargs in ({'table': [foreign]},
                       {'contents': [content_page(security=[{'stock': '600001'}])]},
                       {'contents': [content_page(notice_title='合成乙:2026年半年度报告摘要')]}):
            with self.subTest(kwargs=kwargs):
                self.assert_rejected(collect_forward=lambda stocks, now: offline_collect(
                    m.collector, now=now.isoformat(), **kwargs)[0])

    def test_unreviewed_semantics_and_mixed_categories_are_fail_closed(self):
        errors = ('forward_basis_public_future_period', 'forward_basis_public_notice_future',
                  'forward_basis_public_date_invalid', 'forward_basis_public_duplicate_row',
                  'forward_basis_public_index_invalid', 'forward_basis_public_index_order_invalid',
                  'forward_basis_public_index_repeated', 'forward_basis_public_notice_invalid',
                  'forward_basis_public_notice_pages_invalid', 'forward_basis_public_notice_page_repeated',
                  'forward_basis_public_coverage_incomplete', 'forward_basis_public_unreviewed_failure',
                  'confirmed_public_duplicate_row', 'confirmed_public_payment_identity_invalid',
                  'confirmed_public_payment_duplicate_row', 'confirmed_public_index_identity_invalid',
                  'confirmed_public_index_order_invalid', 'confirmed_public_notice_incomplete',
                  'confirmed_public_unreviewed_failure', 'forward_basis_evidence_path_invalid')
        for category in errors:
            with self.subTest(category=category):
                self.assert_rejected(collect_forward=Mock(side_effect=ValueError(category)))
        self.assertIsNone(m.source_failure(RuntimeError('forward_basis_public_provider_failed')))

    def test_reviewed_response_coverage_json_and_timeouts_still_retain(self):
        categories = ('forward_basis_public_provider_failed', 'forward_basis_public_pagination_invalid',
                      'forward_basis_public_page_incomplete', 'forward_basis_public_index_metadata_invalid',
                      'forward_basis_public_index_page_incomplete', 'forward_basis_public_report_coverage_incomplete',
                      'forward_basis_public_annual_coverage_incomplete', 'confirmed_public_provider_failed',
                      'confirmed_public_pagination_incomplete', 'confirmed_public_payment_source_invalid',
                      'confirmed_public_payment_pagination_incomplete', 'confirmed_public_index_incomplete',
                      'confirmed_public_index_window_incomplete', 'dividend_forward_scan_incomplete',
                      'dividend_actual_scan_incomplete')
        errors = [ValueError(category) for category in categories]
        errors += [json.JSONDecodeError('synthetic response', '', 0), TimeoutError(),
                   subprocess.TimeoutExpired('synthetic', 1)]
        errors += [transport.SyncError(category) for category in (
            'official_notice_transport_failed', 'official_notice_response_failed', 'official_notice_response_shape_invalid')]
        for error in errors:
            with self.subTest(error=type(error).__name__, category=str(error)):
                a = self.adapter()
                out = m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                             collect_forward=Mock(side_effect=error))
                self.assertEqual(out['status'], 'skipped')
                self.assertTrue(out['retainedPrevious']); self.assertFalse(out['published'])
                self.assertEqual(out['retainedAsOf'], OLD)
                self.assertEqual(a.private_rpc.call_count, 1)
                a.part4_writer_secret.assert_not_called()

    def test_real_collector_incomplete_page_still_retains(self):
        a = self.adapter()
        out = m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                     collect_forward=lambda stocks, now: offline_collect(m.collector,
                         indexes=[index_page(total_hits=2)], now=now.isoformat())[0])
        self.assertTrue(out['retainedPrevious']); self.assertEqual(out['retainedAsOf'], OLD)
        a.part4_writer_secret.assert_not_called()


class TransportBoundaryTests(unittest.TestCase):
    def test_real_transport_local_response_read_denied_is_not_retained_or_retried(self):
        from pathlib import Path
        from unittest.mock import patch
        a = retention.RetentionTests().adapter()
        with patch.object(transport.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
             patch.object(Path, 'read_text', side_effect=PermissionError('synthetic local read denied')), \
             patch.object(transport.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                       collect_forward=lambda *args: transport.safe_curl_json('https://synthetic.invalid/response'))
        self.assertEqual(run.call_count, 1); sleep.assert_not_called()
        a.private_rpc.assert_not_called(); a.part4_writer_secret.assert_not_called()

    def test_curl_launch_oserror_is_not_retained_or_retried(self):
        from unittest.mock import patch
        a = retention.RetentionTests().adapter()
        with patch.object(transport.subprocess, 'run', side_effect=PermissionError('synthetic curl launch denied')) as run, \
             patch.object(transport.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                       collect_forward=lambda *args: transport.safe_curl_json('https://synthetic.invalid/response'))
        self.assertEqual(run.call_count, 1); sleep.assert_not_called()
        a.private_rpc.assert_not_called(); a.part4_writer_secret.assert_not_called()

    def test_curl_local_file_errors_cannot_be_retained_as_transport(self):
        from unittest.mock import patch
        for returncode in (23, 26, 77):
            with self.subTest(returncode=returncode):
                a = retention.RetentionTests().adapter()
                with patch.object(transport.subprocess, 'run', return_value=subprocess.CompletedProcess([], returncode, '', '')) as run, \
                     patch.object(transport.time, 'sleep') as sleep:
                    with self.assertRaises(transport.SyncError) as caught:
                        m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                               collect_forward=lambda *args: transport.safe_curl_json('https://synthetic.invalid/response'))
                self.assertEqual(caught.exception.category, 'official_notice_local_io_failed')
                self.assertEqual(run.call_count, 1); sleep.assert_not_called()
                a.private_rpc.assert_not_called(); a.part4_writer_secret.assert_not_called()

    def test_real_transport_json_network_and_timeout_still_retain(self):
        from pathlib import Path
        from unittest.mock import patch
        cases = [(0, '{malformed'), (0, '[]'), (28, ''), (6, ''), ('timeout', '')]
        for code, text in cases:
            with self.subTest(code=code, text=text):
                a = retention.RetentionTests().adapter()
                run = Mock(side_effect=subprocess.TimeoutExpired('synthetic', 55)) if code == 'timeout' else Mock(
                    return_value=subprocess.CompletedProcess([], code, '', ''))
                with patch.object(transport.subprocess, 'run', run), patch.object(Path, 'read_text', return_value=text), \
                     patch.object(transport.time, 'sleep'):
                    out = m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                                 collect_forward=lambda *args: transport.safe_curl_json('https://synthetic.invalid/response'))
                self.assertTrue(out['retainedPrevious']); self.assertEqual(out['retainedAsOf'], OLD)
                self.assertFalse(out['published']); self.assertEqual(run.call_count, 3)
                a.part4_writer_secret.assert_not_called()
                args = run.call_args.args[0]
                self.assertIn('--fail', args); self.assertNotIn('-k', args); self.assertNotIn('--insecure', args)

    def test_unexpected_transport_exception_stays_strict(self):
        from unittest.mock import patch
        a = retention.RetentionTests().adapter()
        with patch.object(transport.subprocess, 'run', side_effect=RuntimeError('synthetic unknown bug')) as run, \
             patch.object(transport.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'synthetic unknown bug'):
                m.sync(adapter=a, now=NOW, publish=True, retain_previous=True,
                       collect_forward=lambda *args: transport.safe_curl_json('https://synthetic.invalid/response'))
        self.assertEqual(run.call_count, 1); sleep.assert_not_called()
        a.private_rpc.assert_not_called(); a.part4_writer_secret.assert_not_called()


if __name__ == '__main__':
    unittest.main()
