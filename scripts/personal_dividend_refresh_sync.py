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
    """Keep strict parsing; isolate only reviewed unusable evidence per issuer.

    A future-dated notice is not available at this as-of. Never change its
    date or reuse any of that issuer's forward numerator. The caller still
    requires independently verified cash before publishing the null marker.
    """
    reasons={'forward_basis_public_notice_body_identity_conflict':'public_report_identity_quarantined',
             'forward_basis_public_notice_future':'public_notice_after_asof'}
    codes={s['code'] for s in stocks}
    if any(r.get('SECURITY_CODE') not in codes for r in rows) or any(d.get('code') not in codes for d in docs):
        raise ValueError('forward_basis_public_identity_invalid')
    try:
        return forward_basis.build_records(rows,docs,stocks,as_of),[]
    except ValueError as error:
        if str(error) not in reasons:raise
    # A first quarantinable error must not hide later rows or reports.
    # Validate original items independently; discard these partial results.
    by_code={s['code']:s for s in stocks}
    for row in rows:
        try:
            forward_basis.build_records([row],[],[by_code[row['SECURITY_CODE']]],as_of)
        except ValueError as error:
            if str(error) not in reasons:raise
    # The full builder already validated as_of. Inspect reports directly so
    # a future table row cannot short-circuit the report's strict checks.
    today=datetime.fromisoformat(as_of.replace('Z','+00:00')).astimezone(BJ).date()
    for doc in docs:
        try:
            forward_basis.report_evidence(doc,codes,today.year,today.isoformat())
        except ValueError as error:
            if str(error) not in reasons:raise
    records=[];quarantined=[]
    for stock in stocks:
        code=stock['code'];indexes=[i for i,r in enumerate(rows) if r.get('SECURITY_CODE')==code]
        try:
            batch=forward_basis.build_records([rows[i] for i in indexes],
                    [d for d in docs if d.get('code')==code],[stock],as_of)
        except ValueError as error:
            if str(error) not in reasons:raise
            # An explicit negative marker, using the Hosted nullable DTO contract.
            # No amounts, periods or source evidence from the disputed report survive.
            reason=reasons[str(error)]
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

def source_failure(error):
    """Only acquisition/response failures qualify; never local I/O/auth/writes."""
    import subprocess
    from dashboard_refresh_health import error_category
    if isinstance(error,(TimeoutError,subprocess.TimeoutExpired)):return 'timeout'
    if isinstance(error,json.JSONDecodeError):return 'invalid_data'
    raw=getattr(error,'category',str(error) if isinstance(error,ValueError) else None)
    if not isinstance(raw,str) or not re.fullmatch('[a-z_]{1,119}',raw):return None
    if raw in {'official_notice_transport_failed','official_notice_response_failed','official_notice_response_shape_invalid'}:
        return 'provider_failed'
    # Exact reviewed availability/response/coverage failures only. Categories
    # that also encode identity/date/title conflicts (e.g. notice_incomplete,
    # notice_pages_invalid, index_invalid, coverage_incomplete) stay strict.
    if raw in {
        'dividend_forward_scan_incomplete','dividend_actual_scan_incomplete',
        'forward_basis_public_provider_failed','forward_basis_public_pagination_invalid',
        'forward_basis_public_page_incomplete','forward_basis_public_index_metadata_invalid',
        'forward_basis_public_index_page_incomplete','forward_basis_public_report_coverage_incomplete',
        'forward_basis_public_annual_coverage_incomplete','confirmed_public_provider_failed',
        'confirmed_public_pagination_incomplete','confirmed_public_payment_source_invalid',
        'confirmed_public_payment_pagination_incomplete','confirmed_public_index_incomplete',
        'confirmed_public_index_window_incomplete',
    }:
        return error_category(raw)
    return None


def retained_publication(value, as_of):
    """Hosted source dates may be null, a date, or an aware publication time."""
    if value is None:return
    try:
        if not isinstance(value,str):raise ValueError()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
            if forward_basis.iso_day(value)>as_of.astimezone(BJ).date().isoformat():raise ValueError()
        else:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})',value):raise ValueError()
            stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
            if stamp>as_of:raise ValueError()
    except (TypeError,ValueError):raise ValueError('dividend_retained_time_invalid') from None


