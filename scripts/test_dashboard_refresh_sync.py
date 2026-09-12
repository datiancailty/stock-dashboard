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
   with patch.dict(m.os.environ,{},clear=True),patch('part4_daily_sync.read_mx_credential',side_effect=AssertionError('must not obtain unused provider key')),patch.object(m,'_run_child',return_value=SimpleNamespace(returncode=0,stdout='{"status":"ok","published":false}')):
    result=m.run_stage(name,['synthetic.py'],False)
    self.assertEqual(result['status'],'ok')
 def test_actual_readonly_stage_audit_ok_is_accepted_but_never_publish(self):
  m=self.module()
  from types import SimpleNamespace
  payload=SimpleNamespace(returncode=0,stdout='{"status":"audit_ok","coverageComplete":true,"published":false}')
  with patch.object(m,'_run_child',return_value=payload):
   dry=m.run_stage('quotes',['scripts/personal_market_snapshot_sync.py'],False)
   publish=m.run_stage('quotes',['scripts/personal_market_snapshot_sync.py'],True)
  self.assertEqual(dry['status'],'ok')
  self.assertFalse(dry['published'])
  self.assertEqual(publish['status'],'error')
 def test_stage_exception_preserves_success_and_finishes_partial_health(self):
  m=self.module()
  from dashboard_refresh_health import HealthReporter
  class MemoryHealth(HealthReporter):
   def __init__(self):super().__init__(adapter=object());self.emitted=[]
   def emit(self):self.emitted.append(json.loads(json.dumps(self.payload)))
  import subprocess
  for failure in (RuntimeError('synthetic_private_detail'),subprocess.TimeoutExpired('synthetic_private_command',0.1)):
   with self.subTest(failure=type(failure).__name__):
    health=MemoryHealth();called=[]
    def stage(name,args,publish):
     called.append(name)
     if name=='news':raise failure
     return {'status':'ok','category':'complete','published':True,'stored':3}
    try:
     with patch.object(m,'run_stage',side_effect=stage):result=m.run_refresh(True,health)
    except Exception:
     self.fail('independent news exception escaped and abandoned later stages/health')
    self.assertEqual(called,['notices','quotes','technical','news','forward','recommendations'])
    for name in ('notices','quotes','technical','forward','recommendations'):
     self.assertEqual(result['stages'][name],{'status':'ok','category':'complete','published':True,'stored':3})
    self.assertEqual(result['stages']['news']['status'],'error')
    if isinstance(failure,subprocess.TimeoutExpired):self.assertEqual(result['stages']['news']['category'],'stage_timeout')
    self.assertEqual(result['status'],'error');self.assertFalse(result['published'])
    self.assertTrue(result['healthPublished']);self.assertEqual(health.emitted[-1]['status'],'partial')
    self.assertIsNotNone(health.emitted[-1]['finishedAt'])
    self.assertNotIn('synthetic_private',json.dumps(result)+json.dumps(health.emitted))
 def test_real_news_timeout_stops_descendant_and_continues_to_final_health(self):
  import os,signal,subprocess,tempfile,time
  m=self.module()
  from dashboard_refresh_health import HealthReporter
  class MemoryHealth(HealthReporter):
   def emit(self):pass
  health=MemoryHealth(adapter=object());real_stage=m.run_stage;real_run=subprocess.run
  with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
   root=Path(directory);pidfile=root/'pid';late=root/'late';child=root/'synthetic.py'
   grandchild=f'import time;from pathlib import Path;time.sleep(0.8);Path({str(late)!r}).write_text("late");time.sleep(30)'
   child.write_text('import subprocess,sys,time\nfrom pathlib import Path\n'
    f'p=subprocess.Popen([sys.executable,"-c",{grandchild!r}])\n'
    f'Path({str(pidfile)!r}).write_text(str(p.pid))\ntime.sleep(30)\n')
   called=[]
   def stage(name,args,publish):
    called.append(name)
    return real_stage(name,[str(child)],publish) if name=='news' else {'status':'ok','published':True}
   # The run shim gives the pre-fix literal 900-second timeout the same short
   # test deadline. The fixed implementation uses the patched policy constant.
   def short_run(*args,**kwargs):kwargs['timeout']=0.4;return real_run(*args,**kwargs)
   try:
    before=time.monotonic()
    with patch.object(m,'STAGE_TIMEOUT_SECONDS',0.4,create=True),patch.object(m.subprocess,'run',side_effect=short_run),patch.object(m,'run_stage',side_effect=stage):
     result=m.run_refresh(True,health)
    self.assertLess(time.monotonic()-before,3)
    self.assertTrue(pidfile.exists(),'synthetic descendant must start before the timeout')
    self.assertEqual(called,['notices','quotes','technical','news','forward','recommendations'])
    self.assertEqual(result['stages']['news'],{'status':'error','category':'stage_timeout','published':False})
    self.assertEqual(result['stages']['forward']['status'],'ok')
    self.assertEqual(health.payload['status'],'partial');self.assertIsNotNone(health.payload['finishedAt'])
    self.assertFalse(result['published']);self.assertEqual(result['status'],'error')
    time.sleep(1)
    self.assertFalse(late.exists(),'timed-out stage descendant survived and executed a late side effect')
   finally:
    if pidfile.exists():
     try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
     except ProcessLookupError:pass
 def test_malformed_stage_result_cannot_abandon_later_stages(self):
  m=self.module()
  for malformed in (None,[],{}, {'status':'running','published':False}):
   with self.subTest(kind=type(malformed).__name__):
    called=[]
    def stage(name,args,publish):
     called.append(name)
     return malformed if name=='news' else {'status':'ok','published':False}
    try:
     with patch.object(m,'run_stage',side_effect=stage):result=m.run_refresh(False)
    except Exception:self.fail('malformed stage result abandoned refresh finalization')
    self.assertEqual(called,['notices','quotes','technical','news','forward','recommendations'])
    self.assertEqual(result['stages']['news'],{'status':'error','category':'stage_response_or_execution_failed','published':False})
    self.assertEqual(result['status'],'error');self.assertEqual(result['stages']['forward']['status'],'ok')
 def test_publish_is_true_only_when_every_stage_confirms(self):
  m=self.module()
  with patch.object(m,'run_stage',return_value={'status':'ok','published':False}): r=m.run_refresh(True)
  self.assertFalse(r['published']);self.assertEqual(r['status'],'error')
 def test_real_child_success_exit_failure_and_invalid_json_stay_sanitized(self):
  m=self.module()
  for publish in (False,True):
   code=('import json,os,sys;assert "MX_APIKEY" not in os.environ;'
         f'assert {repr("--publish" if publish else "--dry-run")} in sys.argv;'
         f'print(json.dumps({{"status":"ok","published":{publish},"stored":2,"private":"synthetic_private_payload"}}))')
   with patch.dict(m.os.environ,{'MX_APIKEY':'synthetic_never_forwarded'}):
    result=m.run_stage('quotes',['-c',code],publish)
   self.assertEqual(result,{'status':'ok','category':'complete','published':publish,'stored':2})
  for code in ('print("synthetic_private_non_json")',
               'import sys;print(\'{"status":"ok","published":true}\');sys.exit(2)'):
   result=m.run_stage('news',['-c',code],True)
   self.assertEqual(result['status'],'error');self.assertNotIn('synthetic_private',json.dumps(result))
 def test_final_health_write_failure_is_not_claimed_as_full_success(self):
  m=self.module()
  from dashboard_refresh_health import HealthReporter
  class UnavailableFinalHealth(HealthReporter):
   def emit(self):
    if self.payload['finishedAt'] is not None:raise RuntimeError('synthetic_private_health')
  health=UnavailableFinalHealth(adapter=object())
  with patch.object(m,'run_stage',return_value={'status':'ok','published':True}):result=m.run_refresh(True,health)
  self.assertEqual(len(result['stages']),6);self.assertTrue(all(s['published'] for s in result['stages'].values()))
  self.assertEqual(result['status'],'error');self.assertFalse(result['healthPublished'])
  self.assertNotIn('synthetic_private_health',json.dumps(result))
if __name__=='__main__':unittest.main()
