"""Shared short auth critical section; no tokens in files or argv."""
import os,stat,time,fcntl
from pathlib import Path
from contextlib import contextmanager
@contextmanager
def session_lock(root=None,timeout=75):
    root=Path(root or Path.home()/'.hermes/workspace/stock-dashboard-private-runtime')
    if any(p.is_symlink() for p in (root,*root.parents)):raise ValueError('session_lock_invalid')
    root.mkdir(parents=True,mode=0o700,exist_ok=True)
    info=root.stat()
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('session_lock_invalid')
    fd=os.open(root/'auth-session.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError('session_lock_invalid')
        deadline=time.monotonic()+timeout
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise ValueError('session_lock_timeout') from None
                time.sleep(0.1)
        yield
    finally:os.close(fd)
