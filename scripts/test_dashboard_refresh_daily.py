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
if __name__=='__main__':unittest.main()
