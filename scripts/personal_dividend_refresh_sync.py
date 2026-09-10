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

def isolated_forward_records(rows,docs,stocks,as_of):
    """Never weaken report identity validation or retain a disputed numerator."""
    codes={s['code'] for s in stocks}
    if any(r.get('SECURITY_CODE') not in codes for r in rows) or any(d.get('code') not in codes for d in docs):
        raise ValueError('forward_basis_public_identity_invalid')
    try:
        return forward_basis.build_records(rows,docs,stocks,as_of),[]
    except ValueError as error:
        if str(error)!='forward_basis_public_notice_body_identity_conflict':raise
    records=[];quarantined=[]
    for stock in stocks:
        code=stock['code'];indexes=[i for i,r in enumerate(rows) if r.get('SECURITY_CODE')==code]
        try:
            batch=forward_basis.build_records([rows[i] for i in indexes],
                    [d for d in docs if d.get('code')==code],[stock],as_of)
        except ValueError as error:
            if str(error)!='forward_basis_public_notice_body_identity_conflict':raise
            # An explicit negative marker, using the Hosted nullable DTO contract.
            # No amounts, periods or source evidence from the disputed report survive.
            reason='public_report_identity_quarantined'
            batch=[{'code':code,'asOf':as_of,'amount':None,'status':'missing','reason':reason,
                    'components':{kind:{'year':None,'kind':kind,'amount':None,'status':'missing',
                      'reason':reason,'amount_scope':'unknown','published_at':None,'source':None,'evidence':[]}
                      for kind in ('annual','interim')}}]
            quarantined.append(code)
        # Restore table provenance to the ORIGINAL complete input, not the subset.
        seen_sources=set()
        for record in batch:
            for component in record.get('components',{}).values():
                for source in [component.get('source'),*component.get('evidence',[])]:
                    if not isinstance(source,dict) or id(source) in seen_sources:continue
                    seen_sources.add(id(source))
                    if source.get('field',{}).get('provider')!='eastmoney_public_dividend_table':continue
                    index=source.get('row_index')
                    if type(index) is not int or not 0<=index<len(indexes) or source['field'].get('raw')!=rows[indexes[index]]:
                        raise ValueError('dividend_source_index_invalid')
                    source['row_index']=indexes[index]
        records.extend(batch)
    return records,quarantined

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
    records,quarantined=isolated_forward_records(rows,docs,stocks,as_of)
    actual_asof=actual_meta['asOf']
    actual=confirmed_basis.build_confirmed_records(actual_rows,stocks,actual_asof,
            coverage=actual_meta.get('confirmedCoverage'),notices=actual_docs,payment_rows=actual_meta.get('paymentRows',[]))
    ready_cash={r['code'] for r in actual if r.get('status')=='ready'}
    if any(code not in ready_cash for code in quarantined):raise ValueError('dividend_quarantine_without_confirmed_cash')
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
    return {'status':'ok','category':'fallback_used' if quarantined else 'complete','quarantinedCount':len(quarantined),'published':publish,'readbackVerified':publish,'coverageComplete':True,'watchlistCount':len(codes),
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