def retained_source(source, stock, as_of):
    """Validate saved source DTOs, including evidence copies; no source refetch."""
    keys={'dto_title','header','raw_plan','raw_progress','raw_pretax','dto_code','field','name_map',
          'published_at','normalization_reason','official_notice','scope_evidence','row_index','distribution_dates'}
    if (not isinstance(source,dict) or set(source)!=keys or
        any(not isinstance(source[k],str) for k in ('header','raw_plan','raw_progress','scope_evidence')) or
        type(source['row_index']) is not int or source['row_index']<0 or
        not isinstance(source['field'],dict) or not isinstance(source['name_map'],dict) or
        not isinstance(source['distribution_dates'],dict) or
        set(source['distribution_dates'])!={'registration','ex_dividend','payment'}):
        raise ValueError('dividend_retained_basis_invalid')
    # Legacy DTOs may identify by title/name instead of dto_code (or use .SH).
    labels=str(source['dto_title'] or '')+' '+str(source['dto_code'] or '')
    codes=set(re.findall(r'(?<!\d)\d{6}(?!\d)',labels))
    if codes-{stock['code']} or (stock['code'] not in codes and not (stock.get('name') and stock['name'] in labels)):
        raise ValueError('dividend_retained_basis_invalid')
    # Evidence may preserve other dated/undated versions and source annotations.
    # Validate each source against the saved as-of, not the selected version.
    retained_publication(source['published_at'],as_of)
    for value in source['distribution_dates'].values():
        if value is not None:
            try:
                if forward_basis.iso_day(value)!=value:raise ValueError()
            except ValueError:raise ValueError('dividend_retained_basis_invalid') from None


def retained_basis(adapter,session,stocks,moment,category):
    """Verify existing authenticated values without loading a writer or retimestamping.

    This is degraded acquisition, NOT a successful publication. The existing
    Part3 projector and trading freshness gates still decide downstream use.
    """
    import math
    back=adapter.private_rpc(*session,'personal_get_part4_v4',{})
    codes={s['code'] for s in stocks}
    saved=back.get('stocks') if isinstance(back,dict) else None
    if (not isinstance(saved,list) or len(saved)!=len(codes) or
        any(not isinstance(s,dict) for s in saved) or {s.get('code') for s in saved}!=codes):
        raise ValueError('dividend_retained_coverage_invalid')
    stamps=[]
    for stock in saved:
        manual=stock.get('forwardBasisOverride')
        forward=stock.get('forwardBasis') or {}
        actual=stock.get('confirmedBasis') or {}
        basis=({'amount':manual,'asOf':stock.get('forwardOverrideAsOf')} if manual is not None else
               forward if isinstance(forward,dict) and forward.get('status')=='ready' else
               actual if isinstance(actual,dict) and actual.get('status')=='ready' else {})
        amount=basis.get('amount')
        if isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount) or not 0<=amount<1000000:
            raise ValueError('dividend_retained_basis_invalid')
        stamp=basis.get('asOf')
        try:
            if not isinstance(stamp,str):raise ValueError()
            parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if parsed.tzinfo is None or parsed>moment:raise ValueError()
        except (AttributeError,TypeError,ValueError):raise ValueError('dividend_retained_time_invalid') from None
        # The getter is backed by strict narrow writers; also reject corrupt
        # ready totals/components rather than accepting a bare numeric value.
        if manual is None:
            from decimal import Decimal
            parts=basis.get('components')
            if basis.get('reason') is not None or 'reason' not in basis:
                raise ValueError('dividend_retained_basis_invalid')
            if basis is forward:
                if not isinstance(parts,dict) or set(parts)!={'annual','interim'}:
                    raise ValueError('dividend_retained_basis_invalid')
                fields={'year','kind','amount','status','reason','amount_scope','published_at','source','evidence'}
                identity=next(s for s in stocks if s['code']==stock['code'])
                for kind,part in parts.items():
                    if (not isinstance(part,dict) or set(part)!=fields or part['kind']!=kind or
                        type(part['year']) is not int or not 1900<=part['year']<=parsed.astimezone(BJ).year or
                        part['status'] not in {'implemented','announced','no_distribution'} or
                        part['reason'] is not None or part['amount_scope']!='distribution' or
                        type(part['amount']) not in (int,float) or forward_basis.finite(part['amount']) is None or
                        (part['amount']!=0 if part['status']=='no_distribution' else part['amount']<=0) or
                        not isinstance(part['evidence'],list) or not 1<=len(part['evidence'])<=200):
                        raise ValueError('dividend_retained_basis_invalid')
                    retained_publication(part['published_at'],parsed)
                    for source in [part['source'],*part['evidence']]:
                        retained_source(source,identity,parsed)
                parts=list(parts.values())
            else:
                from calendar import monthrange
                end=parsed.astimezone(BJ).date()
                if (not isinstance(parts,list) or len(parts)>100 or basis.get('source')!=confirmed_basis.SOURCE or end.year<2):
                    raise ValueError('dividend_retained_basis_invalid')
                start=end.replace(year=end.year-1,day=min(end.day,monthrange(end.year-1,end.month)[1]))
                # Check the ORIGINAL paid-cash window, never roll old cash to now.
                if basis.get('windowStart')!=start.isoformat() or basis.get('windowEnd')!=end.isoformat():
                    raise ValueError('dividend_retained_basis_invalid')
                seen=set();code=re.escape(stock['code'])
                for part in parts:
                    if (not isinstance(part,dict) or set(part)!={'reportDate','paymentDate','amount','sourceUrl','plan'} or
                        type(part['amount']) not in (int,float) or forward_basis.finite(part['amount']) is None or part['amount']<=0 or
                        not isinstance(part['plan'],str) or not 1<=len(part['plan'])<=1200 or
                        not isinstance(part['sourceUrl'],str) or len(part['sourceUrl'])>300 or
                        not re.fullmatch(r'https://data\.eastmoney\.com/(?:notices/detail/'+code+r'/AN\d+|yjfp/detail/'+code+r')\.html',part['sourceUrl'])):
                        raise ValueError('dividend_retained_basis_invalid')
                    try:
                        report=forward_basis.iso_day(part['reportDate']);paid=forward_basis.iso_day(part['paymentDate'])
                    except ValueError:raise ValueError('dividend_retained_basis_invalid') from None
                    if (report is None or paid is None or report!=part['reportDate'] or paid!=part['paymentDate'] or
                        not '1900-01-01'<=report<=paid or not start.isoformat()<paid<=end.isoformat() or
                        (report,paid) in seen):
                        raise ValueError('dividend_retained_basis_invalid')
                    seen.add((report,paid))
            if any(not isinstance(p,dict) or type(p.get('amount')) not in (int,float) or
                   not math.isfinite(p['amount']) or not 0<=p['amount']<1000000 for p in parts):
                raise ValueError('dividend_retained_basis_invalid')
            if sum((Decimal(str(p['amount'])) for p in parts),Decimal(0))!=Decimal(str(amount)):
                raise ValueError('dividend_retained_basis_invalid')
        stamps.append((parsed,stamp))
    return {'status':'skipped','category':category,
            'published':False,'retainedPrevious':True,'readbackVerified':True,
            'watchlistCount':len(codes),'usableCount':len(codes),'retainedAsOf':min(stamps)[1],
            'source':'eastmoney_public','mxInvoked':False,'aiInvoked':False,'private_payload_not_emitted':True}


