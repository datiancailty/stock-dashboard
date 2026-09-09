"""Offline contract tests. No credentials, real provider calls or private writes."""
import contextlib
import copy
import importlib
import importlib.util
import io
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import update_news as legacy

NOW = datetime(2026, 9, 9, 18, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
STOCKS = [{'code': '600036', 'name': '招商银行'}, {'code': '600000', 'name': '浦发银行'}]
RAW = {'code': '600036', 'title': '<b>招商银行利润分配预案</b>',
       'content': '每10股派发现金红利人民币2元', 'date': '2026-09-08 10:00:00',
       'jumpUrl': 'https://example.test/notice', 'informationType': 'NEWS', 'source': '测试新闻'}


class NewsTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('personal_news_sync'), 'private news worker missing')
        return importlib.import_module('personal_news_sync')

    def test_default_public_news_adapter_needs_no_mx_and_covers_requested_window(self):
        m=self.module()
        self.assertTrue(callable(getattr(m,'query_public_notices',None)), 'public notice news route absent')
        pages={'600036': {'page_size':2,'total_hits':2,'list':[
            {'art_code':'AN202609081234567890','title':'招商银行：2026年中期利润分配方案',
             'notice_date':'2026-09-08 00:00:00','codes':[{'stock_code':'600036'}]},
            {'art_code':'AN202401011234567890','title':'旧公告','notice_date':'2024-01-01 00:00:00','codes':[{'stock_code':'600036'}]}]}}
        calls=[]
        def fetch(code,page):calls.append((code,page));return pages[code]
        with patch.object(m.part4,'fetch_notice_page',side_effect=fetch), patch.object(m.requests,'post',side_effect=AssertionError('MX forbidden')):
            rows=m.query_public_notices([STOCKS[0]],'2025-01-01',now=NOW)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['code'],'600036')
        self.assertEqual(calls,[('600036',1)])
        self.assertIn('公告索引',rows[0]['source'])
        self.assertEqual(rows[0]['content'],'')

    def test_nested_weekly_quota_overrides_successful_empty_news_table(self):
        m=self.module()
        class Response:
            def raise_for_status(self):pass
            def json(self):return {'status':0,'data':{'data':{'message':'本周金融数据量已达到上限','llmSearchResponse':{'data':[]}}}}
        with patch.dict(m.os.environ,{'MX_APIKEY':'synthetic'}),patch.object(m.requests,'post',return_value=Response()):
            with self.assertRaises(m.NewsSyncError):m.query_news(STOCKS,'2025-01-01')

    def test_mapping_preserves_legacy_identity_and_news_provenance(self):
        m = self.module()
        rows = m.map_items([RAW, RAW], STOCKS, [], NOW)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['id'], legacy.item_id(STOCKS[0], RAW))
        self.assertEqual(row['title'], legacy.clean_text(RAW['title']))
        self.assertEqual(row['publishedAt'], RAW['date'])
        self.assertEqual(row['type'], 'NEWS')
        self.assertEqual(row['sourceClass'], 'news_search')
        self.assertFalse(row['officialVerified'])
        for k, value in legacy.extract_estimate(legacy.clean_text(RAW['content']), legacy.clean_text(RAW['title'])).items():
            self.assertEqual(row[k], value)
        self.assertEqual(m.map_items([RAW], STOCKS, rows, NOW), [])
        notice = dict(RAW, informationType='公告', jumpUrl='https://example.test/other')
        mapped = m.map_items([notice], STOCKS, [], NOW)[0]
        self.assertEqual(mapped['type'], '公告')
        self.assertFalse(mapped['officialVerified'])

    def test_scan_plan_uses_last_success_with_overlap_and_new_symbol_history(self):
        m = self.module()
        self.assertTrue(callable(getattr(m, 'plan_batches', None)), 'scan planning missing')
        old = {'items': [], 'lastScanAt': '2026-09-07T18:00:00+08:00',
               'updatedAt': '2026-09-09T18:00:00+08:00', 'trackedStockCodes': ['600036']}
        self.assertEqual(m.plan_batches(STOCKS, old, NOW), [
            {'kind': 'incremental', 'since': '2026-09-05', 'codes': ['600036']},
            {'kind': 'history', 'since': '2025-01-01', 'codes': ['600000']}])
        many = [{'code': f'{i:06d}', 'name': f'测试{i}'} for i in range(12)]
        batches = m.plan_batches(many, {'items': []}, NOW)
        self.assertEqual([len(b['codes']) for b in batches], [5, 5, 2])
        self.assertEqual(m.plan_batches([], {'items': []}, NOW), [])
        for bad in [None, {'items': None}, {'items': [], 'lastScanAt': 'bad'},
                    {'items': [], 'lastScanAt': None},
                    {'items': [], 'trackedStockCodes': ['600036', '600036']}]:
            with self.subTest(bad=bad), self.assertRaises(m.NewsSyncError):
                m.plan_batches(STOCKS, bad, NOW)
        with self.assertRaises(m.NewsSyncError):
            m.plan_batches([STOCKS[0], STOCKS[0]], {'items': []}, NOW)

    def test_private_pipeline_defaults_to_dry_run_and_preserves_failure(self):
        m = self.module()
        self.assertTrue(callable(getattr(m, 'sync', None)), 'private pipeline missing')
        old = {'items': [], 'lastScanAt': '2026-09-07T18:00:00+08:00', 'trackedStockCodes': ['600036']}
        original = copy.deepcopy(old)
        writes, searches, pending_readback, readbacks = [], [], [], []
        source_stocks = [dict(s, extraImportedField='preserve') for s in STOCKS]
        def rpc(worker, config, token, name, body):
            if name == 'personal_get_part1':
                return {'watchlist': copy.deepcopy(source_stocks)}
            if name == 'personal_get_part5':
                if pending_readback:
                    written = pending_readback.pop()
                    readbacks.append(True)
                    return dict(old, lastScanAt=written['p_scan_started_at'],
                                trackedStockCodes=[s['code'] for s in STOCKS], items=written['p_items'])
                return old
            writes.append((name, body))
            pending_readback.append(body)
            return {'status': 'ok', 'stored': 1, 'lastScanAt': body['p_scan_started_at']}
        def query(stocks, since):
            searches.append(([s['code'] for s in stocks], since))
            return [RAW] if stocks[0]['code'] == '600036' else []
        with patch.object(m.part4, 'load_private_session', return_value=(object(), {}, 'fake')), \
             patch.object(m.part4, 'private_rpc', side_effect=rpc), \
             patch.object(m.part4, 'part4_writer_secret', return_value='x' * 48) as secret, \
             patch.object(legacy, 'main', side_effect=AssertionError('public main forbidden')):
            summary = m.sync(query_fn=query, clock=lambda: NOW)
            self.assertFalse(summary['published'])
            self.assertTrue(summary['coverageComplete'])
            self.assertEqual(summary['coverageMeaning'], 'successful_search_batches_not_exhaustive_results')
            self.assertEqual(summary['attemptedBatchCount'], 2)
            self.assertEqual(writes, [])
            secret.assert_not_called()
            summary = m.sync(publish=True, query_fn=query, clock=lambda: NOW)
            self.assertTrue(summary['published'])
            self.assertEqual(readbacks, [True])
            self.assertTrue(summary['readbackVerified'])
            self.assertEqual(len(writes), 1)
            name, body = writes[0]
            self.assertEqual(name, 'personal_sync_news')
            self.assertEqual(body['p_expected_last_scan_at'], old['lastScanAt'])
            self.assertEqual(body['p_watchlist'], STOCKS)
            self.assertEqual(body['p_watchlist_snapshot'], source_stocks)
            self.assertEqual(body['p_scan_started_at'], NOW.isoformat(timespec='seconds'))
            self.assertEqual(body['p_items'][0]['id'], legacy.item_id(STOCKS[0], RAW))
            self.assertNotIn('owner_user_id', body)
            calls = 0
            def failing(stocks, since):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError('secret private provider payload')
                return [RAW]
            with self.assertRaisesRegex(m.NewsSyncError, '^news_search_failed_preserving$'):
                m.sync(publish=True, query_fn=failing, clock=lambda: NOW)
            self.assertEqual(len(writes), 1)
            self.assertEqual(old, original)

    def test_provider_contract_distinguishes_empty_results_from_broken_envelopes(self):
        m = self.module()
        self.assertTrue(callable(getattr(m, 'query_news', None)), 'strict legacy provider adapter missing')
        class Response:
            def raise_for_status(self):
                pass
            def json(self):
                return payload
        for payload in [None, {}, {'status': 0}, {'status': 0, 'data': {}},
                        {'status': 0, 'data': {'data': {'llmSearchResponse': {'data': None}}}},
                        {'status': 1, 'message': 'secret'}, {'status': False, 'data': {}}]:
            with self.subTest(payload=payload), patch.dict(m.os.environ, {'MX_APIKEY': 'fake-test-only'}), \
                 patch.object(m.requests, 'post', return_value=Response()), self.assertRaises(m.NewsSyncError):
                m.query_news(STOCKS, '2025-01-01')
        payload = {'status': 0, 'data': {'data': {'llmSearchResponse': {'data': []}}}}
        with patch.dict(m.os.environ, {'MX_APIKEY': 'fake-test-only'}), patch.object(m.requests, 'post', return_value=Response()) as post:
            self.assertEqual(m.query_news(STOCKS, '2025-01-01'), [])
            self.assertEqual(post.call_args.args[0], legacy.API)
            self.assertIn('招商银行、浦发银行自2025-01-01以来', post.call_args.kwargs['json']['query'])

    def test_cli_is_opt_in_and_never_prints_private_exception(self):
        m = self.module()
        self.assertTrue(callable(getattr(m, 'main', None)), 'CLI missing')
        for args, publish in [([], False), (['--dry-run'], False), (['--publish'], True)]:
            out = io.StringIO()
            with patch.object(m, 'sync', return_value={'status': 'ok', 'published': publish}) as sync, contextlib.redirect_stdout(out):
                self.assertEqual(m.main(args), 0)
                sync.assert_called_once_with(publish=publish)
        with patch.object(m, 'sync', side_effect=RuntimeError('private key and article')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(m.main([]), 2)
        self.assertNotIn('private key', out.getvalue())
        self.assertEqual(json.loads(out.getvalue())['category'], 'news_unexpected_failure')

    def test_malformed_news_fails_closed_without_truncating_existing_history(self):
        m = self.module()
        for changes in [{'title': ''}, {'date': ''}, {'date': '2026-02-30'},
                        {'date': '2099-01-01'}, {'source': 'x' * 201},
                        {'jumpUrl': 'https://example.test/a b'}, {'informationType': 'x' * 81}]:
            with self.subTest(changes=changes), self.assertRaises(m.NewsSyncError):
                m.map_items([dict(RAW, **changes)], STOCKS, [], NOW)
        old = [{'id': f'{i:020x}', 'payload': 'keep'} for i in range(1200)]
        original = copy.deepcopy(old)
        rows = m.map_items([RAW], STOCKS, old, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(old, original)
        self.assertEqual(m.map_items([dict(RAW, code='999999', title='无关', content='无关')], STOCKS, [], NOW), [])

    def test_staged_sql_has_narrow_atomic_contract_not_public_table_grants(self):
        path = Path(__file__).resolve().parents[1] / 'supabase/staged/20260909010000_personal_news_sync.sql'
        self.assertTrue(path.exists(), 'manual RPC migration missing')
        sql = path.read_text().lower()
        for required in ['function public.personal_sync_news(', 'p_expected_last_scan_at text',
                         'p_watchlist_snapshot jsonb', 'jsonb_array_elements(p_watchlist_snapshot)',
                         'security definer', 'set search_path = pg_catalog, public',
                         'auth.uid()', 'personal_current_user_is_active()',
                         'personal_part4_sync_writer_credentials', 'extensions.digest(p_writer_secret',
                         'pg_advisory_xact_lock', 'lock table public.personal_watchlist_items in share mode',
                         'news_watchlist_stale', 'news_batch_duplicate_code', 'news_scan_stale',
                         'news_item_duplicate_id', 'news_item_source_invalid',
                         "document_key = 'news_meta'", 'on conflict (owner_user_id, source_id) do nothing',
                         'jsonb_typeof(p_items) is distinct from', 'from public, anon, service_role',
                         'to authenticated']:
            self.assertIn(required, sql)
        import re
        executable = re.sub(r'--[^\n]*', '', sql)
        self.assertNotRegex(executable, r'\b(delete|truncate)\b|disable row level|grant .* on table|p_owner')
        self.assertNotIn("v_item->'inputs' -", sql, 'parenthesize json extraction before subtraction')
        self.assertEqual(executable.count('create or replace function'), 1)
        post = path.with_name('20260909010000_personal_news_sync_postflight.sql')
        self.assertTrue(post.exists(), 'aggregate manual postflight missing')
        self.assertIn('read only', post.read_text().lower())


if __name__ == '__main__':
    unittest.main()
