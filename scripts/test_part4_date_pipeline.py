"""Offline calendar-date pipeline tests; no Hosted or provider calls."""
import json,sys,tempfile,unittest
from pathlib import Path
from urllib.parse import parse_qs,urlparse
sys.path.insert(0,str(Path(__file__).resolve().parent))
import part4_date_sync as subject


def notice():
    return {'id':'eastmoney:AN202609200000000001','date':'2026-09-20','code':'600000','name':'合成甲',
            'type':'权益分派公告','stage':'implementation','title':'合成甲:2026年中期权益分派实施公告',
            'description':'合成公告','source':'东方财富公司公告',
            'sourceUrl':'https://data.eastmoney.com/notices/detail/600000/AN202609200000000001.html','sourceHash':'a'*64}


def payment():
    return {'INFO_CODE':'AN202609200000000001','SECURITY_CODE':'600000','SECUCODE':'600000.SH',
            'ASSIGN_OBJECT':'A股股东','ASSIGN_PROGRESS':'实施方案','NOTICE_DATE':'2026-09-20 00:00:00',
            'REPORT_DATE':'2026中报','REPORT_TIME':'2026-06-30 00:00:00','EQUITY_RECORD_DATE':'2026-09-25 00:00:00',
            'EX_DIVIDEND_DATE':'2026-09-28 00:00:00','PAY_CASH_DATE':'2026-09-29 00:00:00'}


