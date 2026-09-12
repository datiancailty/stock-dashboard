#!/usr/bin/env python3
"""Non-waking weekday refresh. Installation is a separate, authorized action.

Automatic execution requires an immutable-release file manifest, an exclusive
lock and a single-attempt Asia/Shanghai date gate. Failed attempts never advance
successfulDate; explicit manual retries remain possible. No AI or trading.
"""
from __future__ import annotations
import argparse,fcntl,hashlib,json,os,plistlib,re,stat,tempfile
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
BJ=ZoneInfo('Asia/Shanghai')
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=Path.home()/'.hermes/workspace/stock-dashboard-private-runtime'
STAGES=('notices','quotes','technical','news','forward','recommendations')
LABEL='com.datiancailty.stock-dashboard.refresh-daily'

def target_slot(now):
 now=now.astimezone(BJ);day=now.date()
 if (now.hour,now.minute)<(18,5):day-=timedelta(days=1)
 while day.weekday()>=5:day-=timedelta(days=1)
 return day.isoformat()

def due_reason(now,state):
 # RunAtLoad after days powered off and launchd missed-calendar wake both
 # coalesce to one newest due slot, never replay each missing date.
 if state.get('attemptedDate')==target_slot(now):return 'already_attempted'
 return None

def private_dir(path):
 path.mkdir(mode=0o700,parents=True,exist_ok=True)
 info=path.lstat()
 if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
  raise ValueError('daily_state_directory_invalid')

def read_state(path):
 try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 except FileNotFoundError:return {}
 with os.fdopen(fd) as f:
  info=os.fstat(f.fileno())
  if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>65536:
   raise ValueError('daily_state_invalid')
  try:value=json.load(f)
  except (ValueError,UnicodeError):raise ValueError('daily_state_invalid') from None
 if not isinstance(value,dict):raise ValueError('daily_state_invalid')
 return value

def save_state(path,value):
 fd,name=tempfile.mkstemp(prefix='.state-',dir=path.parent)
 try:
  with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
  os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)

def verify_release():
 manifest_path=ROOT/'dashboard-refresh-release.json'
 if manifest_path.is_symlink():raise ValueError('daily_release_manifest_invalid')
 try:manifest=json.loads(manifest_path.read_text())
 except (OSError,ValueError):raise ValueError('daily_release_manifest_missing') from None
 files=manifest.get('files');version=manifest.get('version')
 if not isinstance(files,dict) or not files or not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',version):
  raise ValueError('daily_release_manifest_invalid')
 expected_scripts={f'scripts/{name}' for name in ('dashboard_refresh_daily.py','dashboard_refresh_sync.py','dashboard_refresh_health.py','dashboard_private_session.py','confirmed_dividend_basis.py','part4_daily_sync.py','part4_official_announcement_sync.py','personal_market_snapshot_sync.py','dashboard_data_sources.py','dashboard_technical_indicators.py','personal_technical_snapshot_sync.py','personal_public_forward_sync.py','personal_dividend_refresh_sync.py','public_forward_basis.py','forward_dividend_basis.py','personal_news_sync.py','personal_recommendation_eval_sync.py','plus_strategy_worker.py','update_market.py','update_news.py','process_trade_records.py')}
 if not expected_scripts<=set(files):raise ValueError('daily_release_coverage_incomplete')
 for relative,digest in files.items():
  path=Path(relative)
  if path.is_absolute() or '..' in path.parts or not re.fullmatch(r'[0-9a-f]{64}',str(digest)):
   raise ValueError('daily_release_manifest_invalid')
  actual=ROOT/path
  if actual.is_symlink() or not actual.is_file() or hashlib.sha256(actual.read_bytes()).hexdigest()!=digest:
   raise ValueError('daily_release_hash_mismatch')
 return version

