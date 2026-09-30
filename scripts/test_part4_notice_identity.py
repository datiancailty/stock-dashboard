"""Regression: source issuer identity is evidence, not a request label."""
import copy,unittest
from datetime import date
from unittest.mock import patch
import part4_official_announcement_sync as m
START,END=date(2026,9,1),date(2026,9,30)
STOCK=m.Stock('600000','合成甲')

def raw():
    return {'art_code':'AN202609200000000001','notice_date':'2026-09-20',
            'title':'合成甲:2026年中期利润分配预案公告','columns':[],
            'codes':[{'stock_code':'600000','short_name':'合成甲'}]}

class NoticeIdentityTests(unittest.TestCase):
    def test_wrong_or_absent_source_identity_makes_scan_incomplete(self):
        for codes in [None,[],[{'stock_code':'600999','short_name':'合成乙'}],'600000',[None]]:
            row=raw();row['codes']=codes
            with self.subTest(codes=codes),patch.object(m,'fetch_notice_page',return_value={'total_hits':1,'page_size':100,'list':[row]}):
                events,_,coverage,_=m.scan_stock(STOCK,START,END)
            self.assertFalse(coverage.complete)
            self.assertEqual(events,[])
            with self.assertRaises(m.SyncError):m.ensure_complete_coverage([STOCK],[coverage])
    def test_standalone_normalizers_reject_foreign_issuer(self):
        row=raw();row['codes']=[{'stock_code':'600999','short_name':'合成乙'}]
        with self.assertRaises(m.SyncError):m.normalize_direct_event(STOCK,row,START,END)
        row['title']='合成乙:2026年半年度报告'
        with self.assertRaises(m.SyncError):m.normalize_generic_candidate(STOCK,row,START,END)
    def test_explicit_associated_foreign_issuer_is_not_relabelled(self):
        row=raw();row['codes'].append({'stock_code':'600999','short_name':'合成乙'})
        row['title']='合成乙:2026年中期利润分配预案公告'
        with self.assertRaises(m.SyncError):m.normalize_direct_event(STOCK,row,START,END)
    def test_explicit_foreign_subject_without_colon_and_parent_wrapper_is_not_relabelled(self):
        for title in ['合成乙2026年中期利润分配预案公告','合成乙股份有限公司2026年中期利润分配预案公告','合成甲:合成乙2026年中期利润分配预案公告']:
            row=raw();row['codes'].append({'stock_code':'600999','short_name':'合成乙'});row['title']=title
            with self.subTest(title=title),self.assertRaises(m.SyncError):m.normalize_direct_event(STOCK,row,START,END)
        row['title']='合成乙2026年半年度报告'
        with self.assertRaises(m.SyncError):m.normalize_generic_candidate(STOCK,row,START,END)
    def test_multisecurity_unknown_subject_is_rejected_not_assumed(self):
        row=raw();row['codes'].append({'stock_code':'600999','short_name':'合成乙'});row['title']='2026年中期利润分配预案公告'
        with self.assertRaises(m.SyncError):m.normalize_direct_event(STOCK,row,START,END)
    def test_same_issuer_dual_listing_remains_bound_to_requested_a_share(self):
        row=raw();row['codes'].append({'stock_code':'00001','short_name':'合成甲'})
        self.assertIsNotNone(m.normalize_direct_event(STOCK,row,START,END))
    def test_source_hash_binds_provider_security_metadata(self):
        row=raw();first=m.normalize_direct_event(STOCK,row,START,END)
        changed=copy.deepcopy(row);changed['codes'][0]['short_name']='合成甲原名'
        second=m.normalize_direct_event(STOCK,changed,START,END)
        self.assertNotEqual(first['sourceHash'],second['sourceHash'])

if __name__=='__main__':unittest.main()
