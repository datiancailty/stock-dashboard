#!/usr/bin/env python3
"""VPS wrapper: reuse existing business logic; isolate diagnostics and transport."""
import argparse,fcntl,gzip,hashlib,json,os,re,subprocess,sys,tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
STAGES=('notices','quotes','technical','news','forward','recommendations')
HOME=Path('/var/lib/stock-dashboard')
BJ=ZoneInfo('Asia/Shanghai')

def save(path,value,mode=0o600):
    fd,name=tempfile.mkstemp(prefix='.receipt-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:os.fchmod(f.fileno(),mode);json.dump(value,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def config():
    p=Path('/etc/stock-dashboard/config.json')
    s=p.lstat()
    if p.is_symlink() or s.st_uid!=0 or s.st_mode&0o022:raise ValueError('vps_config_invalid')
    c=json.loads(p.read_text())
    if set(c)!={'bridge_release','bridge_hashes','symbols','archive_repo'}:raise ValueError('vps_config_invalid')
    if not re.fullmatch(r'/opt/stock-sim-v31f-15m/releases/[0-9a-f]{64}',c['bridge_release']):raise ValueError('bridge_path_invalid')
    required={'local/display_yield_sync.py','local/display_yield_export.py','ops/publish_display_yield.py','vps/v31f_15m/__init__.py','vps/v31f_15m/contracts.py','vps/v31f_15m/direct_display_yield.py','vps/v31f_15m/part3_dividends.py'}
    if not isinstance(c['bridge_hashes'],dict) or set(c['bridge_hashes'])!=required or any(not isinstance(d,str) or not re.fullmatch('[a-f0-9]{64}',d) for d in c['bridge_hashes'].values()):raise ValueError('bridge_hash_coverage_invalid')
    return c

def verify_sync(daily,health,release,day):
    stages=daily.get('stages',{})
    return (daily.get('status')=='ok' and daily.get('published') is True and daily.get('healthPublished') is True
        and set(stages)==set(STAGES) and all(stages[k].get('status')=='ok' and stages[k].get('published') is True and stages[k].get('readbackVerified') is True for k in STAGES)
        and health.get('status')=='ok' and health.get('sourceRelease')==release and health.get('targetDate')==day)

def bridge_module(c):
    r=ROOT/'bridge'
    for name,digest in c['bridge_hashes'].items():
        if name.startswith('ops/'):continue
        p=r/name
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('bridge_source_changed')
    sys.path.insert(0,str(r/'local'));sys.path.insert(0,str(r/'vps'))
    import display_yield_sync
    return display_yield_sync

def daily():
    from dashboard_refresh_daily import run_once
    # Original attempt lock/date guard remains authoritative. Never clear it.
    run_once(publish=True,automatic=True)
    return verify()

def state_digest(state):
    return hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def verify():
    from dashboard_refresh_daily import target_slot,verify_release,read_state
    import part4_official_announcement_sync as a
    c=config();release=verify_release();day=target_slot(datetime.now(BJ))
    state=read_state(HOME/'.hermes/workspace/stock-dashboard-private-runtime/daily-refresh-v2/state.json')
    result=state.get('result',{})
    receipt={'schemaVersion':1,'release':release,'targetDate':day,'checkedAt':datetime.now(BJ).isoformat(),'dailyStatus':state.get('lastStatus'),'dailyStateSha256':state_digest(state),'syncVerified':False,'part3Prepared':False}
    try:
        w,cfg,t=a.load_private_session();health=a.private_rpc(w,cfg,t,'personal_get_refresh_health',{})
        receipt['syncVerified']=(state.get('attemptedDate')==day and state.get('sourceRelease')==release and state.get('lastStatus')=='ok' and verify_sync(result,health,release,day))
        receipt['stageReadbacks']={k:result.get('stages',{}).get(k,{}).get('readbackVerified') is True for k in STAGES}
    except Exception:receipt['syncError']='readback_failed'
    # Preserve the former hook's independent Part3 projection even if a stage failed.
    try:
        projected=prepare_bridge(c)
        receipt['part3Prepared']=projected.get('ok') is True
        receipt['part3PayloadSha256']=projected.get('payload_sha256')
    except Exception:receipt['part3Error']='projection_failed'
    save(HOME/'sync-receipt.json',receipt);print(json.dumps(receipt))
    return 0 if receipt['syncVerified'] and receipt['part3Prepared'] else 2

def prepare_bridge(c):
    bridge=bridge_module(c)
    def spool(content,_):
        value=json.loads(content)
        save(HOME/'part3-pending.json',value)
        return {'ok':True,'payload_sha256':value['sha256'],'symbol_count':len(c['symbols']),'arm_modified':False}
    return bridge.sync({'dashboard_root':str(ROOT),'symbols':c['symbols'],'state_dir':str(HOME/'part3-evidence'),'remote_release':c['bridge_release'],'retention_policy':'last_successful_saved_display'},send=spool)

def project():
    result=prepare_bridge(config());print(json.dumps({'ok':result.get('ok') is True,'projectionOnly':True}));return 0

def read_spool(path,uid):
    import stat
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=uid or s.st_mode&0o077 or s.st_nlink!=1 or s.st_size>1024*1024:raise ValueError('mirror_spool_invalid')
        data=f.read(1024*1024+1)
    if len(data)>1024*1024:raise ValueError('mirror_spool_invalid')
    return data

def mirror():
    if os.geteuid()!=0:raise ValueError('mirror_requires_root')
    c=config()
    for name,digest in c['bridge_hashes'].items():
        original=Path(c['bridge_release'])/name
        for part in (original,*original.parents):
            s=part.lstat()
            if s.st_uid!=0 or (not part.is_symlink() and s.st_mode&0o022):raise ValueError('original_bridge_permissions_invalid')
        resolved=original.resolve(strict=True)
        expected=Path('/opt/stock-sim-v31f-weekly')/Path(c['bridge_release']).name/'source'/name
        if resolved!=expected:raise ValueError('original_bridge_target_invalid')
        for part in (resolved,*resolved.parents):
            s=part.lstat()
            if part.is_symlink() or s.st_uid!=0 or s.st_mode&0o022:raise ValueError('original_bridge_target_permissions_invalid')
        if original.is_symlink() or hashlib.sha256(original.read_bytes()).hexdigest()!=digest:raise ValueError('original_bridge_changed')
    pending=HOME/'part3-pending.json'
    import pwd
    content=read_spool(pending,pwd.getpwnam('dashboard').pw_uid)
    # Existing receiver validates frozen universe, payload, age, permission and readback.
    r=Path(c['bridge_release'])
    env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(r/'vps'),'PYTHONDONTWRITEBYTECODE':'1'}
    out=subprocess.run([str(r/'venv/bin/python'),str(r/'ops/publish_display_yield.py')],input=content,capture_output=True,env=env,timeout=90)
    value=json.loads(out.stdout)
    if out.returncode or value.get('ok') is not True or value.get('arm_modified') is not False:raise ValueError('mirror_publish_failed')
    expected=json.loads(content)['sha256']
    if value.get('payload_sha256')!=expected or value.get('symbol_count')!=len(c['symbols']):raise ValueError('mirror_readback_failed')
    value['checkedAt']=datetime.now(BJ).isoformat();save(HOME/'mirror-receipt.json',value,mode=0o644)
    print(json.dumps({'ok':True,'symbolCount':value['symbol_count'],'armModified':False}))
    return 0

