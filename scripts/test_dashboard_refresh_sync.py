import importlib.util,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
SCRIPTS=Path(__file__).resolve().parent
sys.path.insert(0,str(SCRIPTS))
class RefreshTests(unittest.TestCase):
 def module(self):
  p=SCRIPTS/'dashboard_refresh_sync.py'
  self.assertTrue(p.exists(),'combined independent refresh entrypoint missing')
  spec=importlib.util.spec_from_file_location('refresh_test',p); m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
 def test_news_and_evaluations_continue_on_notice_failure(self):
  m=self.module();called=[]
  def stage(name,args,publish):
   called.append(name)
   if name=='notices':return {'status':'error','category':'source_unavailable'}
   return {'status':'ok','published':publish}
  with patch.object(m,'run_stage',side_effect=stage): r=m.run_refresh(False)
  self.assertEqual(called,['notices','quotes','technical','news','forward','recommendations'])
  self.assertEqual(r['stages']['forward']['status'],'ok')
  self.assertFalse(r['published']);self.assertEqual(r['status'],'error')
 def test_dryrun_passed_to_every_stage_no_ai_entry(self):
  m=self.module();calls=[]
  def stage(name,args,publish):calls.append((name,args,publish));return {'status':'ok','published':False}
  with patch.object(m,'run_stage',side_effect=stage): r=m.run_refresh(False)
  self.assertEqual(len(calls),6);self.assertTrue(all(not x[2] for x in calls));self.assertFalse(r['published'])
  self.assertTrue(all('codex' not in str(x).lower() and 'plus_strategy_worker' not in str(x) for x in calls))
 def test_public_basis_and_formal_notices_do_not_require_mx_data(self):
  m=self.module();calls=[]
  def stage(name,args,publish):calls.append((name,args));return {'status':'ok','published':False}
  with patch.object(m,'run_stage',side_effect=stage):m.run_refresh(False)
  commands=dict(calls)
  self.assertEqual(commands['forward'],['scripts/personal_dividend_refresh_sync.py'])
  self.assertNotIn('--include-structured-pre-disclosures',commands['notices'])
  from types import SimpleNamespace
  for name in ('notices','forward','news'):
   with patch.dict(m.os.environ,{},clear=True),patch('part4_daily_sync.read_mx_credential',side_effect=AssertionError('must not obtain unused provider key')),patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"status":"ok","published":false}')):
    result=m.run_stage(name,['synthetic.py'],False)
    self.assertEqual(result['status'],'ok')
 def test_actual_readonly_stage_audit_ok_is_accepted_but_never_publish(self):
  m=self.module()
  from types import SimpleNamespace
  payload=SimpleNamespace(returncode=0,stdout='{"status":"audit_ok","coverageComplete":true,"published":false}')
  with patch.object(m.subprocess,'run',return_value=payload):
   dry=m.run_stage('quotes',['scripts/personal_market_snapshot_sync.py'],False)
   publish=m.run_stage('quotes',['scripts/personal_market_snapshot_sync.py'],True)
  self.assertEqual(dry['status'],'ok')
  self.assertFalse(dry['published'])
  self.assertEqual(publish['status'],'error')
 def test_publish_is_true_only_when_every_stage_confirms(self):
  m=self.module()
  with patch.object(m,'run_stage',return_value={'status':'ok','published':False}): r=m.run_refresh(True)
  self.assertFalse(r['published']);self.assertEqual(r['status'],'error')
if __name__=='__main__':unittest.main()