def sync(*,adapter=None,publish=False,now=None,collect_forward=None,collect_actual=None,evidence_root=None,retain_previous=False):
    if adapter is None:
        import part4_official_announcement_sync as adapter
    worker,config,token=adapter.load_private_session()
    stocks=[{'code':s.code,'name':s.name} for s in adapter.private_watchlist(worker,config,token)]
    moment=now or datetime.now(BJ)
    try:
        rows,docs,meta=(collect_forward or collector.collect)(stocks,moment)
        if not isinstance(meta,dict) or meta.get('coverageComplete') is not True:raise ValueError('dividend_forward_scan_incomplete')
        actual_rows,actual_docs,actual_meta=(collect_actual or collector.collect_confirmed)(stocks,moment)
        if not isinstance(actual_meta,dict) or not isinstance(actual_meta.get('confirmedCoverage'),dict) or actual_meta['confirmedCoverage'].get('complete') is not True:
            raise ValueError('dividend_actual_scan_incomplete')
    except Exception as error:
        category=source_failure(error)
        if not retain_previous or category is None:raise
        return retained_basis(adapter,(worker,config,token),stocks,moment,category)
    # One observation boundary for both scans, even if collection crosses midnight.
    as_of=moment.isoformat(timespec='seconds')
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
            observed={s.get('code'):s.get(field) for s in back['stocks']}
            canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
            if canonical(observed)!=canonical(expected):raise ValueError('dividend_readback_mismatch')
    fc=dict(Counter(r['status'] for r in records));ac=dict(Counter(r['status'] for r in actual))
    usable={r['code'] for r in [*records,*actual] if r.get('status')=='ready'}
    return {'status':'ok','category':'fallback_used' if quarantined else 'complete','quarantinedCount':len(quarantined),'published':publish,'readbackVerified':publish,'coverageComplete':True,'watchlistCount':len(codes),
      'readyCount':fc.get('ready',0),'statusCounts':fc,'confirmedReadyCount':ac.get('ready',0),'confirmedStatusCounts':ac,
      'usableCount':len(usable),'asOf':as_of,'confirmedAsOf':actual_asof,'source':'eastmoney_public',
      'mxInvoked':False,'aiInvoked':False,'private_payload_not_emitted':True}

def main():
    p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group()
    g.add_argument('--publish',action='store_true');g.add_argument('--dry-run',action='store_true');args=p.parse_args()
    try:
        result=sync(publish=args.publish,retain_previous=True)
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as error:
        raw=getattr(error,'category',str(error) if isinstance(error,ValueError) else '')
        category=raw if isinstance(raw,str) and re.fullmatch(r'(?:dividend|confirmed|forward_basis|data|hithink|worker|private|auth|hosted)_[a-z_]{1,100}',raw) else 'dividend_refresh_failed'
        print(json.dumps({'status':'error','category':category,'published':False,'private_payload_not_emitted':True}));return 2
if __name__=='__main__':raise SystemExit(main())
