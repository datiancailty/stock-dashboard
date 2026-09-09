"""Exact public dividend-table + official half-year report normalization.

No I/O, credentials, private writes or inference from profit percentages. Table
PRETAX_BONUS_RMB is yuan per TEN shares (verified against IMPL_PLAN_PROFILE).
Quarterly rows are not silently relabelled as interim. Report statements of
'no plan' stay missing; only explicit current-period non-distribution supplies 0.
"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from forward_dividend_basis import calculate_forward_basis, USABLE

NUM=r'(?:\d+(?:\.\d+)?|\.\d+)'
PLAN=re.compile(r'^10派('+NUM+r')元[（(]含税(?:[,，]扣税后'+NUM+r'元)?[）)]$')
REPORT_AMOUNT=re.compile(r'每10股派(?:发)?(?:现金红利|现金股利|现金)?(?:人民币)?('+NUM+r')元(?:人民币现金)?[（(]含税[）)]')
REPORT_PER_SHARE=re.compile(r'每股(?:(?:派(?:发)?)?(?:现金红利|现金股利|现金股息|股息|现金))?(?:人民币)?('+NUM+r')元[（(]含税[）)]')
END_SECTION=re.compile(r'(?:第[二三]节|[二三23][、.])(?:公司基本情况(?:简介)?|释义|报告期主要业务)|公司简介|董事会决议通过的本报告期优先股')

def compact(text):return re.sub(r'\s+','',str(text or ''))

def body_stock_codes(text):
 """Labelled ordinary issuer codes, not explicitly bound preferred instruments.

 Keep every ambiguous/contradictory code. The short preferred-stock clause
 cannot cross a sentence, another code label, or an ordinary/A-share marker.
 This is a consistency check only; the collector must still bind the source.
 """
 codes=[]
 for match in re.finditer(r'(?:证券|股票)代码[:：]?(\d{6})(?!\d)',text):
  prefix=text[max(0,match.start()-80):match.start()]
  preferred=re.search(r'优先股(?:(?!普通股|A股|[。；;）)]|(?:证券|股票)代码).)*$',prefix)
  bound_name=re.search(r'(?:股票|证券)简称[:：]?[^。；;（）()]{1,20}[，,]?$',preferred[0]) if preferred else None
  if not bound_name:codes.append(match[1])
 return codes

def finite(value, *, per_ten=False):
 try:
  if isinstance(value,bool):return None
  number=Decimal(str(value))
  if per_ten and number.is_finite():
   parts=number.as_tuple();number=Decimal((parts.sign,parts.digits,parts.exponent-1))
  return number if number.is_finite() and 0<=number<1000000 and number==number.quantize(Decimal('.000000000001')) else None
 except (InvalidOperation,ValueError):return None

def per_share(value):
 number=finite(value,per_ten=True)
 if number is None:raise ValueError('forward_basis_component_amount_invalid')
 return number

def iso_day(value, *, optional=False):
 if optional and value in (None,'','-','--'):return None
 if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?: 00:00:00)?',value):
  raise ValueError('forward_basis_public_date_invalid')
 try:return date.fromisoformat(value[:10]).isoformat()
 except ValueError:raise ValueError('forward_basis_public_date_invalid') from None

def source(code,kind,year,plan,stage,amount,published,index,*,field,notice=None,dates=None,reason=None):
 return {'dto_title':f'{code} {year} {kind} source evidence','header':f'{year}年报' if kind=='annual' else f'{year}中报',
         'raw_plan':str(plan or ''),'raw_progress':str(stage or ''),'raw_pretax':amount,'dto_code':code,
         'field':deepcopy(field),'name_map':{},'published_at':published,'normalization_reason':reason,
         'official_notice':deepcopy(notice),'scope_evidence':'individual_distribution' if reason is None else 'unavailable_or_unquantified',
         'row_index':index,'distribution_dates':dates or {'registration':None,'ex_dividend':None,'payment':None}}

def report_evidence(doc,expected,current_year,asof):
 code=doc.get('code');identity=doc.get('id','');title=doc.get('title','')
 if (code not in expected or not re.fullmatch(r'AN\d{12,32}',identity)
     or doc.get('sourceUrl')!=f'https://data.eastmoney.com/notices/detail/{code}/{identity}.html'
     or type(doc.get('pageCount')) is not int or doc['pageCount']<1
     or not isinstance(doc.get('pages'),list) or len(doc['pages'])!=doc['pageCount']
     or not all(isinstance(p,str) and p for p in doc['pages'])):
  raise ValueError('forward_basis_public_notice_invalid')
 published=iso_day(doc.get('date'))
 if published>asof:raise ValueError('forward_basis_public_notice_future')
 fiscal=re.search(r'(?<!\d)(\d{4})年?(?:半年度|中期)报告摘要',compact(title))
 if not fiscal:return None
 current_year=int(fiscal[1])
 if not 1900<=current_year<=9999 or f'{current_year}-06-30'>published:
  raise ValueError('forward_basis_public_notice_period_invalid')
 text=compact(''.join(doc['pages']))
 # These are consistency checks, NOT proof of origin. Only the collector's
 # bound index/content responses establish the I/O provenance boundary.
 body_codes=body_stock_codes(text)
 body_years=re.findall(r'(?<!\d)(\d{4})年?(?:半年度|中期)报告摘要',text)
 if any(c!=code for c in body_codes) or any(int(y)!=current_year for y in body_years):
  raise ValueError('forward_basis_public_notice_body_identity_conflict')
 # Printed PDF folios can span several pages inside ONE API text chunk.
 # Never compare PDF page totals to the API page_size/chunk count.
 folios=[(int(n),int(total)) for n,total in re.findall(r'第(\d+)页[,，、/]?共(\d+)页',text)]
 pdf_count=None
 if folios:
  totals={total for _,total in folios};pdf_count=folios[0][1]
  if len(totals)!=1 or not 1<=pdf_count<=10000 or [n for n,_ in folios]!=list(range(1,pdf_count+1)):
   raise ValueError('forward_basis_public_notice_pdf_pages_incomplete')
 # The report's own distribution section, not historic dividends in its body.
 match=re.search(r'董事会(?:决议通过|审议)?的(?:本)?报告期利润分配(?:预案|方案)或公积金转增股本(?:预案|方案)',text)
 section=END_SECTION.split(text[match.end():],maxsplit=1)[0] if match else ''
 if not match:
  # Some issuers put the board's dividend statement in Important Notices,
  # without a distribution heading. Require BOTH section boundaries and an
  # explicit fiscal rebind below; never search the financial/history body.
  start=re.search(r'重要提示',text)
  end=END_SECTION.search(text,start.end()) if start else None
  if start and end:section=text[start.end():end.start()]
 notice={'id':'eastmoney:'+identity,'title':title,'date':published,'sourceUrl':doc['sourceUrl'],
         'excerpt':section,'contentSha256':hashlib.sha256('\n'.join(doc['pages']).encode()).hexdigest(),
         'pageCount':doc['pageCount'],'pageCountUnit':'api_text_chunks','pdfPageCount':pdf_count,
         'collection':deepcopy(doc.get('collection'))}
 status,amount,reason='unknown',None,'current_interim_no_quantified_plan'
 # Fiscal scope persists through unqualified following sentences; only an
 # explicit current-period marker can rebind a historical discussion.
 current=bool(match);bound=[]
 for sentence in re.split(r'[。；;]|(?=本报告期|本次中期|(?<!\d)\d{4}年?(?:半年度|中期|中报|年度|全年|末期|度))',section):
  period=re.search(r'(?<!\d)(\d{4})年?(半年度|中期|中报|年度|全年|末期|度)',sentence)
  if period:current=int(period[1])==current_year and period[2] in ('半年度','中期','中报')
  elif re.search(r'本报告期|本次中期',sentence):current=True
  elif re.search(r'上年|去年|历史|此前|上期',sentence):current=False
  if '优先股' in sentence:current=False
  if current and sentence:bound.append(sentence)
 current_text='。'.join(bound)
 # Collect all current claims BEFORE choosing zero. Tied contradictory claims
 # are separate events, so the common version resolver exposes conflict.
 claims=[]
 if ('公司计划不派发现金红利' in current_text
     or re.search(rf'(?:公司)?{current_year}年半年度不进行利润分配',current_text)):
  claims.append(('no_distribution',Decimal(0)))
 foreign=bool(re.search(r'港元|港币|美元',current_text))
 # A/H belong to one issuer, not two additive dividends. A declared foreign
 # currency plan is not a fixed CNY claim. Accept only an explicit A-share
 # RMB per-share/per-ten clause; never calculate an exchange conversion.
 cash_bound=bound if not foreign else [s for s in bound if re.search(r'A股[^。；;]*(?:每股|每10股)[^。；;]*人民币',s)]
 if not re.search(r'预计|预测|不低于|不超过',current_text):
  values={per_share(v) for v in REPORT_AMOUNT.findall('。'.join(cash_bound))}
  for sentence in cash_bound:
   if not re.search(r'现金红利|现金股利|现金股息|股息|分红',sentence):continue
   for raw in REPORT_PER_SHARE.findall(sentence):
    value=finite(raw)
    if value is None:raise ValueError('forward_basis_component_amount_invalid')
    values.add(value)
  claims.extend(('announced',v) for v in sorted(values) if v>0)
 if claims:status,amount=claims[0];reason=None
 elif foreign:reason='current_interim_cny_unconfirmed'
 # A conditional share-count adjustment is not a revision of the per-share
 # plan. Exempt only its total-adjustment phrase, never other revision words
 # in the same sentence; keep the original evidence verbatim.
 revision_parts=[]
 for sentence in bound:
  if (re.search(r'(?:如|若)[^。；;]*(?:股本|股数)[^。；;]*变动',sentence)
      and '每股分配金额不变' in sentence):
   sentence=re.sub(r'(?:相应)?调整(?:分配|分红)总额|对(?:分配|分红)总额(?:进行)?调整','',sentence)
  revision_parts.append(sentence)
 if re.search(r'取消|终止|撤销|作废|不再实施',current_text):
  status,amount,reason='cancelled',None,'current_interim_plan_cancelled';claims=[]
 elif re.search(r'更正|修订|调整','。'.join(revision_parts)):
  status,amount,reason='unknown',None,'current_interim_revision_unresolved';claims=[]
 src=source(code,'interim',current_year,section,status,str(amount) if amount is not None else None,published,0,
            field={'provider':'eastmoney_official_report','distributionNature':'special' if re.search(r'(?:特别|非经常性)(?:现金)?(?:分红|股息|股利|红利)',current_text) else 'unknown',
                   'currentPeriodEvidence':current_text},notice=notice,reason=reason)
 return {'code':code,'year':current_year,'kind':'interim','amount':amount,'status':status,
         'amount_scope':'distribution','published_at':published,'source':src,'additional_claims':claims[1:]}

def build_records(rows,reports,stocks,as_of):
 try:
  moment=datetime.fromisoformat(as_of.replace('Z','+00:00'))
  if moment.tzinfo is None:raise ValueError()
 except (ValueError,AttributeError):raise ValueError('forward_basis_as_of_invalid') from None
 today=moment.astimezone(ZoneInfo('Asia/Shanghai')).date();year=today.year
 expected={s.get('code') for s in stocks}
 if not 1<=len(stocks)<=50 or len(expected)!=len(stocks) or not all(isinstance(c,str) and re.fullmatch(r'\d{6}',c) for c in expected):
  raise ValueError('forward_basis_watchlist_invalid')
 if not isinstance(rows,list) or {r.get('SECURITY_CODE') for r in rows}!=expected:
  raise ValueError('forward_basis_public_coverage_incomplete')
 events=[];current_table={}
 for index,row in enumerate(rows):
  code=row['SECURITY_CODE'];secu=row.get('SECUCODE','')
  if not re.fullmatch(re.escape(code)+r'\.(SH|SZ|BJ)',secu):raise ValueError('forward_basis_public_identity_invalid')
  period=iso_day(row.get('REPORT_DATE'));pd=date.fromisoformat(period)
  if pd>today:raise ValueError('forward_basis_public_future_period')
  if (pd.month,pd.day) not in ((12,31),(6,30)):continue
  kind='annual' if pd.month==12 else 'interim'
  published=iso_day(row.get('NOTICE_DATE'))
  if published>today.isoformat():raise ValueError('forward_basis_public_notice_future')
  dates={'registration':iso_day(row.get('EQUITY_RECORD_DATE'),optional=True),
         'ex_dividend':iso_day(row.get('EX_DIVIDEND_DATE'),optional=True),'payment':None}
  if dates['registration'] and dates['ex_dividend'] and dates['registration']>dates['ex_dividend']:
   raise ValueError('forward_basis_public_distribution_date_conflict')
  plan=compact(row.get('IMPL_PLAN_PROFILE'));raw_value=row.get('PRETAX_BONUS_RMB')
  amount=per_share(raw_value) if raw_value not in (None,'','-','--') else None
  raw_amount=Decimal(str(raw_value)) if amount is not None else None
  progress=row.get('ASSIGN_PROGRESS');reason=None
  if progress=='实施分配':
   status='implemented' if dates['ex_dividend'] and dates['ex_dividend']<=today.isoformat() else 'announced'
  elif progress in ('董事会决议通过','股东大会决议通过'):status='announced'
  else:status='unknown';reason='public_plan_not_quantified_or_not_approved'
  matched=PLAN.fullmatch(plan)
  if matched and raw_amount is not None and Decimal(matched[1])!=raw_amount:
   reason='public_plan_amount_conflict'
  elif not matched or amount is None or amount<=0:
   status='unknown';reason='public_plan_not_quantified_or_not_approved';amount=None
  src=source(code,kind,pd.year,row.get('IMPL_PLAN_PROFILE'),progress,str(amount) if amount is not None else None,published,index,
             field={'provider':'eastmoney_public_dividend_table','reportName':'RPT_SHAREBONUS_DET',
                    'sourceUrl':f'https://data.eastmoney.com/yjfp/detail/{code}.html','unit':'CNY_per_10_shares',
                    'raw':deepcopy(row)},dates=dates,reason=reason)
  event={'code':code,'year':pd.year,'kind':kind,'amount':amount,'status':status,
         'amount_scope':'distribution','published_at':published,'source':src}
  events.append(event)
  if reason=='public_plan_amount_conflict':
   alt=deepcopy(event);alt['amount']=per_share(matched[1]);events.append(alt)
  if kind=='interim':current_table.setdefault((code,pd.year),[]).append(event)
 report_events=[]
 for doc in reports:
  event=report_evidence(doc,expected,year,today.isoformat())
  if event is not None:
   report_events.append(event)
   for status,amount in event.pop('additional_claims',[]):
    alt=deepcopy(event);alt.update(status=status,amount=amount);report_events.append(alt)
 for event in report_events:
  if event['status']=='announced':
   special_docs=[d for d in reports if d.get('code')==event['code']
                 and re.search(rf'{event["year"]}年特别分红方案',compact(d.get('title')))
                 and d.get('date')==event.get('published_at')]
   for doc in special_docs:
    values={per_share(v) for v in REPORT_AMOUNT.findall(compact(''.join(doc['pages'])))}
    if values=={event['amount']}:
     event['source']['field']['distributionNature']='special'
     event['source']['official_notice']['relatedNotice']={k:doc[k] for k in ('id','title','date','sourceUrl')}
  # A later implementation amount supersedes a report's earlier draft. A
  # report cannot silently reset an explicit dated payment to "no plan".
  existing=current_table.get((event['code'],event['year']),[])
  if event['status']=='announced' and event['source']['field']['distributionNature']=='special':
   for prior in existing:
    if prior['status'] in USABLE and prior['amount']==event['amount'] and prior['published_at']==event['published_at']:
     prior['source']['field']['distributionNature']='special'
     prior['source']['official_notice']=deepcopy(event['source']['official_notice'])
  if (existing and event['status']=='unknown'
      and re.search(r'无新(?:利润分配|分红)预案',event['source']['field']['currentPeriodEvidence'])
      and all(prior['status']=='implemented' for prior in existing)):
   for prior in existing:
    prior['source']['field'].setdefault('subsequentUnquantifiedReports',[]).append(deepcopy(event['source']['official_notice']))
   continue
  events.append(event)
 # No calendar-driven placeholder: each kind independently uses its latest
 # known fiscal period. The collector, not this pure parser, proves coverage.
 results=calculate_forward_basis(events,codes=[s['code'] for s in stocks]);records=[]
 for code,result in results.items():
  if result['amount'] is not None and (finite(result['amount']) is None or Decimal(str(float(result['amount'])))!=result['amount']):
   raise ValueError('forward_basis_total_amount_invalid')
  components={}
  for kind,item in result['components'].items():
   usable=item['status'] in USABLE
   if usable and (finite(item['amount']) is None or Decimal(str(float(item['amount'])))!=item['amount']):
    raise ValueError('forward_basis_component_amount_invalid')
   reason=None if usable else item.get('reason') if item['status'] in ('conflict','ambiguous') else (item.get('source') or {}).get('normalization_reason') or item.get('reason')
   if not usable and any((e.get('source') or {}).get('normalization_reason')=='current_interim_cny_unconfirmed' for e in item.get('evidence',[])):
    reason='current_interim_cny_unconfirmed'
   components[kind]={'year':item.get('year'),'kind':kind,'amount':float(item['amount']) if usable and item['amount'] is not None else None,
                     'status':item['status'],'reason':reason,'amount_scope':item.get('amount_scope','unknown'),
                     'published_at':item.get('published_at'),'source':deepcopy(item.get('source')),
                     'evidence':[deepcopy(e.get('source')) for e in item.get('evidence',[])]}
  records.append({'code':code,'amount':float(result['amount']) if result['amount'] is not None else None,'status':result['status'],
                  'reason':next((c['reason'] for c in components.values() if c['status'] not in USABLE),None),
                  'components':components,'asOf':as_of})
 return records
