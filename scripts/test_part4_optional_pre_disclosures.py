"""Offline synthetic regression tests: never load sessions or credentials."""
import contextlib
import io
import json
import unittest
from datetime import date
from unittest.mock import patch

import part4_official_announcement_sync as sync


class OptionalPreDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.stock = sync.Stock("600036", "合成标的")
        self.candidate = {
            "code": self.stock.code, "name": self.stock.name,
            "date": "2026-08-29", "title": "合成标的2026年半年度报告",
            "art_code": "AN202608290000000001", "source_hash": "a" * 64,
        }
        self.payload = {"data": {"data": {"searchDataResultDTO": {
            "dataTableDTOList": [{"table": {
                "headName": ["2026中报"], "方案进度": ["预披露"],
            }}]
        }}}}

    def test_progress_without_plan_skips_only_optional_research(self):
        with patch.object(sync, "structured_dividend_payload", return_value=self.payload):
            events, checked, failures = sync.structured_pre_disclosures(
                [self.candidate], date(2026, 8, 31), enabled=True,
                requested_codes={self.stock.code},
            )
        self.assertEqual((events, checked, failures), ([], 1, 1))

    def test_manual_strict_mode_still_rejects_missing_corroboration(self):
        with patch.object(sync, "structured_dividend_payload", return_value=self.payload):
            with self.assertRaisesRegex(sync.SyncError, "structured_dividend_corroboration_missing"):
                sync.structured_pre_disclosures([self.candidate], date(2026, 8, 31),
                    enabled=True, requested_codes={self.stock.code}, strict=True)
        with patch("sys.argv", ["sync", "audit", "--strict-structured-pre-disclosures"]):
            self.assertTrue(sync.parse_args().strict_structured_pre_disclosures)

    def test_real_failures_remain_visible_and_never_write(self):
        cases = [
            ([], None, None, [], "official_notice_coverage_incomplete"),
            ([sync.ScanCoverage(self.stock.code, 1, True, None)],
             sync.SyncError("official_notice_page_missing"), None, [], "official_notice_page_missing"),
            ([sync.ScanCoverage(self.stock.code, 1, True, None)], None,
             sync.SyncError("structured_dividend_transport_failed"), [], "structured_dividend_corroboration_failed"),
            ([sync.ScanCoverage(self.stock.code, 1, True, None)], None, None,
             ["--strict-structured-pre-disclosures"], "structured_dividend_corroboration_missing"),
        ]
        for coverage, scan_error, research_error, flags, category in cases:
            with self.subTest(category=category), contextlib.ExitStack() as stack:
                output = io.StringIO()
                stack.enter_context(patch("sys.argv", ["sync", "sync", "--from", "2026-08-01",
                    "--to", "2026-08-31", "--include-structured-pre-disclosures",
                    "--structured-code", self.stock.code, *flags]))
                stack.enter_context(patch.object(sync, "load_private_session", return_value=(None, None, None)))
                stack.enter_context(patch.object(sync, "private_watchlist", return_value=[self.stock]))
                stack.enter_context(patch.object(sync, "scan_watchlist",
                    return_value=([], [self.candidate], coverage, 1), side_effect=scan_error))
                stack.enter_context(patch.object(sync, "structured_dividend_payload",
                    return_value=self.payload, side_effect=research_error))
                writer = stack.enter_context(patch.object(sync, "part4_writer_secret"))
                rpc = stack.enter_context(patch.object(sync, "private_rpc"))
                stack.enter_context(contextlib.redirect_stdout(output))
                self.assertEqual(sync.main(), 2)
                self.assertEqual(json.loads(output.getvalue()), {"status": "error", "category": category})
                writer.assert_not_called()
                rpc.assert_not_called()

    def test_missing_one_stock_does_not_drop_verified_supplement(self):
        other = {**self.candidate, "code": "600037", "art_code": "AN202608290000000002"}
        verified = {"data": {"data": {"searchDataResultDTO": {"dataTableDTOList": [
            {"table": {"headName": ["2026中报"], "方案进度": ["预披露"],
                       "分红方案": ["合成测试现金分红政策"]}}
        ]}}}}
        with patch.object(sync, "structured_dividend_payload", side_effect=[self.payload, verified]):
            events, checked, failures = sync.structured_pre_disclosures(
                [self.candidate, other], date(2026, 8, 31), enabled=True,
                requested_codes={self.stock.code, other["code"]})
        self.assertEqual((checked, failures), (2, 1))
        self.assertEqual([event["code"] for event in events], [other["code"]])
        self.assertEqual(events[0]["stage"], "pre_disclosure")
        self.assertEqual(events[0]["source"], sync.STRUCTURED_SOURCE)

    def test_plan_alone_wrong_year_and_wrong_progress_do_not_create_facts(self):
        for year, progress, plan in [("2025中报", "预披露", "现金分红"),
                                     ("2026中报", "", "现金分红"),
                                     ("2026中报", "预披露", "")]:
            with self.subTest(year=year, progress=progress, plan=plan):
                payload = {"data": {"data": {"searchDataResultDTO": {"dataTableDTOList": [
                    {"table": {"headName": [year], "方案进度": [progress], "分红方案": [plan]}}
                ]}}}}
                self.assertIsNone(sync.extract_structured_interim_pre_disclosure(self.stock, payload, 2026))

    def test_sync_keeps_ok_contract_and_emits_missing_warning(self):
        direct = {"id": "official-plan", "date": "2026-08-28", "code": self.stock.code,
                  "type": "分红方案公告", "source": sync.DIRECT_SOURCE}
        output = io.StringIO()
        # Every external boundary is replaced; main's orchestration remains real.
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("sys.argv", ["sync", "sync", "--from", "2026-08-01",
                "--to", "2026-08-31", "--include-structured-pre-disclosures",
                "--structured-code", self.stock.code]))
            stack.enter_context(patch.object(sync, "load_private_session", return_value=(None, None, None)))
            stack.enter_context(patch.object(sync, "private_watchlist", return_value=[self.stock]))
            stack.enter_context(patch.object(sync, "scan_watchlist", return_value=(
                [direct], [self.candidate], [sync.ScanCoverage(self.stock.code, 1, True, None)], 2)))
            stack.enter_context(patch.object(sync, "structured_dividend_payload", return_value=self.payload))
            stack.enter_context(patch.object(sync, "part4_writer_secret", return_value="synthetic"))
            rpc = stack.enter_context(patch.object(sync, "private_rpc", return_value={"requested": 1, "stored": 1}))
            stack.enter_context(contextlib.redirect_stdout(output))
            self.assertEqual(sync.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["structuredFailureCount"], 1)
        self.assertEqual(result["warnings"], [{"category": "structured_dividend_corroboration_missing",
                                               "count": 1}])
        self.assertTrue(result["coverageComplete"])
        self.assertEqual(rpc.call_args.args[4]["p_events"], [direct])


if __name__ == "__main__":
    unittest.main()
