import unittest
import part4_official_announcement_sync as m
class ReadbackTests(unittest.TestCase):
    def test_missing_event_fails(self):
        self.assertTrue(hasattr(m,'verify_notice_readback'),'calendar lacks readback gate')
        with self.assertRaises(m.SyncError):m.verify_notice_readback([{'id':'x','title':'t'}],{'events':[]})
    def test_legacy_events_without_id_are_preserved_not_rejected(self):
        m.verify_notice_readback([{'id':'x','title':'t'}],{'events':[{'code':'600000','date':'2026-09-01','type':'legacy'}, {'id':'x','title':'t'}]})
    def test_exact_fields_allow_server_extras(self):
        self.assertTrue(hasattr(m,'verify_notice_readback'),'calendar lacks readback gate')
        m.verify_notice_readback([{'id':'x','title':'t'}],{'events':[{'id':'x','title':'t','extra':1}]})
        with self.assertRaises(m.SyncError):m.verify_notice_readback([{'id':'x','title':'t'}],{'events':[{'id':'x','title':'changed'}]})
if __name__=='__main__':unittest.main()
