"""Calendar, duplicate-attempt and state-preservation tests; no launchd/network."""
import unittest,json,tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock
class DailyTests(unittest.TestCase):
 def setUp(self):
  p=Path(__file__).with_name('dashboard_refresh_daily.py');self.assertTrue(p.exists(),'daily entrypoint not implemented')
  import dashboard_refresh_daily as m
  self.m=m
 def test_china_due_gate_and_once_per_day(self):
  cases=[('2026-09-09T10:04:00+00:00',{},None),('2026-09-09T10:05:00+00:00',{},None),('2026-09-12T18:06:00+08:00',{},None),('2026-09-09T18:06:00+08:00',{'attemptedDate':'2026-09-09'},'already_attempted')]
  for instant,state,expected in cases:self.assertEqual(self.m.due_reason(datetime.fromisoformat(instant),state),expected)
 def test_dry_run_never_advances_write_state(self):
  with tempfile.TemporaryDirectory() as d:
   state=Path(d)/'state';call=Mock(return_value={'status':'ok','published':False})
   result=self.m.run_once(state_dir=state,refresh_fn=call,publish=False,automatic=False)
   self.assertFalse(result['published']);call.assert_called_once_with(False);self.assertFalse(state.exists())
 def test_failed_attempt_is_recorded_once_but_success_date_not_advanced(self):
  with tempfile.TemporaryDirectory() as d:
   state=Path(d)/'state';call=Mock(return_value={'status':'error','published':False,'stages':{'forward':{'status':'error','category':'coverage_incomplete','published':False}}})
   now=datetime.fromisoformat('2026-09-09T18:06:00+08:00')
   result=self.m.run_once(state_dir=state,refresh_fn=call,publish=True,automatic=False,now=now)
   self.assertEqual(result['status'],'error')
   saved=json.loads((state/'state.json').read_text());self.assertNotIn('successfulDate',saved);self.assertEqual(saved['attemptedDate'],'2026-09-09')
   again=self.m.run_once(state_dir=state,refresh_fn=call,publish=True,automatic=True,now=now,release_check=lambda:'synthetic-release')
   self.assertEqual(again['category'],'already_attempted');self.assertEqual(call.call_count,1)
 def test_abrupt_exit_preserves_current_stage_checkpoints_without_auto_replay(self):
  import subprocess,sys
  with tempfile.TemporaryDirectory() as directory:
   state=Path(directory)/'state';self.m.private_dir(state)
   old={'attemptedDate':'2026-09-10','successfulDate':'2026-09-10','lastStatus':'ok',
        'finishedAt':'2026-09-10T18:38:30+08:00','result':{'status':'ok','published':True,
        'stages':{k:{'status':'ok','published':True,'stored':999} for k in self.m.STAGES}}}
   self.m.save_state(state/'state.json',old)
   code='''import sys,os
from datetime import datetime
sys.path.insert(0, SCRIPTS)
import dashboard_refresh_daily as daily
import dashboard_refresh_sync as sync
import dashboard_refresh_health as health
class MemoryHealth(health.HealthReporter):
 def __init__(self,**kwargs):super().__init__(adapter=object(),**kwargs)
 def emit(self):pass
health.HealthReporter=MemoryHealth
def stage(name,args,publish):
 if name=='news':os._exit(23)
 return {'status':'ok','category':'complete','published':True,'stored':1}
sync.run_stage=stage
daily.run_once(state_dir=STATE,publish=True,now=datetime.fromisoformat('2026-09-11T18:05:06+08:00'))
'''.replace('SCRIPTS',repr(str(Path(__file__).resolve().parent))).replace('STATE',repr(str(state)))
   child=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=10)
   self.assertEqual(child.returncode,23,'synthetic abrupt exit must occur after the first three stages')
   saved=self.m.read_state(state/'state.json')
   self.assertIsNone(saved.get('finishedAt'),'new run must not inherit prior finishedAt')
   self.assertEqual(saved['lastStatus'],'running');self.assertEqual(saved['successfulDate'],'2026-09-10')
   self.assertEqual(saved['result']['status'],'running');self.assertFalse(saved['result']['published'])
   for name in ('notices','quotes','technical'):
    self.assertEqual(saved['result']['stages'][name],{'status':'ok','category':'complete','published':True,'stored':1})
   self.assertEqual(saved['result']['stages']['news']['status'],'running')
   self.assertEqual(saved['result']['stages']['forward']['status'],'pending')
   replay=Mock();now=datetime.fromisoformat('2026-09-11T18:16:00+08:00')
   result=self.m.run_once(state_dir=state,refresh_fn=replay,publish=True,automatic=True,now=now,release_check=lambda:'synthetic-release')
   replay.assert_not_called()
   self.assertEqual(result['status'],'error');self.assertEqual(result['category'],'daily_previous_attempt_interrupted')
   recovered=self.m.read_state(state/'state.json')
   self.assertEqual(recovered['lastStatus'],'error');self.assertEqual(recovered['successfulDate'],'2026-09-10')
   self.assertIsNone(recovered.get('finishedAt'),'detection time is not the unknown interruption time')
   self.assertEqual(recovered['interruptedDetectedAt'],now.isoformat(timespec='seconds'))
   self.assertEqual(result['stages']['quotes']['stored'],1)
   self.assertEqual(result['stages']['news']['status'],'error')
   self.assertEqual(result['stages']['forward']['status'],'skipped')
 def test_completed_real_publish_advances_success_date(self):
  with tempfile.TemporaryDirectory() as d:
   now=datetime.fromisoformat('2026-09-09T18:06:00+08:00')
   call=Mock(return_value={'status':'ok','published':True,'stages':{k:{'status':'ok','published':True} for k in self.m.STAGES}})
   out=self.m.run_once(state_dir=Path(d)/'state',refresh_fn=call,publish=True,automatic=False,now=now)
   self.assertTrue(out['published']);self.assertEqual(json.loads((Path(d)/'state/state.json').read_text())['successfulDate'],'2026-09-09')
 def test_incomplete_stage_cannot_falsely_advance_success(self):
  with tempfile.TemporaryDirectory() as d:
   call=Mock(return_value={'status':'ok','published':True,'stages':{'quotes':{'status':'ok','published':True}}})
   out=self.m.run_once(state_dir=Path(d)/'state',refresh_fn=call,publish=True,automatic=False)
   self.assertEqual(out['status'],'error');self.assertFalse(out['published'])
 def test_plist_has_calendar_catchup_without_interval_or_wake(self):
  import plistlib
  p=plistlib.loads(self.m.plist_bytes('/usr/local/bin/python3',Path('/synthetic/release/scripts/dashboard_refresh_daily.py'),Path('/synthetic/runtime'),hour=15,minute=5))
  self.assertTrue(p['RunAtLoad']);self.assertEqual(len(p['StartCalendarInterval']),5)
  self.assertNotIn('StartInterval',p);self.assertNotIn('KeepAlive',p)
  self.assertIn('--automatic',p['ProgramArguments']);self.assertIn('--publish',p['ProgramArguments'])
 def test_malformed_refresh_result_records_error_not_success(self):
  for value in (None,{'status':'ok','published':True,'stages':None},{'status':'ok','published':True,'stages':{k:None for k in self.m.STAGES}}):
   with self.subTest(value=value),tempfile.TemporaryDirectory() as d:
    out=self.m.run_once(state_dir=Path(d)/'state',refresh_fn=Mock(return_value=value),publish=True,automatic=False)
    self.assertEqual(out['status'],'error');self.assertFalse(out['published'])
    self.assertEqual(json.loads((Path(d)/'state/state.json').read_text())['lastStatus'],'error')
 def test_existing_lock_prevents_second_execution(self):
  import os,fcntl
  with tempfile.TemporaryDirectory() as d:
   state=Path(d);fd=os.open(state/'run.lock',os.O_CREAT|os.O_RDWR,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
   try:
    call=Mock();out=self.m.run_once(state_dir=state,refresh_fn=call,publish=True,automatic=False)
    self.assertEqual(out['category'],'already_running');call.assert_not_called()
   finally:os.close(fd)
 def test_corrupt_state_cannot_be_treated_as_first_run(self):
  with tempfile.TemporaryDirectory() as d:
   state=Path(d);p=state/'state.json';p.write_text('{');p.chmod(0o600);call=Mock()
   with self.assertRaisesRegex(ValueError,'state_invalid'):
    self.m.run_once(state_dir=state,refresh_fn=call,publish=True,automatic=False)
   call.assert_not_called()
 def test_legacy_interruption_does_not_relabel_previous_day_success_as_current(self):
  with tempfile.TemporaryDirectory() as directory:
   state=Path(directory)
   self.m.save_state(state/'state.json',{'attemptedDate':'2026-09-11','successfulDate':'2026-09-10',
    'lastStatus':'running','finishedAt':'2026-09-10T18:38:30+08:00',
    'result':{'status':'ok','published':True,'stages':{k:{'status':'ok','published':True} for k in self.m.STAGES}}})
   refresh=Mock()
   result=self.m.run_once(state_dir=state,refresh_fn=refresh,publish=True,automatic=True,
    now=datetime.fromisoformat('2026-09-11T18:16:00+08:00'),release_check=lambda:'synthetic-release')
   self.assertEqual(result['status'],'error');self.assertFalse(result['published'])
   self.assertEqual(result['category'],'daily_previous_attempt_interrupted');self.assertEqual(result['stages'],{})
   refresh.assert_not_called();self.assertEqual(self.m.read_state(state/'state.json')['successfulDate'],'2026-09-10')
if __name__=='__main__':unittest.main()
