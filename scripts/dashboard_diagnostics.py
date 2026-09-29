"""Optional bounded private JSONL diagnostics; never change business stdout."""
import fcntl,json,os,re,stat,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path

EVENTS={'notice_page_start','notice_page_end','notice_page_error','http_start','http_end','http_error','stage_start','stage_end','stage_error'}
TEXT={'stage':r'[a-z_]{1,40}','code':r'[0-9]{6}','window_start':r'\d{4}-\d{2}-\d{2}','window_end':r'\d{4}-\d{2}-\d{2}','host':r'[a-z0-9.-]{1,100}','request_id':r'[a-f0-9]{32}'}
NUMBERS={'page','attempt','elapsed_ms','rows','total_hits','returncode'}

def emit(event,**fields):
    root=os.environ.get('DASHBOARD_DIAGNOSTICS_DIR')
    if not root:return
    try:
        if event not in EVENTS:raise ValueError()
        now=datetime.now(timezone.utc);value={'event':event,'at':now.isoformat(),'pid':os.getpid()}
        stage=os.environ.get('DASHBOARD_DIAGNOSTICS_STAGE')
        if stage:fields.setdefault('stage',stage)
        for key,item in fields.items():
            if item is None:continue
            if key in TEXT and isinstance(item,str) and re.fullmatch(TEXT[key],item):value[key]=item
            elif key in NUMBERS and type(item) is int and 0<=item<=10**12:value[key]=item
            else:raise ValueError()
        directory=Path(root)
        if not directory.is_absolute() or any(p.is_symlink() for p in (directory,*directory.parents)):raise ValueError()
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=directory.stat()
        if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError()
        path=directory/(now.strftime('%Y-%m-%d')+'.jsonl')
        fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        with os.fdopen(fd,'a') as f:
            info=os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError()
            fcntl.flock(f,fcntl.LOCK_EX)
            if info.st_size>=16*1024*1024:raise ValueError()
            f.write(json.dumps(value,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
        cutoff=(now-timedelta(days=30)).strftime('%Y-%m-%d')
        for old in directory.glob('????-??-??.jsonl'):
            if old.stem<cutoff and not old.is_symlink() and old.is_file():old.unlink()
    except Exception:
        # Diagnostics failure is visible, but does not replay/abort a business write.
        print('dashboard_diagnostics_unavailable',file=sys.stderr,flush=True)