class DatePipelineTests(unittest.TestCase):
    def test_complete_public_date_scan_precedes_any_publication(self):
        self.assertIsNotNone(subject,'date source pipeline is not implemented')
        calls=[]
        def fetch(url):
            query=parse_qs(urlparse(url).query);calls.append(query)
            self.assertEqual(query['reportName'],['RPT_F10_DIVIDEND_MAIN'])
            self.assertIn('AN202609200000000001',query['filter'][0])
            return {'success':True,'result':{'pages':1,'count':1,'data':[payment()]}}
        with tempfile.TemporaryDirectory() as temp:
            out=subject.collect_dates([notice()],fetcher=fetch,evidence_dir=Path(temp))
            self.assertEqual(out['implementationDateCount'],3)
            self.assertEqual([e['date'] for e in out['events']],['2026-09-25','2026-09-28','2026-09-29'])
            self.assertEqual(len(calls),1)
            self.assertEqual(len(list(Path(temp).glob('*.json'))),1)


    def test_sync_integrates_dates_and_reads_back_both_ledgers(self):
        import contextlib,io
        from unittest.mock import patch
        import part4_official_announcement_sync as sync
        from part4_dividend_date_materializer import materialize_calendar
        projected=materialize_calendar([notice()],[payment()])
        dates=[e for e in projected['events'] if e['id'].startswith('eastmoney-date:')]
        out=io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('sys.argv',['sync','sync','--from','2026-09-01','--to','2026-09-30','--include-implementation-dates']))
            stack.enter_context(patch.object(sync,'load_private_session',return_value=(None,None,None)))
            stack.enter_context(patch.object(sync,'private_watchlist',return_value=[sync.Stock('600000','合成甲')]))
            stack.enter_context(patch.object(sync,'scan_watchlist',return_value=([notice()],[],[sync.ScanCoverage('600000',1,True,None)],1)))
            collect=stack.enter_context(patch.object(subject,'collect_dates',return_value={'events':dates,'implementationDateCount':3,'missingDates':[]}))
            stack.enter_context(patch.object(sync,'part4_writer_secret',return_value='synthetic'))
            rpc=stack.enter_context(patch.object(sync,'private_rpc',side_effect=[{'stored':1},{'events':[notice()]},{'stored':3},{'events':[notice(),*dates]}]))
            stack.enter_context(contextlib.redirect_stdout(out));stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            try:
                rc=sync.main()
            except SystemExit:
                self.fail('implementation-date CLI flag is not implemented')
            self.assertEqual(rc,0)
            collect.assert_called_once()
            self.assertEqual([c.args[3] for c in rpc.call_args_list],['personal_sync_part4_dividend_notices','personal_get_part4','personal_sync_part4_dividend_dates','personal_get_part4'])
            payload=rpc.call_args_list[2].args[4]
            self.assertEqual(payload['p_events'],dates)
            self.assertEqual(payload['p_watchlist_codes'],['600000'])
            self.assertEqual(payload['p_run_id'],rpc.call_args_list[0].args[4]['p_run_id'])
        result=json.loads(out.getvalue())
        self.assertEqual(result['implementationDateCount'],3)
        self.assertTrue(result['readbackVerified'])


    def test_malformed_public_envelopes_fail_closed(self):
        import copy
        broken=copy.deepcopy(payment());broken.pop('PAY_CASH_DATE')
        envelopes=[None,[],{'success':False},
            {'success':True,'result':{'pages':True,'count':1,'data':[payment()]}},
            {'success':True,'result':{'pages':2,'count':101,'data':[payment()]}},
            {'success':True,'result':{'pages':1,'count':1,'data':[{**payment(),'INFO_CODE':'AN202609200000000099'}]}},
            {'success':True,'result':{'pages':1,'count':2,'data':[payment(),payment()]}},
            {'success':True,'result':{'pages':1,'count':1,'data':[broken]}}]
        for body in envelopes:
            with self.subTest(body=body),tempfile.TemporaryDirectory() as temp:
                try:
                    subject.collect_dates([notice()],fetcher=lambda url:body,evidence_dir=Path(temp))
                except ValueError:
                    pass
                except Exception as error:
                    self.fail('expected sanitized ValueError, got '+type(error).__name__)
                else:
                    self.fail('malformed source was accepted')

    def test_date_validation_failure_prevents_all_writers(self):
        import contextlib,io
        from unittest.mock import patch
        import part4_official_announcement_sync as sync
        output=io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('sys.argv',['sync','sync','--from','2026-09-01','--to','2026-09-30','--include-implementation-dates']))
            stack.enter_context(patch.object(sync,'load_private_session',return_value=(None,None,None)))
            stack.enter_context(patch.object(sync,'private_watchlist',return_value=[sync.Stock('600000','合成甲')]))
            stack.enter_context(patch.object(sync,'scan_watchlist',return_value=([notice()],[],[sync.ScanCoverage('600000',1,True,None)],1)))
            stack.enter_context(patch.object(subject,'collect_dates',side_effect=ValueError('calendar_dates_incomplete')))
            secret=stack.enter_context(patch.object(sync,'part4_writer_secret'))
            rpc=stack.enter_context(patch.object(sync,'private_rpc'))
            stack.enter_context(contextlib.redirect_stdout(output))
            self.assertEqual(sync.main(),2)
            secret.assert_not_called();rpc.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['category'],'part4_date_source_invalid')


    def test_daily_entry_enables_date_contract_without_other_stage_changes(self):
        from unittest.mock import patch
        import dashboard_refresh_sync as daily
        calls=[]
        def run(name,args,publish):
            calls.append((name,args,publish));return {'status':'ok','published':False}
        with patch.object(daily,'run_stage',side_effect=run):
            self.assertEqual(daily.run_refresh(False)['status'],'ok')
        self.assertEqual([c[0] for c in calls],['notices','quotes','technical','news','forward','recommendations'])
        self.assertIn('--include-implementation-dates',calls[0][1])
        self.assertFalse(any(c[2] for c in calls))


    def test_public_query_batches_are_complete_and_deduplicated(self):
        import re
        notes=[]
        for i in range(41):
            aid=f'AN2026092000000000{i:02d}'
            notes.append({**notice(),'id':'eastmoney:'+aid,'sourceUrl':f'https://data.eastmoney.com/notices/detail/600000/{aid}.html'})
        calls=[]
        def fetch(url):
            query=parse_qs(urlparse(url).query)
            ids=re.findall(r'\"(AN[0-9]+)\"',query['filter'][0]);calls.append(ids)
            return {'success':True,'result':{'pages':1,'count':len(ids),'data':[{**payment(),'INFO_CODE':aid} for aid in ids]}}
        with tempfile.TemporaryDirectory() as temp:
            out=subject.collect_dates(notes,fetcher=fetch,evidence_dir=Path(temp))
        self.assertEqual([len(c) for c in calls],[40,1])
        self.assertEqual(len(out['events']),len(notes)*3)
        self.assertEqual(len({e['id'] for e in out['events']}),len(out['events']))


    def test_explicit_preference_title_does_not_require_ordinary_share_f10(self):
        from unittest.mock import Mock
        event={**notice(),'title':'合成甲:2026年度优先股派息实施公告'}
        fetch=Mock(side_effect=AssertionError('ordinary share F10 must not be queried for preference-only notice'))
        with tempfile.TemporaryDirectory() as temp:
            out=subject.collect_dates([event],fetcher=fetch,evidence_dir=Path(temp))
        fetch.assert_not_called()
        self.assertEqual(out['implementationDateCount'],0)
        self.assertEqual(out['events'],[])
        self.assertEqual(out['excludedImplementationNotices'],[{'noticeId':event['id'],'reason':'explicit_preference_share_notice'}])


    def test_mixed_ordinary_share_spacing_and_width_never_get_preference_exemption(self):
        from unittest.mock import Mock
        for marker in ['A 股','Ａ 股','a股','A\u00a0股','普通 股']:
            event={**notice(),'title':f'合成甲:2026年度优先股派息及{marker}分红实施公告'}
            fetch=Mock(return_value={'success':True,'result':{'pages':0,'count':0,'data':[]}})
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError,'calendar_implementation_row_missing'):
                    subject.collect_dates([event],fetcher=fetch,evidence_dir=Path(temp))
                fetch.assert_called_once()


if __name__=='__main__':unittest.main()
