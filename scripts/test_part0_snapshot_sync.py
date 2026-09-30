import unittest, tempfile, json, os
from pathlib import Path
from datetime import datetime,timezone
try:
 import part0_snapshot_sync as subject
except ImportError:
 subject=None
class PublisherTests(unittest.TestCase):
 def test_publishes_only_monitor_and_reads_back_exact_payload(self):
  self.assertIsNotNone(subject,'Part0 publisher is missing')
  payload={'schemaVersion':1,'observedAt':datetime.now(timezone.utc).isoformat(),'account':None,'trades':[],'runtime':{},'events':[]}
  class Adapter:
   calls=[]
   @staticmethod
   def load_private_session():return None,{},'synthetic'
   @staticmethod
   def part4_writer_secret(*args):return 'synthetic'
   @classmethod
   def private_rpc(cls,w,c,t,name,body):
    cls.calls.append(name)
    if name=='personal_sync_part0_monitor':self.assertEqual(body['p_payload'],payload);return {'stored':True}
    self.assertEqual(name,'personal_get_part0_monitor');return payload
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory).resolve()/'snapshot.json';p.write_text(json.dumps(payload));os.chmod(p,0o640)
   result=subject.sync(p,publish=True,adapter=Adapter,expected_owner=os.getuid())
   self.assertTrue(result['readbackVerified']);self.assertEqual(result['providerCalls'],0)
   self.assertEqual(Adapter.calls,['personal_sync_part0_monitor','personal_get_part0_monitor'])
   Adapter.calls=[];payload['observedAt']='2000-01-01T00:00:00+00:00';p.write_text(json.dumps(payload))
   with self.assertRaisesRegex(ValueError,'stale'):subject.sync(p,publish=True,adapter=Adapter,expected_owner=os.getuid())
   self.assertEqual(Adapter.calls,[])
if __name__=='__main__':unittest.main()
