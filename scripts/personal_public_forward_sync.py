#!/usr/bin/env python3
"""Public-source forward basis, authenticated narrow private writer, no MX/AI.

Dry-run is default. Source snapshots are protected local evidence, never Git.
A failed/incomplete public scan fails before obtaining a writer capability.
"""
from __future__ import annotations
import argparse,json,os,re,hashlib,stat
from collections import Counter
from datetime import datetime,date
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import public_forward_basis as basis
BJ=ZoneInfo('Asia/Shanghai')
ROOT=Path(__file__).resolve().parents[1]
EVIDENCE_ROOT=Path.home()/'.hermes/workspace/stock-dashboard-private-runtime'

def _private_evidence_root():
 root=EVIDENCE_ROOT
 if not root.is_absolute() or any(p.is_symlink() for p in (root,*root.parents)):
  raise ValueError('forward_basis_evidence_path_invalid')
 root.mkdir(mode=0o700,exist_ok=True)
 info=root.lstat()
 if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
  raise ValueError('forward_basis_evidence_path_invalid')
 return root

def collect(stocks,now):
 started_at=datetime.now(BJ).isoformat()
 import part4_official_announcement_sync as adapter
 from tempfile import mkdtemp
 root=_private_evidence_root()
 evidence=Path(mkdtemp(prefix='public-forward-',dir=root));evidence.chmod(0o700)
 def save(name,obj):
  fd=os.open(evidence/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,'w') as f:json.dump(obj,f,ensure_ascii=False,allow_nan=False)
 codes={s['code'] for s in stocks}
 if not 1<=len(codes)<=50 or any(not re.fullmatch(r'\d{6}',c) for c in codes):raise ValueError('forward_basis_watchlist_invalid')
 # Two complete fiscal years cover the prior annual payment during Q1.
 flt='(SECURITY_CODE in ('+','.join('"'+c+'"' for c in sorted(codes))+f"))(REPORT_DATE>='{now.year-2}-01-01')"
 rows=[];pages=None;total=None;seen=set()
 for page in range(1,11):
  params={'reportName':'RPT_SHAREBONUS_DET','columns':'ALL','pageNumber':page,'pageSize':100,'sortColumns':'REPORT_DATE,SECURITY_CODE','sortTypes':'-1,1','source':'WEB','client':'WEB','filter':flt}
  payload=adapter.safe_curl_json('https://datacenter-web.eastmoney.com/api/data/v1/get?'+urlencode(params))
  save(f'dividends-{page:02}.json',{'request':params,'response':payload})
  result=payload.get('result') or {}
  if payload.get('success') is not True or not isinstance(result.get('data'),list):raise ValueError('forward_basis_public_provider_failed')
  if page==1:pages=result.get('pages');total=result.get('count')
  if (type(pages) is not int or not 1<=pages<=10 or type(total) is not int or not 1<=total<=1000
      or result.get('pages')!=pages or result.get('count')!=total):raise ValueError('forward_basis_public_pagination_invalid')
  batch=result['data']
  if len(batch)!=(100 if page<pages else total-100*(pages-1)):raise ValueError('forward_basis_public_page_incomplete')
  for row in batch:
   identity=(row.get('SECURITY_CODE'),row.get('REPORT_DATE'),row.get('NOTICE_DATE'))
   if identity in seen:raise ValueError('forward_basis_public_duplicate_row')
   seen.add(identity);rows.append(row)
  if page==pages:break
 if len(rows)!=total or {r.get('SECURITY_CODE') for r in rows}!=codes:raise ValueError('forward_basis_public_coverage_incomplete')
 # The bounded scan proves absence of newer reports; the calendar only sets
 # a search window, never a synthetic fiscal slot. Unproven coverage aborts.
 cutoff=date(now.year-2,1,1);selected=[];index_count=0;excluded_future=0;index_coverage={}
 excluded_other=[]
 for stock in stocks:
  observed=set();window_complete=False;stock_selected=[]
  summary_years=[];interim_years=[];annual_years=[]
  index_total=index_size=None;previous_date=None
  for page in range(1,21):
   data=adapter.fetch_notice_page(stock['code'],page);index_count+=1
   save(f'index-{stock["code"]}-{page:02}.json',data)
   notices=data.get('list')
   if not isinstance(notices,list):raise ValueError('forward_basis_public_index_invalid')
   size=data.get('page_size');hits=data.get('total_hits')
   if (type(size) is not int or not 1<=size<=100 or type(hits) is not int or hits<0
       or ('page_index' in data and (type(data['page_index']) is not int or data['page_index']!=page))):
    raise ValueError('forward_basis_public_index_metadata_invalid')
   if page==1:index_total,index_size=hits,size
   if (hits,size)!=(index_total,index_size) or len(notices)!=max(0,min(size,hits-size*(page-1))):
    raise ValueError('forward_basis_public_index_page_incomplete')
   dates=[]
   for note in notices:
    stamp=basis.iso_day(str(note.get('notice_date',''))[:10]);dates.append(stamp)
    if previous_date is not None and stamp>previous_date:raise ValueError('forward_basis_public_index_order_invalid')
    previous_date=stamp
    if stamp>now.date().isoformat():
     excluded_future+=1;continue
    aid=note.get('art_code','');title=note.get('title','')
    if aid in observed:raise ValueError('forward_basis_public_index_repeated')
    observed.add(aid)
    if not re.fullmatch(r'AN\d{12,32}',aid):raise ValueError('forward_basis_public_index_invalid')
    if stamp<cutoff.isoformat():continue
    text=basis.compact(title)
    summary=re.search(r'(?<!\d)(\d{4})年?(?:半年度|中期)报告摘要',text)
    interim=re.search(r'(?<!\d)(\d{4})年?(?:半年度|中期)报告',text)
    annual=re.search(r'(?<!\d)(\d{4})年?年度报告',text)
    special=re.search(r'(?<!\d)(\d{4})年特别分红方案',text)
    if summary or special or interim or annual:
     securities=note.get('codes')
     # The index can tag a parent's stock on a subsidiary's own report.
     # Exclude only when its explicit title issuer maps to another security
     # in this very index row. Never infer this from a conflicting body.
     title_parts=re.split(r'[:：]',text,maxsplit=1)
     issuer=title_parts[0] if len(title_parts)==2 else None
     other=set()
     for security in securities if isinstance(securities,list) else []:
      if not isinstance(security,dict):continue
      other_code=security.get('stock_code');name=basis.compact(security.get('short_name'))
      if (other_code==stock['code'] or not isinstance(other_code,str) or not re.fullmatch(r'\d{6}',other_code)
          or not name or name==basis.compact(stock.get('name'))):continue
      subject=rf'(?:^|关于披露){re.escape(name)}(?:股份有限公司|有限责任公司|有限公司)?\d{{4}}年?(?:半年度|中期|年度)报告(?:摘要)?(?:的公告)?$'
      if issuer==name or re.search(subject,title_parts[-1]):other.add(other_code)
     if len(other)==1:
      excluded_other.append({'code':stock['code'],'id':aid,'issuerCode':next(iter(other)),
                             'reason':'index_title_identifies_other_issuer'})
      continue
     if not isinstance(securities,list) or stock['code'] not in {s.get('stock_code') for s in securities if isinstance(s,dict)}:
      raise ValueError('forward_basis_public_index_identity_invalid')
     if interim:interim_years.append(int(interim[1]))
     if annual:annual_years.append(int(annual[1]))
     if summary:summary_years.append(int(summary[1]))
     if summary or special:
      stock_selected.append({'code':stock['code'],'id':aid,'title':title,'date':stamp,
                             'fiscalYear':int((summary or special)[1]),
                             'indexEvidence':{'page':page,'pageSize':size,'totalHits':hits,'title':title,'security':securities}})
   if (dates and min(dates)<cutoff.isoformat()) or page*size>=hits:
    window_complete=True;break
  if not window_complete or not summary_years or max(summary_years)!=max(interim_years):
   raise ValueError('forward_basis_public_report_coverage_incomplete')
  annual_table=[int(r['REPORT_DATE'][:4]) for r in rows if r['SECURITY_CODE']==stock['code'] and r['REPORT_DATE'][5:10]=='12-31']
  if not annual_table or (annual_years and max(annual_years)>max(annual_table)):
   raise ValueError('forward_basis_public_annual_coverage_incomplete')
  selected.extend(item for item in stock_selected if item['fiscalYear']==max(summary_years))
  index_coverage[stock['code']]={'from':cutoff.isoformat(),'through':now.date().isoformat(),
                                'windowComplete':True,'totalHits':index_total,'scannedPages':page,
                                'latestInterimSummaryYear':max(summary_years),
                                'latestAnnualReportYear':max(annual_years) if annual_years else None}
 reports=[]
 for item in selected:
  pieces=[];receipts=[];expected_pages=None;title=None
  for page in range(1,31):
   params={'art_code':item['id'],'client_source':'web','page_index':page}
   payload=adapter.safe_curl_json('https://np-cnotice-stock.eastmoney.com/api/content/ann?'+urlencode(params))
   save(f'notice-{item["id"]}-{page:02}.json',payload)
   data=payload.get('data') or {}
   if (payload.get('success')!=1 or data.get('art_code')!=item['id']
       or item['code'] not in {s.get('stock') for s in data.get('security',[]) if isinstance(s,dict)}
       or str(data.get('notice_date',''))[:10]!=item['date'] or not isinstance(data.get('notice_content'),str) or not data['notice_content']):
    raise ValueError('forward_basis_public_notice_invalid')
   if page==1:expected_pages=data.get('page_size');title=data.get('notice_title')
   if (type(expected_pages) is not int or not 1<=expected_pages<=30
       or type(data.get('page_size')) is not int or data['page_size']!=expected_pages
       or data.get('notice_title')!=title or basis.compact(title)!=basis.compact(item['title'])
       or ('page_index' in data and (type(data['page_index']) is not int or data['page_index']!=page))):
    raise ValueError('forward_basis_public_notice_pages_invalid')
   if data['notice_content'] in pieces:raise ValueError('forward_basis_public_notice_page_repeated')
   pieces.append(data['notice_content'])
   receipts.append({'requestedPage':page,'responsePage':data.get('page_index'),'artCode':data['art_code'],
                    'title':data['notice_title'],'date':item['date'],'security':data['security'],
                    'contentSha256':hashlib.sha256(data['notice_content'].encode()).hexdigest()})
   if page==expected_pages:break
  doc={**item,'title':title,'sourceUrl':f'https://data.eastmoney.com/notices/detail/{item["code"]}/{item["id"]}.html','pages':pieces,'pageCount':len(pieces),
       'collection':{'boundary':'eastmoney_index_and_content_response','apiTextPageCount':expected_pages,
                     'index':item['indexEvidence'],'pages':receipts}}
  # A parent-only index tag is not the issuer. Exclude only a fully fetched,
  # bound report with an explicit OTHER legal issuer in the index title AND
  # the identical cover title AND a single foreign ordinary-stock identity.
  # A generic title, wrapper announcement or contradictory cover stays in.
  text=basis.compact(''.join(pieces));parts=re.split(r'[:：]',basis.compact(title),maxsplit=1)
  stock_name=basis.compact(next(s.get('name') for s in stocks if s['code']==item['code']))
  subject=re.fullmatch(r'([^:：]+(?:股份有限公司|有限责任公司|有限公司))(\d{4}年?(?:半年度|中期)报告摘要)',parts[-1])
  body_codes=set(basis.body_stock_codes(text))
  if (len(parts)==2 and stock_name and parts[0]==stock_name and subject
      and stock_name not in subject[1] and text.startswith(subject[0])
      and len(body_codes)==1 and item['code'] not in body_codes
      and body_codes==set(basis.body_stock_codes(text[:1000]))
      and set(re.findall(r'(?<!\d)(\d{4})年?(?:半年度|中期)报告摘要',text))=={str(item['fiscalYear'])}):
   excluded_other.append({'code':item['code'],'id':item['id'],'issuerCode':next(iter(body_codes)),
                          'reason':'index_legal_title_and_body_identify_other_issuer',
                          'contentSha256':hashlib.sha256('\n'.join(pieces).encode()).hexdigest(),
                          'collection':doc['collection']})
   continue
  reports.append(doc)
 # Excluded subsidiary reports cannot establish the parent's latest-period
 # coverage or make absence of its own summary look like a completed scan.
 for code,coverage in index_coverage.items():
  if not any(d['code']==code and d['fiscalYear']==coverage['latestInterimSummaryYear']
             and re.search(r'(?:半年度|中期)报告摘要',basis.compact(d['title'])) for d in reports):
   raise ValueError('forward_basis_public_report_coverage_incomplete')
 metadata={'startedAt':started_at,'completedAt':datetime.now(BJ).isoformat(),
           'excludedOtherIssuerIndexRows':len(excluded_other),'excludedOtherIssuerNotices':excluded_other,
           'sourceCoverage':{'tableReportDateFrom':cutoff.isoformat(),'asOf':now.isoformat(),
                             'securityCount':len(codes),'dividendPagesComplete':True,'indexWindowComplete':True},
           'basisPolicy':'latest_known_annual_plus_latest_known_interim','indexCoverage':index_coverage,'source':'eastmoney_public','tableRows':len(rows),'queryBatches':pages,'indexPages':index_count,'excludedFutureIndexRows':excluded_future,'reportDocuments':len(reports),'coverageComplete':True,'evidenceDirectory':str(evidence)}
 save('normalized-inputs.json',{'rows':rows,'reports':reports,'stocks':stocks,'metadata':metadata})
 return rows,reports,metadata

