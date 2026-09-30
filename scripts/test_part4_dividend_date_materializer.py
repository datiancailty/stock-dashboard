"""Synthetic native F10 rows only; never imports a session or calls a source."""
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any
import importlib
import importlib.util
import socket
import unittest
from unittest.mock import patch

import part4_official_announcement_sync as sync


def fixture():
    aid = 'AN202609200000000001'
    event = sync.normalize_direct_event(
        sync.Stock('600001', '合成甲'),
        {'art_code': aid, 'notice_date': '2026-09-21',
         'title': '合成甲:2026年中期利润分派A股实施公告', 'columns': [],
         'codes': [{'stock_code': '600001', 'short_name': '合成甲'}]},
        date(2026, 9, 1), date(2026, 9, 30))
    assert event is not None
    row: dict[str, Any] = {'INFO_CODE': aid, 'SECURITY_CODE': '600001', 'SECUCODE': '600001.SH',
           'ASSIGN_OBJECT': 'A股股东', 'ASSIGN_PROGRESS': '实施方案',
           'NOTICE_DATE': '2026-09-21 00:00:00', 'REPORT_TIME': '2026-06-30 00:00:00',
           'REPORT_DATE': '2026半年报', 'IMPL_PLAN_PROFILE': '10派3元',
           'EQUITY_RECORD_DATE': '2026-09-28 00:00:00',
           'EX_DIVIDEND_DATE': '2026-09-29 00:00:00',
           'PAY_CASH_DATE': '2026-10-09 00:00:00'}
    return event, row


class DateMaterializerTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def materialize(self, notices, rows):
        spec = importlib.util.find_spec('part4_dividend_date_materializer')
        self.assertIsNotNone(spec, 'Part4缺少结构化日期物化器')
        module = importlib.import_module('part4_dividend_date_materializer')
        return module.materialize_calendar(notices, rows)

    def test_materializes_three_distinct_dates_without_replacing_announcement(self):
        event, row = fixture()
        before = deepcopy((event, row))
        result = self.materialize([event], [row])
        self.assertEqual((event, row), before)
        self.assertEqual(result['status'], 'local_candidate')
        self.assertTrue(result['requiresHostedMigration'])
        self.assertEqual(result['missingDates'], [])
        self.assertEqual(result['implementationDateCount'], 3)
        self.assertEqual(len(result['events']), 4)
        self.assertIn(event, result['events'])
        dated = [e for e in result['events'] if e['id'] != event['id']]
        self.assertEqual({e['type']: e['date'] for e in dated}, {
            '股权登记日': '2026-09-28', '除权除息日': '2026-09-29', '派息日': '2026-10-09'})
        self.assertEqual(len({e['id'] for e in dated}), 3)
        for item in dated:
            self.assertEqual(item['noticeId'], event['id'])
            self.assertEqual(item['noticeDate'], event['date'])
            self.assertEqual(item['reportDate'], '2026-06-30')
            self.assertEqual(item['sourceUrl'], event['sourceUrl'])
            self.assertEqual(len(item['sourceHash']), 64)
            self.assertNotIn('paid', item['stage'])

    def test_unknown_payment_is_not_filled_from_ex_date(self):
        event, row = fixture()
        row['PAY_CASH_DATE'] = ''
        result = self.materialize([event], [row])
        self.assertEqual(result['implementationDateCount'], 2)
        self.assertNotIn('派息日', [e['type'] for e in result['events']])
        self.assertEqual(result['missingDates'], [{'noticeId': event['id'],
                         'reportDate': '2026-06-30', 'dateField': 'PAY_CASH_DATE'}])

    def test_dates_require_exact_a_share_implementation_binding(self):
        event, row = fixture()
        conflicts = {'SECURITY_CODE': '600002', 'SECUCODE': '600001.HK',
                     'ASSIGN_OBJECT': 'H股股东', 'ASSIGN_PROGRESS': '预案',
                     'NOTICE_DATE': '2026-09-20 00:00:00',
                     'INFO_CODE': 'AN202609200000000002'}
        for field, value in conflicts.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'calendar_'):
                    self.materialize([event], [{**row, field: value}])

    def test_proposals_and_cancelled_notices_never_gain_implementation_dates(self):
        event, row = fixture()
        for stage in ('proposal', 'pre_disclosure'):
            with self.subTest(stage=stage):
                proposal = {**event, 'stage': stage}
                result = self.materialize([proposal], [row])
                self.assertEqual(result['events'], [proposal])
                self.assertEqual(result['implementationDateCount'], 0)

    def test_invalid_calendar_dates_and_reversed_chronology_fail_closed(self):
        event, row = fixture()
        conflicts = (('PAY_CASH_DATE', '2026-02-30'), ('PAY_CASH_DATE', '2026-09-28'),
                     ('EX_DIVIDEND_DATE', '2026-09-27'), ('EQUITY_RECORD_DATE', '2026-09-20'),
                     ('PAY_CASH_DATE', '2026-10-09 untrusted'), ('REPORT_TIME', 'bad'),
                     ('NOTICE_DATE', '2026-09-21 garbage'))
        for field, value in conflicts:
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, 'calendar_'):
                    self.materialize([event], [{**row, field: value}])

    def test_eight_synthetic_implementations_include_null_period_special_distribution(self):
        notices, rows = [], []
        for index in range(8):
            event, row = fixture()
            code, aid = f'600{index + 1:03}', f'AN20260920000000000{index + 1}'
            event.update(code=code, id='eastmoney:' + aid,
                         sourceUrl=f'https://data.eastmoney.com/notices/detail/{code}/{aid}.html')
            row.update(SECURITY_CODE=code, SECUCODE=code + '.SH', INFO_CODE=aid,
                       ASSIGN_OBJECT='全体股东')
            if index == 0:
                event['title'] = '合成甲:2026年特别分红权益分派实施公告'
                row.update(REPORT_TIME=None, REPORT_DATE='2026特别分配')
            notices.append(event)
            rows.append(row)
        try:
            result = self.materialize(notices, rows)
        except ValueError as error:
            self.fail(f'已绑定A股特别分配/全体股东行被误拒绝: {error}')
        self.assertEqual(result['implementationDateCount'], 24)
        self.assertEqual(len(result['events']), 32)
        special = [e for e in result['events'] if e.get('noticeId') == notices[0]['id']]
        self.assertEqual(len(special), 3)
        self.assertTrue(all(e['reportDate'] is None for e in special), '不得猜特别分红会计日期')
        self.assertTrue(all(e['reportPeriod'] == '2026特别分配' for e in special))

    def test_duplicate_or_conflicting_version_in_one_batch_is_rejected(self):
        event, row = fixture()
        for notices, rows in (([event, event], [row]), ([event], [row, row]),
                              ([event], [row, {**row, 'PAY_CASH_DATE': '2026-10-10'}])):
            with self.subTest(notices=len(notices), rows=len(rows)):
                with self.assertRaisesRegex(ValueError, 'calendar_duplicate_event_id'):
                    self.materialize(notices, rows)

    def test_malformed_notice_binding_is_rejected_before_materialization(self):
        event, row = fixture()
        for field, value in (('id', 'eastmoney:OTHER'), ('code', '600001.SH'),
                             ('sourceUrl', 'https://example.invalid/notices'),
                             ('sourceHash', ''), ('name', ''), ('title', ''),
                             ('date', '2026-09-21 garbage')):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, 'calendar_'):
                    self.materialize([{**event, field: value}], [row])

    def test_duplicate_rows_with_all_dates_unknown_are_still_rejected(self):
        event, row = fixture()
        row.update(EQUITY_RECORD_DATE=None, EX_DIVIDEND_DATE=None, PAY_CASH_DATE=None)
        with self.assertRaisesRegex(ValueError, 'calendar_duplicate_event_id'):
            self.materialize([event], [row, row])

    def test_malformed_inputs_and_unknown_source_metadata_fail_closed(self):
        event, row = fixture()
        cases = ((None, [row]), ([event], None), ([None], [row]), ([event], [None]),
                 ([{**event, 'stage': 'paid'}], [row]),
                 ([{**event, 'source': 'unverified'}], [row]),
                 ([event], [{**row, 'REPORT_DATE': ''}]),
                 ([event], [{**row, 'REPORT_DATE': '2025半年报'}]))
        for notices, rows in cases:
            with self.subTest(notices=notices, rows=rows):
                try:
                    self.materialize(notices, rows)
                except ValueError as error:
                    self.assertTrue(str(error).startswith('calendar_'))
                except Exception as error:
                    self.fail(f'未按契约拒绝错误输入: {type(error).__name__}')
                else:
                    self.fail('错误输入被接受')


    def test_preference_share_notice_is_preserved_without_ordinary_share_dates(self):
        event, _ = fixture()
        event['title'] = '合成甲:关于境内优先股“合成优1”股息派发实施的公告'
        try:
            result = self.materialize([event], [])
        except ValueError as error:
            self.fail(f'非普通股公告不应要求普通A股F10行: {error}')
        self.assertEqual(result['events'], [event])
        self.assertEqual(result['implementationDateCount'], 0)
        self.assertEqual(result['excludedImplementationNotices'], [
            {'noticeId': event['id'], 'reason': 'explicit_preference_share_notice'}])

    # Characterization checks below protect already-passing invariants; no
    # production behavior was added after these tests.
    def test_ambiguous_ordinary_and_preference_title_does_not_bypass_missing_evidence(self):
        event, _ = fixture()
        for title in ('合成甲:关于优先股及普通股股息派发实施公告',
                      '合成甲:关于境内优先股与A股实施公告'):
            with self.subTest(title=title):
                with self.assertRaisesRegex(ValueError, 'calendar_implementation_row_missing'):
                    self.materialize([{**event, 'title': title}], [])

    def test_same_day_annual_and_interim_events_keep_separate_identities(self):
        event, row = fixture()
        annual = {**row, 'REPORT_DATE': '2025年报', 'REPORT_TIME': '2025-12-31'}
        result = self.materialize([event], [annual, row])
        self.assertEqual(result['implementationDateCount'], 6)
        self.assertEqual(len({e['id'] for e in result['events']}), 7)
        self.assertEqual(result, self.materialize([event], [row, annual]))

    def test_date_revision_keeps_id_but_changes_bound_evidence_hash(self):
        event, row = fixture()
        first = self.materialize([event], [row])
        second = self.materialize([event], [{**row, 'PAY_CASH_DATE': '2026-10-10'}])
        old = next(e for e in first['events'] if e['type'] == '派息日')
        new = next(e for e in second['events'] if e['type'] == '派息日')
        self.assertEqual(old['id'], new['id'])
        self.assertNotEqual(old['date'], new['date'])
        self.assertNotEqual(old['sourceHash'], new['sourceHash'])

    def test_null_missing_and_placeholder_dates_stay_unknown(self):
        event, row = fixture()
        for value in (None, '', '-'):
            with self.subTest(value=value):
                result = self.materialize([event], [{**row, 'PAY_CASH_DATE': value}])
                self.assertEqual(result['implementationDateCount'], 2)
                self.assertEqual(result['missingDates'][0]['dateField'], 'PAY_CASH_DATE')
        row.pop('PAY_CASH_DATE')
        self.assertEqual(self.materialize([event], [row])['implementationDateCount'], 2)

    def test_original_notice_history_is_not_replaced_by_new_dates(self):
        event, row = fixture()
        old = {**event, 'id': 'eastmoney:AN202501010000000001', 'date': '2025-01-01',
               'stage': 'proposal',
               'sourceUrl': 'https://data.eastmoney.com/notices/detail/600001/AN202501010000000001.html'}
        result = self.materialize([old, event], [row])
        self.assertIn(old, result['events'])
        self.assertIn(event, result['events'])
        self.assertEqual(len(result['events']), 5)

    def test_static_existing_sql_contract_is_an_explicit_publication_blocker(self):
        # This is source-contract verification, NOT an executed Hosted/PGlite test.
        root = Path(__file__).resolve().parents[1]
        sql = (root / 'supabase/migrations/20260831010000_personal_part4_official_dividend_notices.sql').read_text()
        self.assertIn("source_id ~ '^eastmoney:AN[0-9]{12,32}$'", sql)
        self.assertIn("v_event_type not in ('分红方案公告', '权益分派公告', '分红相关决议', '中期分红预披露')", sql)
        self.assertIn('(select count(*) from jsonb_object_keys(v_item)) <> 11', sql)
        self.assertIn('p_official_notice_count < v_event_count', sql)
        self.assertIn('v_event_date < v_window_start or v_event_date > v_window_end', sql)
        event, row = fixture()
        result = self.materialize([event], [row])
        payment = next(e for e in result['events'] if e['type'] == '派息日')
        self.assertNotRegex(payment['id'], r'^eastmoney:AN[0-9]{12,32}$')
        self.assertGreater(len(payment), 11)
        self.assertTrue(result['requiresHostedMigration'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
