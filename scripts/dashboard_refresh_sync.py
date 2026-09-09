#!/usr/bin/env python3
"""Candidate local refresh chain. Defaults read-only; NOT installed in launchd.

After manual hosted migrations + readback + explicit authorization, --publish
refreshes official notices, quotes, news, latest-period basis and recommendation
outcomes independently. No Codex, profile mutation, GitHub writes or trading.
Existing part4-daily schedule is not changed by creating/running this file.
"""
from __future__ import annotations
import argparse,json,os,re,subprocess,sys
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from dashboard_refresh_health import HealthReporter,current_release
ROOT=Path(__file__).resolve().parents[1]
BJ=ZoneInfo('Asia/Shanghai')

def run_stage(name,args,publish):
 env=os.environ.copy()
 # Default Dashboard data jobs never receive MX credentials. Broker/account
 # integration lives in a separate VPS process and is not modified here.
 env.pop('MX_APIKEY',None)
 mode=[] if publish and name == 'notices' else ['--publish'] if publish else ['--dry-run']
 try:
  result=subprocess.run([sys.executable,*args,*mode],cwd=ROOT,env=env,capture_output=True,text=True,timeout=900,check=False)
  payload=json.loads(result.stdout)
  if not isinstance(payload,dict):raise ValueError()
  # Accept only documented sanitized aggregate stage summary; never pass payload
  # strings, exceptions, stderr or credentials to the combined output.
  accepted={'ok','dry_run','dry_run_ok'} | ({'audit_ok'} if not publish else set())
  ok=result.returncode==0 and payload.get('status') in accepted
  published=payload.get('published') is True
  category=payload.get('category','complete' if ok else 'stage_failed')
  if not isinstance(category,str) or not re.fullmatch('[a-z][a-z0-9_]{0,119}',category) or (env.get('MX_APIKEY') and env['MX_APIKEY'] in category):category='stage_failed'
  if publish and not published:ok=False
  if ok and payload.get('fallbackReasons'):category='fallback_used'
  out={'status':'ok' if ok else 'error','category':category,'published':published}
  source=payload.get('source')
  if isinstance(source,str) and source in {'hithink_snapshot','eastmoney_public_snapshot','hithink_daily','public_company_notice_index','eastmoney_public_dividend_table_and_official_reports','eastmoney_public','legacy_public_daily'}:out['source']=source
  for key in ('stored','watchlistCount','readyCount','missingCount','itemCount','recordCount','new','successfulBatchCount','confirmedReadyCount','usableCount'):
   value=payload.get(key)
   if type(value) is int and 0<=value<=100000:out[key]=value
  counts=payload.get('statusCounts')
  if isinstance(counts,dict) and set(counts)<= {'ready','missing','ambiguous','conflict','negated'} and all(type(v) is int and 0<=v<=50 for v in counts.values()):out['statusCounts']=counts
  return out
 except subprocess.TimeoutExpired:return {'status':'error','category':'stage_timeout','published':False}
 except Exception:return {'status':'error','category':'stage_response_or_execution_failed','published':False}

def run_refresh(publish=False,health_reporter=None):
 now=datetime.now(BJ);start=(now.date()-timedelta(days=34)).isoformat()
 commands={
 'notices':['scripts/part4_official_announcement_sync.py','sync','--from',start,'--to',now.date().isoformat()],
 'quotes':['scripts/personal_market_snapshot_sync.py'],
 'technical':['scripts/personal_technical_snapshot_sync.py'],
 'news':['scripts/personal_news_sync.py'],
 'forward':['scripts/personal_dividend_refresh_sync.py'],
 'recommendations':['scripts/personal_recommendation_eval_sync.py'],
 }
 stages={}
 if health_reporter is not None:health_reporter.start()
 for name,args in commands.items():
  if health_reporter is not None:health_reporter.stage(name,{'status':'running','category':'running','published':False})
  # Public forward collector now proves its own full official-source scan;
  # it no longer consumes the Part4 notice writer's result.
  stages[name]=run_stage(name,args,publish)
  if health_reporter is not None:health_reporter.stage(name,stages[name])
 complete=all(s['status']=='ok' for s in stages.values())
 published=publish and complete and all(s.get('published') is True for s in stages.values())
 health_ok=health_reporter.finish(complete and published) if health_reporter is not None else None
 return {'status':'ok' if complete and (not publish or published) and health_ok is not False else 'error','dryRun':not publish,'published':published,'stages':stages,'healthPublished':health_ok,'aiInvoked':False,'profileChanged':False}

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 group=parser.add_mutually_exclusive_group();group.add_argument('--publish',action='store_true');group.add_argument('--dry-run',action='store_true')
 args=parser.parse_args();result=run_refresh(args.publish,HealthReporter(release=current_release(ROOT)) if args.publish else None);print(json.dumps(result,ensure_ascii=False));return 0 if result['status']=='ok' else 2
if __name__=='__main__':raise SystemExit(main())
