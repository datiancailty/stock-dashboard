"""Synthetic/offline regressions for audited Part 4 classification omissions."""
from datetime import date
import socket
import unittest
from unittest.mock import patch

import part4_official_announcement_sync as sync

START, END = date(2026, 9, 1), date(2026, 9, 30)
STOCK = sync.Stock('600001', '合成甲')


def notice(title, columns=(), aid='AN202609200000000001'):
    return {'art_code': aid, 'notice_date': '2026-09-21 00:00:00',
            'title': '合成甲:' + title, 'columns': [{'column_name': c} for c in columns],
            'codes': [{'stock_code': STOCK.code, 'short_name': STOCK.name}]}


class CalendarSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_profit_distribution_synonym_is_implementation_notice(self):
        event = sync.normalize_direct_event(
            STOCK, notice('2026年中期利润分派A股实施公告'), START, END)
        self.assertIsNotNone(event, '明确利润分派标题不能被丢弃')
        assert event is not None
        self.assertEqual(event['date'], '2026-09-21')
        self.assertEqual(event['stage'], 'implementation')
        self.assertEqual(event['id'], 'eastmoney:AN202609200000000001')
        self.assertEqual(event['source'], sync.DIRECT_SOURCE)

    def test_discussion_records_are_not_distribution_notices(self):
        for title in ('投资者关系活动记录表（利润分派答问）', '调研纪要：现金分红讨论'):
            with self.subTest(title=title):
                self.assertIsNone(sync.normalize_direct_event(STOCK, notice(title), START, END))

    def test_explicit_distribution_columns_cover_implementation_and_resolution(self):
        cases = (
            ('2026年中期分配事项实施公告', '分配方案实施', '分红方案公告', 'implementation'),
            ('2026年临时股东会决议公告', '分配方案决议公告', '分红相关决议', 'proposal'),
            ('2026年临时股东大会决议公告', '分配方案决议公告', '分红相关决议', 'proposal'),
        )
        for title, column, kind, stage in cases:
            with self.subTest(title=title):
                event = sync.normalize_direct_event(STOCK, notice(title, [column]), START, END)
                self.assertIsNotNone(event, '明确分配栏目必须入选')
                assert event is not None
                self.assertEqual((event['type'], event['stage']), (kind, stage))


if __name__ == '__main__':
    unittest.main(verbosity=2)
