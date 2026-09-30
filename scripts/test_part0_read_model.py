"""Offline Part0 monitor tests: never import the trading worker."""
import sys, unittest, json, sqlite3, tempfile, hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ops'))
try:
    import part0_read_model as subject
except ImportError:
    subject=None

class ProjectionTests(unittest.TestCase):
    def test_saved_snapshot_is_not_restamped_and_ledger_is_read_only(self):
        self.assertIsNotNone(subject,'Part0 read model is not implemented')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.sqlite'
            c=sqlite3.connect(path)
            c.executescript('CREATE TABLE account_snapshot(observed_at_cn TEXT,account_json TEXT,source TEXT); CREATE TABLE intent(symbol TEXT,decision_json TEXT,status TEXT,broker_trade_quantity INTEGER,broker_trade_price REAL,reconciled_trade_quantity INTEGER,trade_date TEXT,updated_at_cn TEXT); CREATE TABLE meta(key TEXT,value_json TEXT,updated_at_cn TEXT);')
            a={'observed_at_cn':'2026-09-30T15:00:00+08:00','total_assets':10000,'available_cash':8800,'total_position_value':1200,'total_profit':100,'positions':[{'symbol':'600000.SH','quantity':100,'available_quantity':0,'cost_price':11,'market_value':1200}]}
            c.execute('INSERT INTO account_snapshot VALUES(?,?,?)',(a['observed_at_cn'],json.dumps({'account':a,'order_watermarks':{'PRIVATE_ID':'unused'}}),'provider_success_eod'))
            c.execute('INSERT INTO intent VALUES(?,?,?,?,?,?,?,?)',('600000.SH',json.dumps({'side':'buy','price':99,'raw_secret':'not published'}),'completed',100,10.5,100,'2026-09-30','2026-09-30T09:36:05+08:00'))
            c.execute('INSERT INTO meta VALUES(?,?,?)',('active_symbols',json.dumps(['600000.SH']),a['observed_at_cn']))
            c.execute('INSERT INTO meta VALUES(?,?,?)',('last_strategy_cycle_at_cn',json.dumps(a['observed_at_cn']),a['observed_at_cn']))
            c.commit();c.close()
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            out=subject.read_ledger(path)
            self.assertEqual(out['account']['asOf'],a['observed_at_cn'])
            self.assertEqual(out['strategyCycleAt'],a['observed_at_cn'])
            p=out['account']['positions'][0]
            self.assertEqual((p['quantity'],p['availableQuantity'],p['price'],p['pnl']),(100,0,12,100))
            self.assertEqual(out['trades'][0]['price'],10.5,'actual broker fill, not requested price')
            self.assertEqual(out['trades'][0]['confirmedAt'],'2026-09-30T09:36:05+08:00')
            self.assertNotIn('PRIVATE_ID',json.dumps(out));self.assertNotIn('raw_secret',json.dumps(out))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
            self.assertEqual([p.name for p in Path(directory).iterdir()],['state.sqlite'])

    def test_runtime_expiry_does_not_hide_historical_failure(self):
        self.assertTrue(hasattr(subject,'build_runtime'),'runtime projection is missing')
        now='2026-09-30T18:00:00+08:00'
        journal=[{'__REALTIME_TIMESTAMP':'1790738765701683','MESSAGE':json.dumps({'ok':False,'error':'PRIVATE_DETAIL'})}, {'__REALTIME_TIMESTAMP':'1790751002795039','MESSAGE':json.dumps({'ok':True,'halted':False,'passed':True})}]
        units={'trading':{'LoadState':'loaded','ActiveState':'inactive','UnitFileState':'disabled','NextElapseUSecRealtime':''},'strategy':{'LoadState':'loaded','ActiveState':'inactive','MainPID':'0'},'dashboard':{'ActiveState':'inactive','Result':'success'},'mirror':{'ActiveState':'inactive','Result':'success'}}
        daily={'lastStatus':'ok','finishedAt':'2026-09-30T17:59:00+08:00','attemptedDate':'2026-09-30'}
        r,events=subject.build_runtime(now=now,expires='2026-09-30T15:05:00+08:00',units=units,daily=daily,journal=journal,ledger={'activeSymbols':[],'strategyCycleAt':None,'quoteAsOf':None})
        self.assertEqual(r['authorization']['status'],'expired')
        self.assertEqual(r['strategy']['status'],'ok')
        self.assertEqual(r['dashboard']['status'],'ok')
        self.assertTrue(any(e['status']=='error' for e in events))
        self.assertNotIn('PRIVATE_DETAIL',json.dumps(events))
        units['trading']['UnitFileState']='enabled'
        r,_=subject.build_runtime(now=now,expires='2026-09-30T15:05:00+08:00',units=units,daily=daily,journal=journal,ledger={'activeSymbols':[],'strategyCycleAt':None,'quoteAsOf':None})
        self.assertEqual(r['authorization']['status'],'requires_review')

    def test_disabled_timer_is_not_proof_a_service_stopped(self):
        for state in ['active','activating','deactivating',None]:
            units={'trading':{'LoadState':'loaded','ActiveState':'inactive','UnitFileState':'disabled','NextElapseUSecRealtime':''},'strategy':{'LoadState':'loaded','ActiveState':state,'MainPID':'12'}}
            r,_=subject.build_runtime(now='2026-09-30T18:00:00+08:00',expires='2026-09-30T15:05:00+08:00',units=units,daily={},journal=[],ledger={'activeSymbols':[],'strategyCycleAt':None,'quoteAsOf':None})
            self.assertEqual(r['authorization']['status'],'requires_review')

    def test_missing_cost_stays_null_not_zero(self):
        self.assertIsNotNone(subject)
        p=subject.project_account({'observed_at_cn':'2026-09-30T15:00:00+08:00','total_assets':100,'available_cash':0,'total_position_value':100,'positions':[{'symbol':'600000.SH','quantity':10,'available_quantity':10,'cost_price':None,'market_value':100}]})
        self.assertIsNone(p['positions'][0]['pnl']);self.assertIsNone(p['positions'][0]['pnlPct'])
        for bad in [True,float('nan'),float('inf'),-1]:
            with self.assertRaises(ValueError):subject.number(bad,minimum=0)

    def test_collector_is_read_only_and_emits_no_private_stdout(self):
        self.assertTrue(hasattr(subject,'collect'),'collector is missing')
        from unittest.mock import patch
        def command(args):
            if args[0]=='journalctl':return ''
            self.assertEqual(args[:2],['systemctl','show'])
            return 'LoadState=loaded\nActiveState=inactive\nMainPID=0\nUnitFileState=disabled\nResult=success\nNextElapseUSecRealtime=\n'
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'daily.json';state.write_text(json.dumps({'lastStatus':'ok','finishedAt':'2026-09-30T16:00:00+08:00','attemptedDate':'2026-09-30'}))
            config={'db_path':'/synthetic/state.sqlite','daily_state':str(state),'authority_expires_at':'2026-09-30T15:05:00+08:00'}
            ledger={'account':None,'trades':[],'activeSymbols':[],'strategyCycleAt':None,'quoteAsOf':None}
            with patch.object(subject,'read_ledger',return_value=ledger):
                p=subject.collect(config,now='2026-09-30T18:00:00+08:00',command=command)
            self.assertEqual(p['runtime']['authorization']['status'],'expired')
            self.assertEqual(p['schemaVersion'],1)
            self.assertIsNone(p['account'],'unread is not an empty portfolio')
            out=Path(directory).resolve()/'snapshot.json';subject.write_snapshot(out,p)
            self.assertEqual(json.loads(out.read_text()),p)
            import stat
            self.assertEqual(stat.S_IMODE(out.stat().st_mode),0o640)

if __name__=='__main__':unittest.main()