def sanitize_log(value):
    from dashboard_diagnostics import EVENTS,TEXT,NUMBERS
    allowed={'event','at','pid'}|set(TEXT)|NUMBERS
    if not isinstance(value,dict) or set(value)-allowed or value.get('event') not in EVENTS:raise ValueError('archive_log_invalid')
    if not isinstance(value.get('at'),str) or not re.fullmatch(r'[0-9T:.+Z-]{20,40}',value['at']):raise ValueError('archive_log_invalid')
    datetime.fromisoformat(value['at'])
    if type(value.get('pid')) is not int or value['pid']<=0:raise ValueError('archive_log_invalid')
    for k,v in value.items():
        if k in TEXT and (not isinstance(v,str) or not re.fullmatch(TEXT[k],v)):raise ValueError('archive_log_invalid')
        if k in NUMBERS and (type(v) is not int or not 0<=v<=10**12):raise ValueError('archive_log_invalid')
    # Cloud diagnostics never disclose watchlist membership.
    return {k:v for k,v in value.items() if k!='code'}

def current_status(receipt,state,mirror,now,release):
    from dashboard_refresh_daily import target_slot
    day=target_slot(now)
    current=(state.get('attemptedDate')==day and state.get('sourceRelease')==release
             and receipt.get('targetDate')==day and receipt.get('release')==release
             and receipt.get('dailyStateSha256')==state_digest(state))
    result=state.get('result',{})
    good=(current and state.get('lastStatus')=='ok' and receipt.get('syncVerified') is True
          and verify_sync(result,{'status':'ok','sourceRelease':release,'targetDate':day},release,day))
    mirror_ok=False
    stamp=receipt.get('checkedAt')
    try:
        checked=datetime.fromisoformat(stamp)
        good=good and checked<=now
    except (TypeError,ValueError):good=False
    try:
        mirrored=datetime.fromisoformat(mirror.get('checkedAt',''))
        mirror_ok=(good and receipt.get('part3Prepared') is True and mirror.get('ok') is True
            and mirror.get('arm_modified') is False and isinstance(receipt.get('part3PayloadSha256'),str)
            and mirror.get('payload_sha256')==receipt['part3PayloadSha256'] and checked<=mirrored<=now)
    except (TypeError,ValueError):pass
    return {'archivedAt':now.isoformat(),'checkedAt':stamp if isinstance(stamp,str) and re.fullmatch(r'[0-9T:.+Z-]{20,40}',stamp) else None,
            'targetDate':day,'syncVerified':bool(good),'part3Verified':bool(mirror_ok),
            'dailyStatus':state.get('lastStatus') if state.get('attemptedDate')==day and state.get('lastStatus') in {'ok','error','running'} else 'not_observed'}

