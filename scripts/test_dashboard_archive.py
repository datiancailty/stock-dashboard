"""Real local Git repos only: failed archive must not poison its next run."""
import contextlib,io,json,subprocess,tempfile,unittest,importlib.util
from pathlib import Path
from unittest.mock import patch
class ArchiveRecoveryTests(unittest.TestCase):
 def test_partial_workspace_failure_recovers_without_daily(self):
  path=Path(__file__).resolve().parents[1]/'ops/dashboard_vps.py'
  spec=importlib.util.spec_from_file_location('archive_under_test',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp).resolve();home=root/'home';home.mkdir(mode=0o700);logs=home/'diagnostics';logs.mkdir(mode=0o700)
   remote=root/'remote.git';seed=root/'seed'
   def git(*args):subprocess.run(['git',*map(str,args)],check=True,capture_output=True)
   git('init','--bare','--initial-branch=main',remote);git('clone',remote,seed)
   git('-C',seed,'config','user.name','test');git('-C',seed,'config','user.email','test@example.invalid')
   (seed/'diagnostics').mkdir();(seed/'diagnostics/2026-09-28.jsonl.gz').write_bytes(b'old synthetic tracked blob')
   (seed/'README.md').write_text('synthetic archive test\n');git('-C',seed,'add','README.md','diagnostics');git('-C',seed,'commit','-m','init');git('-C',seed,'push','origin','main')
   valid={'event':'http_start','host':'example.com','pid':1,'at':'2026-09-29T00:00:00+00:00'}
   (logs/'2026-09-28.jsonl').write_text(json.dumps(valid)+'\n');(logs/'2026-09-29.jsonl').write_text('{invalid\n')
   import dashboard_refresh_daily as daily
   with patch.object(m,'HOME',home),patch.object(m,'config',return_value={'archive_repo':str(remote)}),patch.object(daily,'verify_release',return_value='r'),patch.object(daily,'read_state',return_value={}),patch.object(daily,'run_once',side_effect=AssertionError('business replay forbidden')),contextlib.redirect_stdout(io.StringIO()):
    self.assertEqual(m.archive(),2)
    self.assertEqual(list(home.glob('archive-*')),[home/'archive-receipt.json'])
    (logs/'2026-09-29.jsonl').write_text(json.dumps(valid)+'\n')
    self.assertEqual(m.archive(),0)
    receipt=json.loads((home/'archive-receipt.json').read_text());self.assertTrue(receipt['ok'])
    remote_sha=subprocess.check_output(['git','--git-dir',str(remote),'rev-parse','main'],text=True).strip()
    self.assertEqual(remote_sha,receipt['remoteCommit'])
if __name__=='__main__':unittest.main()
