#!/usr/bin/env python3
"""Publish a root-produced read model only; no quote, broker or trade calls."""
from __future__ import annotations
import json
import os
import stat
from pathlib import Path
from datetime import datetime, timezone


def read_snapshot(path, *, expected_owner=0, now=None):
    path=Path(path)
    if any(p.is_symlink() for p in path.parents):
        raise ValueError('part0_spool_invalid')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        info=os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=expected_owner or stat.S_IMODE(info.st_mode)!=0o640 or info.st_nlink!=1 or info.st_size>200000:
            raise ValueError('part0_spool_invalid')
        raw=f.read(200001)
    p=json.loads(raw)
    if not isinstance(p,dict) or set(p)!={'schemaVersion','observedAt','account','trades','runtime','events'} or p['schemaVersion']!=1:
        raise ValueError('part0_spool_invalid')
    at=datetime.fromisoformat(p['observedAt'].replace('Z','+00:00'))
    if at.tzinfo is None:
        raise ValueError('part0_spool_time_invalid')
    age=((now or datetime.now(timezone.utc))-at).total_seconds()
    if not -60 <= age <= 1800:
        raise ValueError('part0_spool_stale')
    return p


def sync(path, *, publish=False, adapter=None, expected_owner=0):
    payload=read_snapshot(path,expected_owner=expected_owner)
    if not publish:
        return {'status':'audit_ok','published':False,'providerCalls':0}
    if adapter is None:
        import part4_official_announcement_sync as adapter
    # Reuse the short shared auth-session lock inside load_private_session.
    # This avoids refresh-token races with the separately scheduled daily worker.
    worker,config,token=adapter.load_private_session()
    secret=adapter.part4_writer_secret(worker,config)
    result=adapter.private_rpc(worker,config,token,'personal_sync_part0_monitor',
                               {'p_payload':payload,'p_writer_secret':secret})
    if not isinstance(result,dict) or result.get('stored') is not True:
        raise ValueError('part0_write_unconfirmed')
    back=adapter.private_rpc(worker,config,token,'personal_get_part0_monitor',{})
    if back!=payload:
        raise ValueError('part0_readback_mismatch')
    return {'status':'ok','published':True,'readbackVerified':True,
            'observedAt':payload['observedAt'],'providerCalls':0,'tradingWrites':0}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True)
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    try:
        print(json.dumps(sync(args.input,publish=args.publish)))
    except Exception:
        print(json.dumps({'status':'error','category':'part0_publication_unconfirmed','privatePayloadNotEmitted':True}))
        raise SystemExit(2)
