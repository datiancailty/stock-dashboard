import unittest
from unittest.mock import Mock
import importlib.util
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'ops/dashboard_vps.py'
class VpsTests(unittest.TestCase):
    def module(self):
        self.assertTrue(P.exists(),'VPS runner absent')
        s=importlib.util.spec_from_file_location('vps_runner',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
    def test_health_requires_all_readbacks(self):
        m=self.module();stages={k:{'status':'ok','published':True,'readbackVerified':True} for k in m.STAGES}
        daily={'status':'ok','published':True,'stages':stages,'healthPublished':True}
        self.assertTrue(m.verify_sync(daily,{'status':'ok','sourceRelease':'r','targetDate':'2026-09-29'},'r','2026-09-29'))
        stages['notices']['readbackVerified']=False
        self.assertFalse(m.verify_sync(daily,{'status':'ok','sourceRelease':'r','targetDate':'2026-09-29'},'r','2026-09-29'))
    def test_stale_health_and_partial_rejected(self):
        m=self.module();self.assertFalse(m.verify_sync({'status':'skipped'},{'status':'ok'},'r','2026-09-29'))
    def test_current_status_rejects_old_running_and_mismatched_state(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import hashlib,json
        m=self.module();self.assertTrue(hasattr(m,'current_status'),'current-slot status gate absent')
        now=datetime(2026,9,30,22,0,tzinfo=ZoneInfo('Asia/Shanghai'))
        state={'attemptedDate':'2026-09-30','sourceRelease':'r','lastStatus':'ok','result':{'status':'ok','published':True,'healthPublished':True,'stages':{k:{'status':'ok','published':True,'readbackVerified':True} for k in m.STAGES}}}
        digest=hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        receipt={'targetDate':'2026-09-30','release':'r','dailyStatus':'ok','syncVerified':True,'checkedAt':'2026-09-30T20:00:00+08:00','dailyStateSha256':digest,'part3Prepared':True,'part3PayloadSha256':'abc'}
        mirror={'ok':True,'arm_modified':False,'payload_sha256':'abc','checkedAt':'2026-09-30T20:01:00+08:00'}
        self.assertTrue(m.current_status(receipt,state,mirror,now,'r')['syncVerified'])
        self.assertTrue(m.current_status(receipt,state,mirror,now,'r')['part3Verified'])
        for bad in ({},dict(state,lastStatus='running'),dict(state,lastStatus='error'),dict(state,attemptedDate='2026-09-29')):
            self.assertFalse(m.current_status(receipt,bad,mirror,now,'r')['syncVerified'])
        self.assertFalse(m.current_status(receipt,state,dict(mirror,payload_sha256='old'),now,'r')['part3Verified'])
    def test_spool_rejects_symlink(self):
        import tempfile,os
        m=self.module()
        self.assertTrue(hasattr(m,'read_spool'),'descriptor-based spool reader absent')
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'data';p.write_text('{}');p.chmod(0o600)
            link=Path(root)/'link';link.symlink_to(p)
            with self.assertRaises((ValueError,OSError)):m.read_spool(link,os.getuid())
            self.assertEqual(m.read_spool(p,os.getuid()),b'{}')
    def test_archive_rejects_unknown_fields(self):
        m=self.module()
        with self.assertRaises(ValueError):m.sanitize_log({'event':'http_start','token':'secret'})
        self.assertEqual(m.sanitize_log({'event':'http_start','host':'example.com','pid':1,'at':'2026-09-29T00:00:00+00:00'})['host'],'example.com')
if __name__=='__main__':unittest.main()
