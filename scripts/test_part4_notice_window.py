"""Offline regression: scan the complete requested window, not all history."""
import unittest
from datetime import date
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import part4_official_announcement_sync as m

class NoticeWindowTests(unittest.TestCase):
    def test_scan_passes_date_window_to_provider(self):
        queries = []
        def transport(url):
            queries.append(parse_qs(urlparse(url).query))
            return {'success': 1, 'data': {'list': [], 'total_hits': 0, 'page_size': 100}}
        with patch.object(m, 'safe_curl_json', side_effect=transport):
            _, _, coverage, _ = m.scan_stock(m.Stock('000001', '合成标的'), date(2026,8,26), date(2026,9,29))
        self.assertTrue(coverage.complete)
        self.assertEqual(queries[0].get('begin_time'), ['2026-08-26'])
        self.assertEqual(queries[0].get('end_time'), ['2026-09-29'])

    def test_filtered_scan_rejects_out_of_window_rows(self):
        row = {'art_code':'AN202608010000000001', 'notice_date':'2026-08-01', 'title':'合成公告', 'columns':[]}
        with patch.object(m, 'fetch_notice_page', return_value={'list':[row], 'total_hits':1, 'page_size':100}):
            _, _, coverage, _ = m.scan_stock(m.Stock('000001','合成标的'), date(2026,8,26), date(2026,9,29))
        self.assertFalse(coverage.complete)
        self.assertEqual(coverage.error, 'official_notice_window_mismatch')

    def test_other_consumers_keep_unbounded_request(self):
        with patch.object(m,'safe_curl_json',return_value={'success':1,'data':{'list':[]}}) as get:
            m.fetch_notice_page('000001',1)
        query=parse_qs(urlparse(get.call_args.args[0]).query)
        self.assertNotIn('begin_time',query)
        self.assertNotIn('end_time',query)

    def test_inclusive_boundary_dates_and_complete_pagination(self):
        calls=[]
        def transport(url):
            q=parse_qs(urlparse(url).query);calls.append(q)
            p=int(q['page_index'][0]);day=['2026-09-29','2026-08-26'][p-1]
            return {'success':1,'data':{'list':[{'art_code':f'AN20260929000000000{p}','notice_date':day,'title':'合成分红公告','columns':[]}],'total_hits':2,'page_size':1}}
        with patch.object(m,'safe_curl_json',side_effect=transport):
            events,_,coverage,count=m.scan_stock(m.Stock('000001','合成标的'),date(2026,8,26),date(2026,9,29))
        self.assertTrue(coverage.complete)
        self.assertEqual((coverage.pages,count,len(events)),(2,2,2))
        self.assertEqual([q['page_index'] for q in calls],[['1'],['2']])
        self.assertTrue(all(q['begin_time']==['2026-08-26'] and q['end_time']==['2026-09-29'] for q in calls))

    def test_failed_second_page_cannot_publish_partial_coverage(self):
        row={'art_code':'AN202609290000000001','notice_date':'2026-09-29','title':'合成分红公告','columns':[]}
        with patch.object(m,'fetch_notice_page',side_effect=[{'list':[row],'total_hits':2,'page_size':1},m.SyncError('official_notice_transport_failed')]):
            _,_,coverage,_=m.scan_stock(m.Stock('000001','合成标的'),date(2026,8,26),date(2026,9,29))
        self.assertFalse(coverage.complete)
        with self.assertRaises(m.SyncError):
            m.ensure_complete_coverage([m.Stock('000001','合成标的')],[coverage])

if __name__ == '__main__':
    unittest.main()