def run_once(*,state_dir=None,refresh_fn=None,publish=False,automatic=False,now=None,release_check=None):
 now=(now or datetime.now(BJ)).astimezone(BJ)
 release=(release_check or verify_release)() if automatic and publish else 'explicit_manual'
 if not publish:
  if refresh_fn is None:
   from dashboard_refresh_sync import run_refresh
   refresh_fn=run_refresh
  return refresh_fn(False)
 state_dir=Path(state_dir or RUNTIME/'daily-refresh-v2');private_dir(state_dir)
 fd=os.open(state_dir/'run.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
 try:
  info=os.fstat(fd)
  if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError('daily_lock_invalid')
  try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return {'status':'skipped','category':'already_running','published':False}
  path=state_dir/'state.json';state=read_state(path)
  reason=due_reason(now,state) if automatic else None
  if reason and state.get('lastStatus')=='running':
   # The exclusive lock is free, so the old attempt is no longer executing.
   # Do not replay any writers or mistake the legacy previous-day result for
   # this run's checkpoints. Hosted reconciliation remains a separate action.
   prior=state.get('result',{}) if state.get('checkpointVersion')==1 else {}
   stages=prior.get('stages',{}) if isinstance(prior,dict) else {}
   stages={k:(v if v.get('status') in ('ok','error') else
               {'status':'error' if v.get('status')=='running' else 'skipped','category':'refresh_interrupted','published':False})
           for k,v in stages.items() if k in STAGES and isinstance(v,dict)} if isinstance(stages,dict) else {}
   result={'status':'error','category':'daily_previous_attempt_interrupted','published':False,'stages':stages}
   state.update(lastStatus='error',finishedAt=None,interruptedDetectedAt=now.isoformat(timespec='seconds'),result=result)
   save_state(path,state)
   return result
  if reason:return {'status':'skipped','category':reason,'published':False,'date':now.date().isoformat()}
  state={**state,'attemptedDate':target_slot(now),'startedAt':now.isoformat(timespec='seconds'),'sourceRelease':release,'lastStatus':'running',
         'finishedAt':None,'checkpointVersion':1,'result':{'status':'running','published':False,
         'stages':{k:{'status':'pending','category':'pending','published':False} for k in STAGES}}}
  state.pop('interruptedDetectedAt',None)
  save_state(path,state)
  if refresh_fn is None:
   from dashboard_refresh_sync import run_refresh
   from dashboard_refresh_health import HealthReporter,current_release
   reporter=HealthReporter(release=current_release(ROOT),now=now)
   def checkpoint(name,value):
    state['result']['stages'][name]=dict(value)
    save_state(path,state)
   refresh_fn=lambda publish:run_refresh(publish,reporter,checkpoint)
  try:result=refresh_fn(True)
  except Exception:result={'status':'error','category':'refresh_exception','published':False,'stages':{}}
  if not isinstance(result,dict):result={'status':'error','category':'refresh_result_invalid','published':False,'stages':{}}
  stages=result.get('stages',{})
  ok=(result.get('status')=='ok' and result.get('published') is True and isinstance(stages,dict)
      and set(stages)==set(STAGES) and all(isinstance(stages[k],dict) and stages[k].get('status')=='ok' and stages[k].get('published') is True for k in STAGES))
  if not ok:result={**result,'status':'error','published':False}
  state.update(lastStatus='ok' if ok else 'error',finishedAt=datetime.now(BJ).isoformat(timespec='seconds'),result=result)
  if ok:state['successfulDate']=target_slot(now)
  save_state(path,state)
  return result
 finally:os.close(fd)

def plist_bytes(python,entrypoint,logs,*,hour,minute,weekdays=(1,2,3,4,5)):
 entrypoint=Path(entrypoint);logs=Path(logs)
 if not entrypoint.is_absolute() or not logs.is_absolute() or not Path(python).is_absolute() or not 0<=hour<=23 or not 0<=minute<=59:
  raise ValueError('daily_plist_arguments_invalid')
 return plistlib.dumps({'Label':LABEL,'ProgramArguments':[python,str(entrypoint),'--automatic','--publish'],
  'WorkingDirectory':str(entrypoint.parent.parent),'StartCalendarInterval':[{'Weekday':d,'Hour':hour,'Minute':minute} for d in weekdays],
  'RunAtLoad':True,'Umask':0o077,'ProcessType':'Background','EnvironmentVariables':{'PATH':'/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin','PYTHONDONTWRITEBYTECODE':'1'},
  'StandardOutPath':str(logs/'stdout.log'),'StandardErrorPath':str(logs/'stderr.log')})

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--automatic',action='store_true');p.add_argument('--publish',action='store_true')
 args=p.parse_args()
 try:
  result=run_once(publish=args.publish,automatic=args.automatic);print(json.dumps(result,ensure_ascii=False));return 0 if result['status'] in ('ok','skipped') else 2
 except Exception as error:
  category=str(error) if isinstance(error,ValueError) and re.fullmatch(r'daily_[a-z_]+',str(error)) else 'daily_refresh_failed'
  print(json.dumps({'status':'error','category':category,'published':False}));return 2
if __name__=='__main__':raise SystemExit(main())