def archive():
    c=config();workspace=tempfile.TemporaryDirectory(prefix='archive-',dir=HOME);repo=Path(workspace.name)/'repo';now=datetime.now(BJ)
    env={**os.environ,'GIT_SSH_COMMAND':'ssh -i /var/lib/stock-dashboard/archive-key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/etc/stock-dashboard/github-known-hosts','GIT_TERMINAL_PROMPT':'0'}
    def git(*args):
        r=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,env=env,timeout=90)
        if r.returncode:raise ValueError('archive_git_failed')
        return r.stdout.strip()
    try:
        if not repo.exists():
            r=subprocess.run(['git','clone','--depth','1',c['archive_repo'],str(repo)],capture_output=True,env=env,timeout=90)
            if r.returncode:raise ValueError('archive_clone_failed')
        git('config','user.name','Dashboard diagnostics');git('config','user.email','dashboard-diagnostics@users.noreply.github.com')
        folder=repo/'diagnostics';folder.mkdir(exist_ok=True)
        for p in sorted((HOME/'diagnostics').glob('????-??-??.jsonl')):
            if p.is_symlink() or p.stat().st_size>17*1024*1024:raise ValueError('archive_log_invalid')
            values=[sanitize_log(json.loads(line)) for line in p.read_text().splitlines()]
            data=('\n'.join(json.dumps(v,separators=(',',':')) for v in values)+'\n').encode()
            (folder/(p.stem+'.jsonl.gz')).write_bytes(gzip.compress(data,mtime=0))
        # Only positive-schema aggregate status crosses the cloud boundary.
        receipt=json.loads((HOME/'sync-receipt.json').read_text()) if (HOME/'sync-receipt.json').exists() else {}
        mirror=json.loads((HOME/'mirror-receipt.json').read_text()) if (HOME/'mirror-receipt.json').exists() else {}
        from dashboard_refresh_daily import read_state,verify_release
        state=read_state(HOME/'.hermes/workspace/stock-dashboard-private-runtime/daily-refresh-v2/state.json')
        summary=current_status(receipt,state,mirror,now,verify_release())
        save(repo/'STATUS.json',summary)
        git('add','--','diagnostics','STATUS.json')
        if git('diff','--cached','--name-only'):git('commit','-m','Archive sanitized daily diagnostics')
        git('push','origin','HEAD:main')
        sha=git('rev-parse','HEAD');remote=git('ls-remote','origin','refs/heads/main').split()[0]
        if sha!=remote:raise ValueError('archive_readback_failed')
        out={'ok':True,'checkedAt':now.isoformat(),'remoteCommit':sha}
    except Exception:out={'ok':False,'checkedAt':now.isoformat(),'category':'archive_pending_retry'}
    finally:workspace.cleanup()
    save(HOME/'archive-receipt.json',out);print(json.dumps(out));return 0 if out['ok'] else 2

def main():
    os.umask(0o077);p=argparse.ArgumentParser();p.add_argument('operation',choices=['daily','verify','project','mirror','archive']);args=p.parse_args()
    try:
        from dashboard_refresh_daily import verify_release
        verify_release()
        return globals()[args.operation]()
    except Exception as error:
        category=str(error) if isinstance(error,ValueError) and re.fullmatch(r'(?:mirror|original|archive|bridge|daily|calendar|vps)_[a-z_]{1,100}',str(error)) else args.operation+'_failed'
        print(json.dumps({'ok':False,'category':category}));return 2
if __name__=='__main__':raise SystemExit(main())
