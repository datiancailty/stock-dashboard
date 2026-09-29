import contextlib,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import part4_official_announcement_sync as notices
from datetime import date

class DiagnosticsTests(unittest.TestCase):
    def test_page_start_and_end_are_durable_without_stdout(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{'DASHBOARD_DIAGNOSTICS_DIR':str(Path(root).resolve())}), patch.object(notices,'safe_curl_json',return_value={'success':1,'data':{'list':[],'total_hits':0}}):
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                notices.fetch_notice_page('601318',2,window_start=date(2026,9,1),window_end=date(2026,9,29))
            files=list(Path(root).glob('*.jsonl'))
            self.assertEqual(len(files),1,'missing durable request diagnostics')
            rows=[json.loads(x) for x in files[0].read_text().splitlines()]
            self.assertEqual([r['event'] for r in rows],['notice_page_start','notice_page_end'])
            self.assertEqual(rows[0]['page'],2)
            self.assertEqual(rows[0]['window_start'],'2026-09-01')
            self.assertGreaterEqual(rows[1]['elapsed_ms'],0)
            self.assertEqual(out.getvalue(),'')
    def test_http_attempt_logging_omits_query(self):
        import subprocess
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{'DASHBOARD_DIAGNOSTICS_DIR':str(Path(root).resolve())}):
            def fake(command,**kwargs):
                Path(command[-1]).write_text('{"success":1}')
                return subprocess.CompletedProcess(command,0,'','')
            with patch.object(notices.subprocess,'run',side_effect=fake):
                notices.safe_curl_json('https://example.com/api?token=SECRET_TOKEN')
            files=list(Path(root).glob('*.jsonl'))
            self.assertEqual(len(files),1)
            text=files[0].read_text();self.assertNotIn('SECRET_TOKEN',text)
            self.assertEqual([json.loads(x)['event'] for x in text.splitlines()],['http_start','http_end'])
    def test_quote_transport_diagnostics_do_not_record_key(self):
        import dashboard_data_sources as sources
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{'DASHBOARD_DIAGNOSTICS_DIR':str(Path(root).resolve())}):
            response=Mock(status_code=200);response.json.return_value={'code':0,'data':{}}
            sources.request_json(sources.HITHINK+'/api/a-share/prices/snapshot',{}, {'X-api-key':'SECRET_TOKEN'},get=Mock(return_value=response))
            files=list(Path(root).glob('*.jsonl'));self.assertEqual(len(files),1)
            self.assertNotIn('SECRET_TOKEN',files[0].read_text())
    def test_failure_has_no_raw_exception(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{'DASHBOARD_DIAGNOSTICS_DIR':str(Path(root).resolve())}), patch.object(notices,'safe_curl_json',side_effect=RuntimeError('SECRET_TOKEN')):
            with self.assertRaises(RuntimeError):notices.fetch_notice_page('601318',1)
            files=list(Path(root).glob('*.jsonl'))
            self.assertEqual(len(files),1,'missing failure diagnostics')
            text=files[0].read_text();self.assertNotIn('SECRET_TOKEN',text)
            self.assertEqual(json.loads(text.splitlines()[-1])['event'],'notice_page_error')
if __name__=='__main__':unittest.main()
