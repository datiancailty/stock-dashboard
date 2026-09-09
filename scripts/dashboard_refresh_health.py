"""Sanitized private acquisition status, separate from AI/profile health."""
from __future__ import annotations
import json, re, uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
BJ=ZoneInfo('Asia/Shanghai')
STAGES=('notices','quotes','technical','news','forward','recommendations')
SOURCES={'hithink_snapshot','eastmoney_snapshot','hithink_daily','eastmoney_public','legacy_public_daily','public_company_notice_index'}
CATEGORIES={'pending','running','complete','fallback_used','rate_limited','timeout','empty_response','auth_failed','provider_failed','invalid_data','missing_contract','write_failed','readback_failed','unknown_error','dependency_failed'}

def error_category(value):
    if not isinstance(value,str) or not re.fullmatch('[a-z][a-z0-9_]{0,119}',value):return 'unknown_error'
    if value in CATEGORIES:return value
    if any(s in value for s in ('quota','rate_limit')):return 'rate_limited'
    if 'timeout' in value:return 'timeout'
    if 'empty' in value:return 'empty_response'
    if any(s in value for s in ('auth','credential','session','keychain','login','writer_not_initialized')):return 'auth_failed'
    if any(s in value for s in ('contract_missing','rpc_not_found')):return 'missing_contract'
    if 'readback' in value:return 'readback_failed'
    if any(s in value for s in ('publish','write','private_rpc')):return 'write_failed'
    if any(s in value for s in ('invalid','mismatch','incomplete','coverage','stale','future','conflict','insufficient')):return 'invalid_data'
    if any(s in value for s in ('provider','business','http','transport','retry','quote_sources','execution')):return 'provider_failed'
    return 'unknown_error'

def stage_health(value):
    value=value if isinstance(value,dict) else {}
    status=value.get('status');status=status if status in {'pending','running','ok','error','skipped'} else 'error'
    published=value.get('published') is True
    if status=='ok' and not published:status='error'
    category=error_category(value.get('category','complete' if status=='ok' else status))
    source=value.get('source')
    if not isinstance(source,str):source=None
    source={'eastmoney_public_snapshot':'eastmoney_snapshot','eastmoney_public_dividend_table_and_official_reports':'eastmoney_public'}.get(source,source)
    return {'status':status,'category':category,'published':published,'source':source if source in SOURCES else None}

class HealthReporter:
    """Only instantiate for explicit publication; writes no business payload."""
    def __init__(self, *, release='explicit_manual', now=None, adapter=None):
        from dashboard_refresh_daily import target_slot
        if adapter is None:
            import part4_official_announcement_sync as adapter
        self.adapter=adapter;now=now or datetime.now(BJ)
        self.payload={'schemaVersion':1,'runId':'refresh-'+uuid.uuid4().hex,'sourceRelease':release,
            'status':'running','targetDate':target_slot(now),'startedAt':now.isoformat(timespec='microseconds'),
            'finishedAt':None,'stages':{s:stage_health({'status':'pending','category':'pending'}) for s in STAGES}}
        self.failures=0
    def emit(self):
        w,c,t=self.adapter.load_private_session()
        secret=self.adapter.part4_writer_secret(w,c)
        result=self.adapter.private_rpc(w,c,t,'personal_sync_refresh_health',
            {'p_run_id':self.payload['runId'],'p_health':self.payload,'p_writer_secret':secret})
        # Exact payload proof comes from the narrow getter, not a truthy POST.
        back=self.adapter.private_rpc(w,c,t,'personal_get_refresh_health',{})
        if not isinstance(back,dict) or any(back.get(k)!=v for k,v in self.payload.items()):
            raise ValueError('health_readback_failed')
        return result
    def start(self):return self.emit()
    def stage(self,name,value):
        if name not in STAGES:raise ValueError('health_stage_invalid')
        self.payload['stages'][name]=stage_health(value)
        try:self.emit()
        except Exception:self.failures+=1
    def finish(self,success):
        states=self.payload['stages']
        self.payload['status']='ok' if success else 'partial' if any(s['status']=='ok' for s in states.values()) else 'error'
        self.payload['finishedAt']=datetime.now(BJ).isoformat(timespec='microseconds')
        try:self.emit();return True
        except Exception:self.failures+=1;return False

def current_release(root):
    try:
        value=json.loads((Path(root)/'dashboard-refresh-release.json').read_text())['version']
        if isinstance(value,str) and re.fullmatch('[A-Za-z0-9._-]{1,100}',value):return value
    except (OSError,ValueError,KeyError,TypeError):pass
    return 'explicit_manual'