def collect_confirmed(stocks,now=None,*,public_adapter=None,official_codes=('600938',)):
 """Read-only independent candidate inputs; NEVER calls auth/writers or saves files.

 Full provider distribution history, not a fiscal-date or ex-date cutoff, is
 required: an old fiscal dividend can be paid late inside the current window.
 Only requested official_codes (default: 600938 A-share currency audit) get
 bounded current-window notice scans; ordinary candidates use the two complete
 structured reports, never claim full-text verification. Partial pages abort.
 Return rows, implementation documents, metadata. The caller supplies
 metadata['confirmedCoverage'] to build_confirmed_records(); no RPC is assumed.
 """
 from confirmed_dividend_basis import SOURCE
 from calendar import monthrange
 if public_adapter is None:
  import part4_official_announcement_sync as public_adapter
 moment=now or datetime.now(BJ)
 if moment.tzinfo is None:raise ValueError('confirmed_as_of_invalid')
 moment=moment.astimezone(BJ);as_of=moment.isoformat(timespec='seconds')
 codes={s.get('code') for s in stocks}
 if not 1<=len(stocks)<=50 or len(codes)!=len(stocks) or any(not isinstance(c,str) or not re.fullmatch(r'\d{6}',c) for c in codes):
  raise ValueError('confirmed_watchlist_invalid')
 flt='(SECURITY_CODE in ('+','.join('"'+c+'"' for c in sorted(codes))+'))'
 rows=[];seen=set();pages=total=None
 for page in range(1,21):
  params={'reportName':'RPT_SHAREBONUS_DET','columns':'ALL','pageNumber':page,'pageSize':100,
          'sortColumns':'REPORT_DATE,SECURITY_CODE','sortTypes':'-1,1','source':'WEB','client':'WEB','filter':flt}
  payload=public_adapter.safe_curl_json('https://datacenter-web.eastmoney.com/api/data/v1/get?'+urlencode(params))
  result=payload.get('result') or {}
  if payload.get('success') is not True or not isinstance(result.get('data'),list):raise ValueError('confirmed_public_provider_failed')
  if page==1:pages,total=result.get('pages'),result.get('count')
  if (type(pages) is not int or not 0<=pages<=20 or type(total) is not int or not 0<=total<=2000
      or result.get('pages')!=pages or result.get('count')!=total
      or pages!=(total+99)//100 or len(result['data'])!=max(0,min(100,total-100*(page-1)))):
   raise ValueError('confirmed_public_pagination_incomplete')
  for row in result['data']:
   if not isinstance(row,dict) or row.get('SECURITY_CODE') not in codes:raise ValueError('confirmed_public_identity_invalid')
   identity=(row.get('SECURITY_CODE'),row.get('REPORT_DATE'),row.get('NOTICE_DATE'))
   if identity in seen:raise ValueError('confirmed_public_duplicate_row')
   seen.add(identity);rows.append(row)
  if page>=pages:break
 # The F10 source actually exposes PAY_CASH_DATE (派息日). It is joined
 # by A-share identity + notice date, not guessed from EX_DIVIDEND_DATE.
 end=moment.date();start=end.replace(year=end.year-1,day=min(end.day,monthrange(end.year-1,end.month)[1]))
 # PageAjax only displays a truncated recent subset. The underlying F10
 # report is paginated with a count and includes the same explicit pay field.
 payment_rows=[];cutoffs={c:start.isoformat() for c in codes};payment_seen=set()
 payment_pages=payment_total=None
 for page in range(1,21):
  params={'reportName':'RPT_F10_DIVIDEND_MAIN','columns':'ALL','pageNumber':page,'pageSize':100,
          'sortColumns':'NOTICE_DATE,SECURITY_CODE','sortTypes':'-1,1','source':'HSF10','client':'PC','filter':flt}
  payload=public_adapter.safe_curl_json('https://datacenter.eastmoney.com/securities/api/data/v1/get?'+urlencode(params))
  data=payload.get('result') or {}
  if payload.get('success') is not True or not isinstance(data.get('data'),list):raise ValueError('confirmed_public_payment_source_invalid')
  if page==1:payment_pages,payment_total=data.get('pages'),data.get('count')
  if (type(payment_pages) is not int or not 0<=payment_pages<=20 or type(payment_total) is not int or not 0<=payment_total<=2000
      or data.get('pages')!=payment_pages or data.get('count')!=payment_total or payment_pages!=(payment_total+99)//100
      or len(data['data'])!=max(0,min(100,payment_total-100*(page-1)))):
   raise ValueError('confirmed_public_payment_pagination_incomplete')
  for raw in data['data']:
   code=raw.get('SECURITY_CODE')
   market='SH' if isinstance(code,str) and code.startswith('6') else 'SZ' if isinstance(code,str) and code.startswith(('0','3')) else 'BJ'
   if code not in codes or raw.get('SECUCODE')!=code+'.'+market:raise ValueError('confirmed_public_payment_identity_invalid')
   identity=(code,raw.get('NOTICE_DATE'),raw.get('REPORT_DATE'))
   if identity in payment_seen:raise ValueError('confirmed_public_payment_duplicate_row')
   payment_seen.add(identity)
   payment_rows.append({**raw,'sourceUrl':'https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?type=web&code='+market+code})
   paid=basis.iso_day(raw.get('PAY_CASH_DATE'),optional=True)
   if paid and start.isoformat()<paid<=end.isoformat():cutoffs[code]=min(cutoffs[code],basis.iso_day(raw['NOTICE_DATE']))
  if page>=payment_pages:break
 selected=[];index_count=0
 for code in sorted(codes & set(official_codes)):
  observed=set();hits=size=None;previous=None
  for page in range(1,21):
   data=public_adapter.fetch_notice_page(code,page);index_count+=1
   notes=data.get('list')
   if page==1:hits,size=data.get('total_hits'),data.get('page_size')
   if (type(hits) is not int or hits<0 or type(size) is not int or not 1<=size<=100
       or data.get('total_hits')!=hits or data.get('page_size')!=size
       or not isinstance(notes,list) or len(notes)!=max(0,min(size,hits-size*(page-1)))
       or ('page_index' in data and (type(data['page_index']) is not int or data['page_index']!=page))):
    raise ValueError('confirmed_public_index_incomplete')
   for note in notes:
    stamp=basis.iso_day(str(note.get('notice_date',''))[:10]);aid=note.get('art_code','')
    if previous is not None and stamp>previous:raise ValueError('confirmed_public_index_order_invalid')
    previous=stamp
    if aid in observed or not re.fullmatch(r'AN\d{12,32}',aid):raise ValueError('confirmed_public_index_identity_invalid')
    observed.add(aid)
    title=basis.compact(note.get('title',''))
    if stamp>moment.date().isoformat() or stamp<cutoffs[code] or not re.search(r'(?:分派|派息|分红).*实施公告',title):continue
    securities=note.get('codes')
    if not isinstance(securities,list) or code not in {s.get('stock_code') for s in securities if isinstance(s,dict)}:
     raise ValueError('confirmed_public_index_identity_invalid')
    if re.search(r'H股公告|港股公告',title):continue
    selected.append({'code':code,'id':aid,'title':note['title'],'date':stamp,
                     'indexEvidence':{'page':page,'pageSize':size,'totalHits':hits,'title':note['title'],'security':securities}})
   if page*size>=hits or (previous is not None and previous<cutoffs[code]):break
  else:raise ValueError('confirmed_public_index_window_incomplete')
 docs=[]
 for item in selected:
  pieces=[];receipts=[];count=None
  for page in range(1,31):
   payload=public_adapter.safe_curl_json('https://np-cnotice-stock.eastmoney.com/api/content/ann?'+urlencode({'art_code':item['id'],'client_source':'web','page_index':page}))
   data=payload.get('data') or {}
   if page==1:count=data.get('page_size')
   if (payload.get('success')!=1 or data.get('art_code')!=item['id']
       or item['code'] not in {s.get('stock') for s in data.get('security',[]) if isinstance(s,dict)}
       or str(data.get('notice_date',''))[:10]!=item['date']
       or basis.compact(data.get('notice_title'))!=basis.compact(item['title'])
       or type(count) is not int or not 1<=count<=30 or type(data.get('page_size')) is not int or data['page_size']!=count
       or ('page_index' in data and (type(data['page_index']) is not int or data['page_index']!=page))
       or not isinstance(data.get('notice_content'),str) or not data['notice_content'] or data['notice_content'] in pieces):
    raise ValueError('confirmed_public_notice_incomplete')
   pieces.append(data['notice_content'])
   receipts.append({'requestedPage':page,'responsePage':data.get('page_index'),'artCode':item['id'],
                    'title':data['notice_title'],'date':item['date'],'security':data['security'],
                    'contentSha256':hashlib.sha256(data['notice_content'].encode()).hexdigest()})
   if page==count:break
  docs.append({**item,'pages':pieces,'pageCount':len(pieces),
               'sourceUrl':f'https://data.eastmoney.com/notices/detail/{item["code"]}/{item["id"]}.html',
               'collection':{'boundary':'eastmoney_index_and_content_response','apiTextPageCount':count,
                             'index':item['indexEvidence'],'pages':receipts}})
 return rows,docs,{'asOf':as_of,'source':SOURCE,'indexPages':index_count,'noticeDocuments':len(docs),
                   'paymentRows':payment_rows,'paymentPages':payment_pages,'indexWindowFrom':cutoffs,
                   'confirmedCoverage':{'complete':True,'scope':'all_distribution_history','source':SOURCE,
                                        'asOf':as_of,'codes':sorted(codes),'rowCount':len(rows)}}

def run(adapter,*,publish=False,now=None,collect_fn=None):
 worker,config,token=adapter.load_private_session()
 stocks=[{'code':s.code,'name':s.name} for s in adapter.private_watchlist(worker,config,token)]
 started=now or datetime.now(BJ)
 rows,reports,meta=(collect_fn or collect)(stocks,started)
 if not isinstance(meta,dict) or meta.get('coverageComplete') is not True:raise ValueError('forward_basis_public_coverage_incomplete')
 as_of=(now or datetime.now(BJ)).isoformat(timespec='seconds')
 records=basis.build_records(rows,reports,stocks,as_of)
 if publish:
  secret=adapter.part4_writer_secret(worker,config)
  result=adapter.private_rpc(worker,config,token,'personal_sync_forward_basis',{'p_as_of':as_of,'p_records':records,'p_writer_secret':secret})
  if not isinstance(result,dict) or type(result.get('stored')) is not int or result['stored']!=len(records):raise ValueError('forward_basis_publish_response_invalid')
  back=adapter.private_rpc(worker,config,token,'personal_get_part4_v2',{})
  actual={s.get('code'):s.get('forwardBasis') for s in back.get('stocks',[])} if isinstance(back,dict) else {}
  expected={r['code']:{k:v for k,v in r.items() if k!='code'} for r in records}
  if actual!=expected:raise ValueError('forward_basis_publish_readback_mismatch')
 counts=Counter(r['status'] for r in records)
 return {'status':'ok','source':'eastmoney_public_dividend_table_and_official_reports','watchlistCount':len(stocks),'coveredCount':len(records),
         'coverageComplete':True,'readyCount':counts['ready'],'statusCounts':dict(counts),'published':publish,'readbackVerified':publish,
         'asOf':as_of,'excludedFutureIndexRows':meta.get('excludedFutureIndexRows',0),'mxInvoked':False,'aiInvoked':False,'private_payload_not_emitted':True}

def parse_args(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group()
 group.add_argument('--publish',action='store_true');group.add_argument('--dry-run',action='store_true')
 return parser.parse_args(argv)

def main():
 args=parse_args()
 try:
  import part4_official_announcement_sync as adapter
  print(json.dumps(run(adapter,publish=args.publish),ensure_ascii=False));return 0
 except Exception as error:
  category=str(error) if isinstance(error,ValueError) and re.fullmatch(r'forward_basis_[a-z_]+',str(error)) else 'forward_basis_public_sync_failed'
  print(json.dumps({'status':'error','category':category,'private_payload_not_emitted':True}));return 2
if __name__=='__main__':raise SystemExit(main())
