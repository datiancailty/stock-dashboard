#!/usr/bin/env python3
"""Offline synthetic-only Part6 tests. Never authenticate or fetch live data."""
import copy
import importlib.util
import unittest
from datetime import date
from pathlib import Path

PATH = Path(__file__).with_name('personal_recommendation_eval_sync.py')


def worker():
    if not PATH.is_file():
        raise AssertionError('Part6 non-AI worker not implemented')
    spec = importlib.util.spec_from_file_location('recommendation_eval_under_test', PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    payload = {'id': 'payload-id-1', 'recommendationId': 'brief-old-1',
               'date': '2026-09-01', 'recommendedAt': '2026-09-01T15:00:00+08:00',
               'code': '600000', 'action': '分批买入', 'snapshot': {'price': 100},
               'evaluation': {'status': 'pending'}}
    context = {'records': [{'sourceId': 'database-source-1', 'payload': payload}],
               'meta': {'updatedAt': '2026-09-01T15:00:00+08:00', 'analysis': {'frozen': True}}}
    bars = [{'date': date(2026, 9, day), 'close': close, 'high': high, 'low': low}
            for day, close, high, low in [(1, 100, 101, 99), (2, 101, 106, 100),
                                          (3, 102, 103, 100), (4, 102, 104, 100)]]
    calendar = {'validFrom': '2026-08-31', 'validThrough': '2026-09-04',
                'tradingDates': ['2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04']}
    return context, bars, calendar


class RefreshTests(unittest.TestCase):
    def test_default_cli_gets_proxy_calendar_from_index_not_weekday_guess(self):
        mod = worker()
        self.assertFalse(mod.parse_args(['--dry-run']).publish)
        self.assertTrue(hasattr(mod, 'proxy_calendar'), 'missing real public index session proxy')
        context, bars, calendar = fixture()
        index = [{'date': date(2026, 8, 28), 'close': 100, 'high': 101, 'low': 99}] + bars
        calls = []
        def fetch(code, start, end):
            calls.append(code)
            return index if code == '399001' else bars
        cal, cutoff = mod.proxy_calendar(context, fetch, '2026-09-04T18:00:00+08:00', None)
        self.assertEqual(calls, ['399001'])
        self.assertEqual(cutoff, '2026-09-04')
        self.assertEqual(cal['source'], 'legacy_sz399001_daily_session_proxy')
        self.assertNotIn('2026-08-31', cal['tradingDates'])
        result = mod.run_refresh(lambda *args: context, lambda: self.fail('dryrun secret read'),
                                 fetch, None, as_of='2026-09-04T18:00:00+08:00')
        self.assertEqual(result['status'], 'dry_run_ok')
        # Before close the same-day daily bar must not be treated as complete.
        cal, cutoff = mod.proxy_calendar(context, fetch, '2026-09-04T12:00:00+08:00', None)
        self.assertEqual(cutoff, '2026-09-03')

    def test_cli_is_dryrun_by_default_and_legacy_loader_has_no_ai_symbols(self):
        mod = worker()
        self.assertTrue(hasattr(mod, 'parse_args'), 'missing usable CLI')
        args = mod.parse_args(['--data-as-of', '2026-09-04'])
        self.assertFalse(args.publish)
        self.assertTrue(mod.parse_args(['--data-as-of', '2026-09-04', '--publish']).publish)
        ns = mod.legacy_namespace(date(2026, 9, 4), lambda *args: [])
        for forbidden in ('main', 'model_analysis', 'deterministic_learning', 'deterministic_brief',
                          'append_recommendation', 'historical_context', 'requests', 'os'):
            self.assertNotIn(forbidden, ns)
        self.assertTrue(hasattr(mod, 'legacy_fetcher'), 'missing isolated historical retrieval')
        class OfflineHTTP:
            class RequestException(Exception):
                pass
            def get(self, *args, **kwargs):
                self.calls += 1
                raise self.RequestException('private upstream error')
        http = OfflineHTTP()
        http.calls = 0
        self.assertEqual(mod.legacy_fetcher(http)('600000', date(2026, 9, 1), date(2026, 9, 4)), [])
        self.assertEqual(http.calls, 2)

    def test_exported_sql_fixture_covers_cas_owner_atomicity(self):
        self.assertTrue('pglite_sql_fixture' in globals(), 'missing executable SQL regression fixture')
        sql = pglite_sql_fixture()
        for token in ('wrong_writer', 'wrong_owner', 'duplicate_id', 'changed_base',
                      'partial_coverage', 'stale_as_of', 'evaluation_extra_key',
                      'metadata_extra_key', 'atomic_metadata_failure', 'positive_roundtrip'):
            self.assertIn(token, sql)

    def test_sql_artifact_has_narrow_rpc_and_race_guard(self):
        sql = PATH.parents[1] / 'supabase/staged/20260909030000_personal_recommendation_eval.sql'
        self.assertTrue(sql.is_file(), 'missing staged atomic evaluation RPC')
        text = sql.read_text()
        self.assertIn('personal_get_recommendation_eval_context', text)
        self.assertIn('personal_sync_recommendation_evaluations', text)
        self.assertIn('share row exclusive mode', text.lower())
        self.assertIn("v_base is distinct from v_item->'previousPayload'", text)
        self.assertIn("v_meta is distinct from p_previous_meta", text)
        self.assertNotIn('update public.personal_trade_records', text.lower())
        self.assertNotIn('insert into public.personal_strategy_recommendations', text.lower())

    def test_transport_defaults_to_dryrun_and_publish_uses_only_narrow_rpc(self):
        mod = worker()
        self.assertTrue(hasattr(mod, 'run_refresh'), 'missing narrow dryrun/publish worker')
        context, bars, calendar = fixture()
        calls = []
        state = copy.deepcopy(context)
        def rpc(name, body):
            calls.append(name)
            if name == 'personal_get_recommendation_eval_context':
                return copy.deepcopy(state)
            self.assertEqual(name, 'personal_sync_recommendation_evaluations')
            self.assertEqual(set(body), {'p_as_of', 'p_records', 'p_previous_meta', 'p_performance', 'p_writer_secret'})
            for patch in body['p_records']:
                row = next(r for r in state['records'] if r['sourceId'] == patch['sourceId'])
                row['payload']['evaluation'] = patch['evaluation']
            state['meta'].update({'asOf': body['p_as_of'], 'performance': body['p_performance']})
            return {'stored': len(body['p_records']), 'asOf': body['p_as_of']}
        def secret():
            return 'synthetic-not-a-real-writer-secret'
        result = mod.run_refresh(rpc, secret, lambda *args: bars, calendar,
                                 as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
        self.assertEqual(result['status'], 'dry_run_ok')
        self.assertEqual(calls, ['personal_get_recommendation_eval_context'])
        self.assertEqual(state, context)
        result = mod.run_refresh(rpc, secret, lambda *args: bars, calendar, publish=True,
                                 as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['verified'])
        self.assertNotIn('synthetic-not', str(result))
        self.assertNotIn('payload-id', str(result))

    def test_transport_failure_does_not_publish_or_leak_upstream_exception(self):
        mod = worker()
        self.assertTrue(hasattr(mod, 'run_refresh'), 'missing safe failure gate')
        context, bars, calendar = fixture()
        calls = []
        def rpc(name, body):
            calls.append(name)
            return context
        def failed(*args):
            raise RuntimeError('SECRET_TOKEN url private-record')
        result = mod.run_refresh(rpc, failed, failed, calendar, publish=True,
                                 as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(calls, ['personal_get_recommendation_eval_context'])
        self.assertNotIn('SECRET', str(result))
        self.assertEqual(result['publishState'], 'not_attempted')

    def test_context_and_time_validation_rejects_stale_or_invalid_bases(self):
        mod = worker()
        context, bars, calendar = fixture()
        cases = []
        duplicate = copy.deepcopy(context)
        duplicate['records'] *= 2
        cases.append(duplicate)
        for change in ({'snapshot': {'price': 0}}, {'snapshot': {'price': float('nan')}},
                       {'code': 'wrong'}, {'recommendedAt': 'bad'}, {'action': ''}):
            bad = copy.deepcopy(context)
            bad['records'][0]['payload'].update(change)
            cases.append(bad)
        for bad in cases:
            with self.subTest(case=len(str(bad))):
                with self.assertRaisesRegex(RuntimeError, 'recommendation_context_invalid'):
                    mod.build_refresh(bad, lambda *args: bars, calendar,
                                      as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
        for as_of, data_as_of in [('2026-09-04', '2026-09-04'),
                                 ('2026-09-03T18:00:00+08:00', '2026-09-04'),
                                 ('2026-09-20T18:00:00+08:00', '2026-09-04')]:
            with self.assertRaisesRegex(RuntimeError, 'recommendation_as_of_invalid'):
                mod.build_refresh(context, lambda *args: bars, calendar,
                                  as_of=as_of, data_as_of=data_as_of)
        context['meta']['asOf'] = '2026-09-04T18:00:00+08:00'
        with self.assertRaisesRegex(RuntimeError, 'recommendation_context_stale'):
            mod.build_refresh(context, lambda *args: bars, calendar,
                              as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')

    def test_regression_of_resolved_or_observation_date_is_rejected(self):
        mod = worker()
        context, bars, calendar = fixture()
        context['records'][0]['payload']['evaluation'] = {
            'status': 'success', 'observedTradingDays': 3, 'firstHitAt': '2026-09-02',
            'evaluatedAt': '2026-09-03T18:00:00+08:00', 'observationAsOf': '2026-09-04'}
        for bar in bars:
            bar['high'] = 104
        with self.assertRaisesRegex(RuntimeError, 'recommendation_outcome_regression'):
            mod.build_refresh(context, lambda *args: bars, calendar,
                              as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')

    def test_missing_active_observations_fail_closed_without_zero_returns(self):
        mod = worker()
        context, bars, calendar = fixture()
        before = copy.deepcopy(context)
        for bad_rows in ([], bars[:-1], bars[:1] + bars[2:]):
            with self.subTest(rows=len(bad_rows)):
                with self.assertRaisesRegex(RuntimeError, 'recommendation_observations_unavailable'):
                    mod.build_refresh(context, lambda *args: bad_rows, calendar,
                                      as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
        self.assertEqual(context, before)

    def test_no_data_can_recover_but_early_success_is_still_active(self):
        mod = worker()
        context, bars, calendar = fixture()
        context['records'][0]['payload']['evaluation'] = {'status': 'no_data'}
        good = mod.build_refresh(context, lambda *args: bars, calendar,
                                 as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
        context['records'][0]['payload']['evaluation'] = good['records'][0]['evaluation']
        with self.assertRaisesRegex(RuntimeError, 'recommendation_observations_unavailable'):
            mod.build_refresh(context, lambda *args: [], calendar,
                              as_of='2026-09-05T18:00:00+08:00', data_as_of='2026-09-04')

    def test_settled_30_day_outcome_is_retained_without_fetch(self):
        mod = worker()
        context, bars, calendar = fixture()
        ev = {'status': 'failed', 'observedTradingDays': 30, 'evaluatedAt': '2026-08-31T18:00:00+08:00'}
        context['records'][0]['payload'].update({'date': '2026-07-01', 'recommendedAt': '2026-07-01'})
        context['records'][0]['payload']['evaluation'] = ev
        def forbidden(*args):
            self.fail('Settled records must not fetch or lose their existing outcome')
        result = mod.build_refresh(context, forbidden, None,
                                   as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
        self.assertEqual(result['records'][0]['evaluation'], ev)
        self.assertEqual(result['performance']['failures'], 1)
        self.assertEqual(result['performance']['retainedSettled'], 1)

    def test_absent_calendar_blocks_active_but_not_nonbuy(self):
        mod = worker()
        context, bars, calendar = fixture()
        with self.assertRaisesRegex(RuntimeError, 'recommendation_calendar_required'):
            mod.build_refresh(context, lambda *args: bars, None,
                              as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
        context['records'][0]['payload']['action'] = '当前不买'
        result = mod.build_refresh(context, lambda *args: [], None,
                                   as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
        self.assertEqual(result['records'][0]['evaluation']['status'], 'not_scored')
        self.assertIsNone(result['performance']['successRate'])

    def test_reuses_legacy_hit_math_without_mutating_base_or_analysis(self):
        mod = worker()
        context, bars, calendar = fixture()
        original = copy.deepcopy(context)
        result = mod.build_refresh(context, lambda *args: bars, calendar,
                                   as_of='2026-09-04T18:00:00+08:00', data_as_of='2026-09-04')
        self.assertEqual(context, original)
        patch = result['records'][0]
        self.assertEqual(patch['sourceId'], 'database-source-1')
        self.assertEqual(patch['previousPayload'], original['records'][0]['payload'])
        self.assertEqual(set(patch), {'sourceId', 'previousPayload', 'evaluation'})
        ev = patch['evaluation']
        self.assertEqual(ev['status'], 'success')
        self.assertEqual(ev['firstHitAt'], '2026-09-02')
        self.assertEqual(ev['tradingDaysToHit'], 1)
        self.assertEqual(ev['observedTradingDays'], 3)
        self.assertEqual(ev['observationAsOf'], '2026-09-04')
        self.assertEqual(ev['evaluatedAt'], '2026-09-04T18:00:00+08:00')
        self.assertEqual(result['performance']['successRate'], 100.0)
        self.assertEqual(result['previousMeta'], original['meta'])


def pglite_rpc_fixture():
    """Synthetic caller shape for the parent harness; dates are relative to test execution."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    mod = worker()
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    cutoff = now.date() - timedelta(days=1)
    start = cutoff - timedelta(days=7)
    week = start - timedelta(days=start.weekday())
    days = [week + timedelta(days=i) for i in range((cutoff-week).days+1)]
    # Deliberately synthetic daily sessions, not claimed to be an exchange calendar.
    bars = [{'date': d, 'close': 103 if d > start else 100,
             'high': 106 if d > start else 104, 'low': 99} for d in days]
    context, _, _ = fixture()
    context['records'][0]['payload'].update({'date': start.isoformat(), 'recommendedAt': start.isoformat()})
    nonbuy = copy.deepcopy(context['records'][0])
    nonbuy['sourceId'] = 'database-source-2'
    nonbuy['payload'].update({'id': 'payload-id-2', 'recommendationId': 'brief-old-2',
                              'action': '当前不买', 'evaluation': {'status': 'not_scored', 'reason': '旧记录保留'}})
    context['records'].append(nonbuy)
    calendar = {'validFrom': week.isoformat(), 'validThrough': cutoff.isoformat(),
                'tradingDates': [d.isoformat() for d in days]}
    plan = mod.build_refresh(context, lambda *args: bars, calendar,
                             as_of=now.isoformat(timespec='seconds'), data_as_of=cutoff.isoformat())
    body = {'p_as_of': plan['asOf'], 'p_records': plan['records'],
            'p_previous_meta': plan['previousMeta'], 'p_performance': plan['performance'],
            'p_writer_secret': 'synthetic_' + 'x'*48}
    return {'context': context, 'body': body}


def pglite_sql_fixture():
    """Standalone DISPOSABLE PGlite regression SQL, never for Hosted SQL Editor.

    --emit-pglite-sql prints bootstrap + real staged migrations + synthetic tests.
    The only compatibility shim is extensions.digest using PostgreSQL sha256.
    The transaction rolls back all fixtures after positive/negative assertions.
    """
    import json
    pack = pglite_rpc_fixture()
    context, body = pack['context'], pack['body']
    def literal(value):
        return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    owner = '11111111-1111-4111-8111-111111111111'
    other = '22222222-2222-4222-8222-222222222222'
    secret = body['p_writer_secret']
    setup = """
-- SYNTHETIC LOCAL TEST DATABASE ONLY.
create role anon; create role authenticated; create role service_role;
create schema auth; create schema extensions;
create table auth.users(id uuid primary key);
create table public.app_usernames(user_id uuid primary key, status text);
create function auth.uid() returns uuid language sql stable as $$
select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
create function extensions.digest(text,text) returns bytea language sql immutable as $$
select sha256(convert_to($1,'UTF8')) where $2='sha256' $$;
create table public.personal_part4_sync_writer_credentials(owner_user_id uuid primary key, secret_sha256 text);
"""
    root = PATH.parents[1]
    setup += (root/'supabase/migrations/20260830000000_personal_dashboard_legacy_stage1.sql').read_text()
    setup += (root/'supabase/staged/20260909030000_personal_recommendation_eval.sql').read_text()
    setup += f"""
begin;
insert into auth.users values ('{owner}'),('{other}');
insert into public.app_usernames values ('{owner}','active'),('{other}','active');
insert into public.personal_part4_sync_writer_credentials
select id,encode(extensions.digest('{secret}','sha256'),'hex') from auth.users;
select set_config('request.jwt.claim.sub','{owner}',true);
"""
    for who in (owner, other):
        setup += f"insert into public.personal_documents(owner_user_id,document_key,payload,source_path,source_sha256) values ('{who}','recommendations_meta',{literal(context['meta'])},'data/strategy-recommendations.json',repeat('a',64));\n"
    for row in context['records']:
        setup += f"insert into public.personal_strategy_recommendations(owner_user_id,source_id,payload,source_sha256) values ('{owner}','{row['sourceId']}',{literal(row['payload'])},repeat('a',64));\n"
    setup += """
create function pg_temp.eval_snapshot() returns jsonb language sql stable as $$
select jsonb_build_object('r',(select jsonb_agg(to_jsonb(r) order by owner_user_id,source_id) from public.personal_strategy_recommendations r),
'd',(select jsonb_agg(to_jsonb(d) order by owner_user_id,document_key) from public.personal_documents d)) $$;
create function pg_temp.reject_meta() returns trigger language plpgsql as $$
begin raise exception 'synthetic_sensitive_upstream_error'; end $$;
"""
    cases = []
    def case(name, change=None, prefix=''):
        changed = copy.deepcopy(body)
        if change:
            change(changed)
        cases.append((name, changed, prefix))
    case('wrong_writer', lambda b: b.update(p_writer_secret='bad'))
    case('wrong_owner', prefix=f"perform set_config('request.jwt.claim.sub','{other}',true);")
    case('anonymous', prefix="perform set_config('request.jwt.claim.sub','',true);")
    case('duplicate_id', lambda b: b['p_records'][1].update(sourceId=b['p_records'][0]['sourceId']))
    case('wrong_id', lambda b: b['p_records'][0].update(sourceId='not-an-existing-id'))
    case('changed_base', lambda b: b['p_records'][0]['previousPayload']['snapshot'].update(price=1))
    case('partial_coverage', lambda b: b['p_records'].pop())
    case('stale_as_of', prefix="update public.personal_documents set payload=jsonb_set(payload,'{asOf}',to_jsonb(now()::text),true) where document_key='recommendations_meta';")
    case('evaluation_extra_key', lambda b: b['p_records'][0]['evaluation'].update(action='卖出'))
    case('no_data_regression', lambda b: b['p_records'][0]['evaluation'].update(status='no_data'))
    case('invalid_stats', lambda b: b['p_performance'].update(successRate=0))
    case('metadata_extra_key', lambda b: b['p_performance'].update(analysis={'doNotWrite':True}))
    case('atomic_metadata_failure', prefix="create trigger synthetic_reject before update on public.personal_documents for each row execute function pg_temp.reject_meta();")
    for name, changed, prefix in cases:
        setup += f"""
do $test$ declare b jsonb := {literal(changed)}; before_state jsonb; begin
  before_state := pg_temp.eval_snapshot();
  begin
    {prefix}
    perform public.personal_sync_recommendation_evaluations(b->>'p_as_of',b->'p_records',b->'p_previous_meta',b->'p_performance',b->>'p_writer_secret');
    raise exception '__unexpected_accept__';
  exception when others then
    if sqlerrm='__unexpected_accept__' or sqlerrm not like 'recommendation_%' then
      raise exception 'test_failed:{name}:%',sqlerrm;
    end if;
  end;
  if pg_temp.eval_snapshot() is distinct from before_state then raise exception 'partial_write:{name}'; end if;
end $test$;
"""
    expected = {'meta': {**context['meta'], 'asOf': body['p_as_of'], 'performance': body['p_performance']},
                'records': [{'sourceId': p['sourceId'], 'payload': {**p['previousPayload'], 'evaluation': p['evaluation']}}
                            for p in body['p_records']]}
    setup += f"""
-- positive_roundtrip: exact getter equality also proves old metadata sibling preservation.
do $test$ declare b jsonb := {literal(body)}; result jsonb; begin
result := public.personal_sync_recommendation_evaluations(b->>'p_as_of',b->'p_records',b->'p_previous_meta',b->'p_performance',b->>'p_writer_secret');
if result->>'stored' <> '2' or public.personal_get_recommendation_eval_context() is distinct from {literal(expected)} then
raise exception 'positive_roundtrip_failed'; end if;
end $test$;
rollback;
select 'part6_synthetic_sql_tests_passed' as result;
"""
    return setup


if __name__ == '__main__':
    import json
    import sys
    if '--emit-pglite-sql' in sys.argv:
        print(pglite_sql_fixture())
    elif '--emit-rpc-fixture' in sys.argv:
        print(json.dumps(pglite_rpc_fixture(), ensure_ascii=False))
    else:
        unittest.main()
