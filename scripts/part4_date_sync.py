"""Complete public F10 date scan for already validated implementation notices.

No account/provider credentials, orders, dividend-numerator changes or SQL.
All collection/validation finishes before the caller requests a writer capability.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode
from part4_dividend_date_materializer import materialize_calendar, preference_only_notice


def collect_dates(notices, *, fetcher=None, evidence_dir=None):
    ids=sorted({n['id'].removeprefix('eastmoney:') for n in notices
                if n.get('stage')=='implementation' and not preference_only_notice(n.get('title'))})
    if any(not re.fullmatch(r'AN[0-9]{12,32}',aid) for aid in ids):
        raise ValueError('calendar_notice_binding_invalid')
    if not ids:
        projected=materialize_calendar(notices,[])
        return {**projected,'events':[]}
    if fetcher is None:
        from part4_official_announcement_sync import safe_curl_json
        fetcher=safe_curl_json
    if evidence_dir is None:
        from tempfile import mkdtemp
        from personal_public_forward_sync import _private_evidence_root
        evidence_dir=Path(mkdtemp(prefix='calendar-dates-',dir=_private_evidence_root()))
    evidence_dir=Path(evidence_dir)
    rows=[]
    for batch_no,offset in enumerate(range(0,len(ids),40),1):
        batch=ids[offset:offset+40]
        flt='(INFO_CODE in ('+','.join('"'+aid+'"' for aid in batch)+'))'
        pages=total=None
        for page in range(1,11):
            params={'reportName':'RPT_F10_DIVIDEND_MAIN','columns':'ALL','pageNumber':page,'pageSize':100,
                    'sortColumns':'NOTICE_DATE,SECURITY_CODE','sortTypes':'-1,1','source':'HSF10','client':'PC','filter':flt}
            payload=fetcher('https://datacenter.eastmoney.com/securities/api/data/v1/get?'+urlencode(params))
            fd=os.open(evidence_dir/f'batch-{batch_no:02}-page-{page:02}.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as f:
                json.dump({'request':params,'response':payload},f,ensure_ascii=False,allow_nan=False)
            result=payload.get('result') if isinstance(payload,dict) else None
            if not isinstance(payload,dict) or payload.get('success') is not True or not isinstance(result,dict) or not isinstance(result.get('data'),list):
                raise ValueError('calendar_date_source_invalid')
            if page==1:
                pages,total=result.get('pages'),result.get('count')
            if (type(pages) is not int or not 0<=pages<=10 or type(total) is not int or not 0<=total<=1000
                    or pages!=(total+99)//100 or result.get('pages')!=pages or result.get('count')!=total
                    or len(result['data'])!=max(0,min(100,total-100*(page-1)))):
                raise ValueError('calendar_date_pagination_incomplete')
            if any(not isinstance(row,dict) or row.get('INFO_CODE') not in batch for row in result['data']):
                raise ValueError('calendar_date_identity_invalid')
            rows.extend(result['data'])
            if page>=pages:
                break
    projected=materialize_calendar(notices,rows)
    if projected['missingDates']:
        raise ValueError('calendar_dates_incomplete')
    return {**projected,'events':[e for e in projected['events'] if e['id'].startswith('eastmoney-date:')]}
