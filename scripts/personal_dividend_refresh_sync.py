#!/usr/bin/env python3
"""Collect forward + implemented cash independently before guarded publication.

Default is dry-run. Unknown forward is never backfilled with actual cash: the
separate confirmedBasis is selected and labelled by the UI. No AI/MX or trades.
"""
from __future__ import annotations
import argparse,json,os,re,stat,tempfile
from pathlib import Path
from datetime import datetime
from collections import Counter
from zoneinfo import ZoneInfo
import personal_public_forward_sync as collector
import public_forward_basis as forward_basis
import confirmed_dividend_basis as confirmed_basis
BJ=ZoneInfo('Asia/Shanghai')

def sync(*,adapter=None,publish=False,now=None,collect_forward=None,collect_actual=None,evidence_root=None):
    if adapter is None:
        import part4_official_announcement_sync as adapter
    worker,config,token=adapter.load_private_session()
    stocks=[{'code':s.code,'name':s.name} for s in adapter.private_watchlist(worker,config,token)]
    moment=now or datetime.now(BJ)
    rows,docs,meta=(collect_forward or collector.collect)(stocks,moment)
    if not isinstance(meta,dict) or meta.get('coverageComplete') is not True:raise ValueError('dividend_forward_scan_incomplete')
    actual_rows,actual_docs,actual_meta=(collect_actual or collector.collect_confirmed)(stocks,now or datetime.now(BJ))
    if not isinstance(actual_meta,dict) or not isinstance(actual_meta.get('confirmedCoverage'),dict) or actual_meta['confirmedCoverage'].get('complete') is not True:
        raise ValueError('dividend_actual_scan_incomplete')
    as_of=(now or datetime.now(BJ)).isoformat(timespec='seconds')
    records=forward_basis.build_records(rows,docs,stocks,as_of)
    actual_asof=actual_meta['asOf']
    actual=confirmed_basis.build_confirmed_records(actual_rows,stocks,actual_asof,
            coverage=actual_meta.get('confirmedCoverage'),notices=actual_docs,payment_rows=actual_meta.get('paymentRows',[]))
    codes={s['code'] for s in stocks}
    if any(len(batch)!=len(codes) or {r.get('code') for r in batch}!=codes for batch in (records,actual)):
        raise ValueError('dividend_coverage_incomplete')
    root=Path(evidence_root or Path.home()/'.hermes/workspace/stock-dashboard-private-runtime')
    if any(p.is_symlink() for p in (root,*root.parents)):raise ValueError('dividend_evidence_path_invalid')
    root.mkdir(parents=True,mode=0o700,exist_ok=True)
    info=root.stat()
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('dividend_evidence_path_invalid')
    folder=Path(tempfile.mkdtemp(prefix='confirmed-dividend-',dir=root))
    fd=os.open(folder/'normalized-inputs.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump({'rows':actual_rows,'documents':actual_docs,'metadata':actual_meta,'records':actual},f,ensure_ascii=False,allow_nan=False)
    if publish:
        secret=adapter.part4_writer_secret(worker,config)
        for name,stamp,batch in [('personal_sync_forward_basis',as_of,records),('personal_sync_confirmed_dividends',actual_asof,actual)]:
            written=adapter.private_rpc(worker,config,token,name,{'p_as_of':stamp,'p_records':batch,'p_writer_secret':secret})
            if not isinstance(written,dict) or type(written.get('stored')) is not int or written['stored']!=len(codes):
                raise ValueError('dividend_write_unconfirmed')
        back=adapter.private_rpc(worker,config,token,'personal_get_part4_v4',{})
        if not isinstance(back,dict) or not isinstance(back.get('stocks'),list) or len(back['stocks'])!=len(codes):
            raise ValueError('dividend_readback_mismatch')
        for field,batch in [('forwardBasis',records),('confirmedBasis',actual)]:
            expected={r['code']:{k:v for k,v in r.items() if k!='code'} for r in batch}
            if {s.get('code'):s.get(field) for s in back['stocks']}!=expected:raise ValueError('dividend_readback_mismatch')
    fc=dict(Counter(r['status'] for r in records));ac=dict(Counter(r['status'] for r in actual))
    usable={r['code'] for r in [*records,*actual] if r.get('status')=='ready'}
    return {'status':'ok','published':publish,'readbackVerified':publish,'coverageComplete':True,'watchlistCount':len(codes),
      'readyCount':fc.get('ready',0),'statusCounts':fc,'confirmedReadyCount':ac.get('ready',0),'confirmedStatusCounts':ac,
      'usableCount':len(usable),'asOf':as_of,'confirmedAsOf':actual_asof,'source':'eastmoney_public',
      'mxInvoked':False,'aiInvoked':False,'private_payload_not_emitted':True}

def main():
    p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group()
    g.add_argument('--publish',action='store_true');g.add_argument('--dry-run',action='store_true');args=p.parse_args()
    try:print(json.dumps(sync(publish=args.publish),ensure_ascii=False));return 0
    except Exception as error:
        raw=getattr(error,'category',str(error) if isinstance(error,ValueError) else '')
        category=raw if isinstance(raw,str) and re.fullmatch(r'(?:dividend|confirmed|forward_basis|data|hithink|worker|private|auth|hosted)_[a-z_]{1,100}',raw) else 'dividend_refresh_failed'
        print(json.dumps({'status':'error','category':category,'published':False,'private_payload_not_emitted':True}));return 2
if __name__=='__main__':raise SystemExit(main())
