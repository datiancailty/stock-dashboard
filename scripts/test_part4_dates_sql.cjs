'use strict';
/* Real embedded PostgreSQL only. Synthetic data; no fetch, Hosted, browser or VPS. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { PGlite } = require('@electric-sql/pglite');
const { pgcrypto } = require('@electric-sql/pglite/contrib/pgcrypto');
const ROOT = path.resolve(__dirname, '..');
const MIGRATION = path.join(ROOT, 'supabase/staged/20260930021000_part4_dates.sql');
const POSTFLIGHT = path.join(ROOT, 'supabase/staged/20260930022000_part4_dates_postflight.sql');
const U = '00000000-0000-4000-8000-000000000001';
const V = '00000000-0000-4000-8000-000000000002';
const SECRET = 'synthetic_date_writer_capability_1234567890123456';
const OLD_PATH = 'supabase/migrations/20260831010000_personal_part4_official_dividend_notices.sql';
const WRAPPERS = [
  ['supabase/staged/20260909020000_personal_forward_basis.sql', 'personal_get_part4_v2'],
  ['supabase/staged/20260909040000_personal_technical_snapshot.sql', 'personal_get_part4_v3'],
  ['supabase/staged/20260910010000_personal_refresh_observability.sql', 'personal_get_part4_v4'],
];
const read = p => fs.readFileSync(path.join(ROOT, p), 'utf8');
const sha = s => crypto.createHash('sha256').update(s).digest('hex');
function definition(sql, name) {
  const start = sql.indexOf(`create or replace function public.${name}(`);
  assert.ok(start >= 0, `historical function ${name} exists`);
  const end = sql.indexOf('\n$$;', start);
  assert.ok(end >= 0, `historical function ${name} body terminates`);
  return sql.slice(start, end + 4);
}
const results = [];
const observations = {};
async function test(name, fn, independent = false) {
  try { await fn(); results.push({ name, passed: true }); console.log(`PASS ${name}`); }
  catch (e) {
    results.push({ name, passed: false, message: e.message, code: e.code });
    if (!independent) throw e;
    console.log(`FAIL ${name}: ${e.message}`);
  }
}
async function scalar(db, sql, args = []) { return (await db.query(sql, args)).rows[0].x; }
async function auth(db, user = U, role = 'authenticated') {
  await db.exec('reset role');
  await db.query("select set_config('request.jwt.claim.sub',$1,false)", [user || '']);
  await db.exec(`set role ${role}`); // role is a constant controlled by this test.
}
async function setup() {
  const db = new PGlite({ extensions: { pgcrypto } });
  await db.exec(`create role anon; create role authenticated; create role service_role;
    create schema auth; create schema extensions; create extension pgcrypto with schema extensions;
    create table auth.users(id uuid primary key);
    insert into auth.users values('${U}'),('${V}');
    create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
    grant usage on schema auth to authenticated,anon,service_role;
    grant execute on function auth.uid() to authenticated,anon,service_role;
    create function public.personal_current_user_is_active() returns boolean language sql stable as $$select auth.uid() is not null and coalesce(current_setting('test.active',true),'true') <> 'false'$$;
    create table public.personal_watchlist_items(owner_user_id uuid not null references auth.users(id),source_id text not null,stock_code text not null,display_name text not null,payload jsonb not null default '{}',source_sha256 text not null default repeat('0',64), imported_at timestamptz not null default now(),updated_at timestamptz not null default now(),primary key(owner_user_id,source_id),unique(owner_user_id,stock_code));
    create table public.personal_documents(owner_user_id uuid not null references auth.users(id),document_key text not null,payload jsonb not null,source_path text not null default 'synthetic',source_sha256 text not null default repeat('0',64),imported_at timestamptz not null default now(),updated_at timestamptz not null default now(),primary key(owner_user_id,document_key));
    alter table public.personal_watchlist_items enable row level security;
    alter table public.personal_documents enable row level security;`);
  const old = read(OLD_PATH);
  await db.exec(old.slice(old.indexOf('create table if not exists public.personal_part4_dividend_notices'), old.indexOf('-- Serialize whole-list')));
  const tables = ['personal_forward_basis_snapshots', 'personal_technical_snapshots', 'personal_confirmed_dividend_snapshots'];
  for (const [i, [p, name]] of WRAPPERS.entries()) {
    const sql = read(p);
    const start = sql.indexOf(`create table if not exists public.${tables[i]}`);
    await db.exec(sql.slice(start, sql.indexOf('\n);', start) + 3));
  }
  for (const [sql, name, signature] of [
    [old, 'personal_sync_part4_dividend_notices', '(text,text,text,integer,integer,integer,jsonb,text,text,jsonb)'],
    [old, 'personal_get_part4', '()'],
    ...WRAPPERS.map(([p, n]) => [read(p), n, '()']),
  ]) {
    await db.exec(definition(sql, name));
    await db.exec(`revoke all on function public.${name}${signature} from public,anon,service_role; grant execute on function public.${name}${signature} to authenticated;`);
  }
  await db.query("insert into public.personal_part4_sync_writer_credentials(owner_user_id,secret_sha256) values($1,encode(extensions.digest($2,'sha256'),'hex'))", [U, SECRET]);
  for (const user of [U, V]) {
    await db.query('insert into public.personal_watchlist_items(owner_user_id,source_id,stock_code,display_name) values($1,$2,$2,$3)', [user, '600000', user === U ? '测试甲' : '测试乙']);
    await db.query('insert into public.personal_documents(owner_user_id,document_key,payload) values($1,\'market\',$2::jsonb)', [user, JSON.stringify({
      updatedAt: '2026-01-01T01:00:00Z', source: 'synthetic-base', stocks: [{ code: '600000', name: user === U ? '测试甲' : '测试乙', price: 12, annualDividend: 1.1, interimDividend: 0.2 }],
      events: [{ date: '2026-01-05', code: '600000', type: 'legacy-without-id', description: 'preserve exact bytes' }],
    })]);
  }
  await db.query("insert into public.personal_market_quote_snapshots(owner_user_id,stock_code,price,as_of,source_label) values($1,'600000',13,'2026-01-02T01:00:00Z','东方财富公开行情')", [U]);
  await db.query("insert into public.personal_future_dividend_grid_snapshots(owner_user_id,stock_code,future_dividend,status,as_of) values($1,'600000',0.3,'已公告待实施','2026-01-02T01:00:00Z')", [U]);
  await db.query("insert into public.personal_forward_basis_snapshots(owner_user_id,stock_code,payload,as_of) values($1,'600000',$2::jsonb,now())", [U, JSON.stringify({ amount: 1.3, status: 'ready', components: { annual: { amount: 1.1 }, interim: { amount: 0.2 } }, source: 'synthetic forward' })]);
  await db.query("insert into public.personal_confirmed_dividend_snapshots(owner_user_id,stock_code,payload,as_of) values($1,'600000',$2::jsonb,now())", [U, JSON.stringify({ amount: 1.2, status: 'ready', components: [{ amount: 1.2, paymentDate: '2026-08-01' }], source: 'synthetic confirmed' })]);
  await db.query("insert into public.personal_technical_snapshots(owner_user_id,stock_code,payload,as_of,trading_date) values($1,'600000',$2::jsonb,now(),current_date)", [U, JSON.stringify({ weeklyBoll: { upper: 14, middle: 12, lower: 10 }, positions: { day: '高位', week: '中位', month: '低位' }, source: 'synthetic technical', asOf: new Date().toISOString() })]);
  await auth(db);
  const notice = { id: 'eastmoney:AN202609300000001', date: '2026-09-01', code: '600000', name: '测试甲', type: '权益分派公告', stage: 'implementation', title: '测试甲2026年中期权益分派实施公告', description: 'synthetic official notice', source: '东方财富公司公告', sourceUrl: 'https://data.eastmoney.com/notices/detail/600000/AN202609300000001.html', sourceHash: 'a'.repeat(64) };
  await db.query('select public.personal_sync_part4_dividend_notices($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10::jsonb)', ['synthetic-run', '2026-09-01', '2026-09-30', 1, 1, 1, JSON.stringify([notice]), 'b'.repeat(64), SECRET, '["600000"]']);
  const before = await scalar(db, 'select public.personal_get_part4_v4() as x');
  await db.exec('reset role');
  return { db, before, notice };
}
(async () => {
  let db;
  try {
    const fixture = await setup();
    db = fixture.db;
    const { before, notice } = fixture;
    const preservedTables = ['personal_documents', 'personal_market_quote_snapshots', 'personal_future_dividend_grid_snapshots', 'personal_technical_snapshots', 'personal_forward_basis_snapshots', 'personal_confirmed_dividend_snapshots', 'personal_part4_dividend_notices', 'personal_part4_dividend_notice_sync_runs', 'personal_part4_dividend_notice_run_items'];
    const preserved = {};
    for (const table of preservedTables) preserved[table] = await scalar(db, `select jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text) as x from public.${table} t`);
    await test('historical fixtures run actual writer and v4/v3/v2/base getter chain', async () => {
      assert.equal(await scalar(db, 'select count(*)::integer as x from public.personal_part4_dividend_notice_run_items'), 1);
      assert.equal(await scalar(db, "select md5(prosrc) as x from pg_proc where oid='public.personal_get_part4()'::regprocedure"), 'c8243f342412d9dd30946dca83236a2c');
    });
    await test('forward migration creates an empty private date ledger', async () => {
      assert.ok(fs.existsSync(MIGRATION), 'Part4 date ledger migration is missing');
      await db.exec(fs.readFileSync(MIGRATION, 'utf8'));
      assert.equal(await scalar(db, 'select count(*)::integer as x from public.personal_part4_dividend_date_events'), 0);
      assert.equal(await scalar(db, "select relrowsecurity as x from pg_class where oid='public.personal_part4_dividend_date_events'::regclass"), true);
    });
    await test('catalog-only postflight returns one aggregate row with zero failures', async () => {
      assert.ok(fs.existsSync(POSTFLIGHT), 'Part4 date postflight is missing');
      const r = await db.query(fs.readFileSync(POSTFLIGHT, 'utf8'));
      assert.equal(r.rows.length, 1); assert.equal(Number(r.rows[0].failed_count), 0);
      assert.deepEqual(r.rows[0].failed_checks, []);
      assert.ok(Number(r.rows[0].check_count) >= 15);
      observations.postflight = r.rows[0];
      observations.postgresVersion = await scalar(db, 'select version() as x');
    });
    await test('postflight is invariant to SQL Editor search_path', async () => {
      await db.exec('set search_path=pg_catalog');
      try {
        const r = await db.query(fs.readFileSync(POSTFLIGHT, 'utf8'));
        assert.equal(Number(r.rows[0].failed_count), 0, JSON.stringify(r.rows[0]));
      } finally { await db.exec('reset search_path'); }
    });
    await auth(db);
    const get = () => scalar(db, 'select public.personal_get_part4_v4() as x');
    const asOf = new Date(Date.now() - 60000).toISOString();
    const events = [
      ['EQUITY_RECORD_DATE', 'registration', '股权登记日', '2026-09-10'],
      ['EX_DIVIDEND_DATE', 'ex_dividend', '除权除息日', '2026-09-11'],
      ['PAY_CASH_DATE', 'payment', '派息日', '2026-09-12'],
    ].map(([dateField, kind, type, date]) => ({
      ...notice, id: `eastmoney-date:AN202609300000001:600000:2026-06-30:${kind}`,
      date, type, description: `结构化日期核对 · 2026中报 · ${type}`,
      source: '东方财富公司公告 + F10分红日期核对', sourceHash: sha(dateField),
      noticeId: notice.id, noticeDate: notice.date, reportDate: '2026-06-30', reportPeriod: '2026中报', dateField,
    }));
    const write = (ev = events, time = asOf, run = 'synthetic-run', codes = ['600000'], secret = SECRET) => scalar(db,
      'select public.personal_sync_part4_dividend_dates($1,$2,$3::jsonb,$4::jsonb,$5) as x',
      [run, time, JSON.stringify(ev), JSON.stringify(codes), secret]);
    await test('no-date getter is byte-for-byte the historical v4 projection', async () => {
      assert.deepEqual(await get(), before);
    });
    await test('authenticated capability writer publishes registration/ex/payment through actual v4 chain', async () => {
      const receipt = await write();
      assert.equal(receipt.stored, 3);
      const after = await get();
      assert.deepEqual(after.events, [...before.events, ...events]);
      assert.deepEqual(after.stocks, before.stocks);
      assert.equal(after.updatedAt, before.updatedAt);
      assert.equal(after.calendarNoticeUpdatedAt, before.calendarNoticeUpdatedAt);
      assert.equal(new Date(after.calendarDateUpdatedAt).toISOString(), asOf);
      const { calendarDateUpdatedAt, ...rest } = after;
      assert.deepEqual({ ...rest, events: rest.events.slice(0, before.events.length) }, before);
    });
    const later = new Date(Date.parse(asOf) + 1000).toISOString();
    const rejectionCases = [];
    const addEvent = (name, change) => {
      const ev = structuredClone(events); change(ev[0], ev);
      rejectionCases.push([name, () => write(ev, later)]);
    };
    for (const key of Object.keys(events[0])) {
      addEvent(`missing key ${key}`, e => { delete e[key]; });
      addEvent(`nested value ${key}`, e => { e[key] = { private: 'forbidden' }; });
      if (key !== 'reportDate') addEvent(`null ${key}`, e => { e[key] = null; });
    }
    for (const [name, change] of [
      ['unknown key', e => { e.extra = 'forbidden'; }],
      ['wrong name', e => { e.name = 'other'; }],
      ['wrong code', e => { e.code = '600001'; }],
      ['wrong title', e => { e.title += 'different'; }],
      ['wrong noticeId', e => { e.noticeId = 'eastmoney:AN202609300000009'; }],
      ['wrong noticeDate', e => { e.noticeDate = '2026-09-02'; }],
      ['wrong URL', e => { e.sourceUrl = 'https://example.test'; }],
      ['wrong source', e => { e.source = notice.source; }],
      ['wrong hash', e => { e.sourceHash = 'G'.repeat(64); }],
      ['wrong stage', e => { e.stage = 'proposal'; }],
      ['wrong type mapping', e => { e.type = '派息日'; }],
      ['wrong dateField', e => { e.dateField = 'PAY_CASH_DATE'; }],
      ['wrong ID', e => { e.id += '-bad'; }],
      ['impossible date', e => { e.date = '2026-02-30'; }],
      ['date before notice', e => { e.date = '2026-08-01'; }],
      ['reversed event dates', e => { e.date = '2026-09-13'; }],
      ['null non-special report', e => { e.reportDate = null; }],
      ['special report with guessed June', e => { e.reportPeriod = '2026特别分配'; }],
      ['year mismatch', e => { e.reportPeriod = '2025中报'; }],
      ['invalid reportDate', e => { e.reportDate = '2026-02-30'; }],
      ['blank description', e => { e.description = ''; }],
      ['oversize description', e => { e.description = 'x'.repeat(451); }],
      ['trim mismatch', e => { e.name += ' '; }],
      ['duplicate ID', (e, ev) => { ev.push(e); }],
    ]) addEvent(name, change);
    for (const [name, value] of [['null events', null], ['object events', {}], ['scalar event', [1]], ['too many events', Array(1501).fill(events[0])]])
      rejectionCases.push([name, () => write(value, later)]);
    for (const [name, value] of [['null codes', null], ['wrong codes', ['600001']], ['duplicate codes', ['600000', '600000']], ['nested codes', [['600000']]], ['number code', [600000]], ['null code', [null]], ['spaced code', ['600000 ']]])
      rejectionCases.push([name, () => write(events, later, 'synthetic-run', value)]);
    for (const [name, value] of [['null run', null], ['invalid run', 'bad run'], ['unbound run', 'unknown-run']])
      rejectionCases.push([name, () => write(events, later, value)]);
    for (const [name, value] of [['null timestamp', null], ['no timezone', later.slice(0,19)], ['infinite timestamp', 'infinity'], ['invalid timestamp', '2026-02-30T00:00:00Z'], ['old timestamp', '2000-01-01T00:00:00Z'], ['future timestamp', new Date(Date.now()+3600000).toISOString()]])
      rejectionCases.push([name, () => write(events, value)]);
    rejectionCases.push(['no capability', () => write(events, later, 'synthetic-run', ['600000'], null)]);
    rejectionCases.push(['wrong capability', () => write(events, later, 'synthetic-run', ['600000'], 'z'.repeat(48))]);
    for (const [name, action] of rejectionCases) {
      await test(`reject ${name}`, async () => {
        await db.exec('begin');
        try { await assert.rejects(action, /part4_dates_|personal_auth_required/); }
        finally { await db.exec('rollback'); }
      }, true);
    }
    if (results.some(x => !x.passed)) throw new Error('validation matrix failed; every case rolled back');
    for (const [name, action] of [
      ['reordered request with explicit Beijing timezone is idempotent', async () => {
        const bj = new Date(Date.parse(asOf) + 8*3600000).toISOString().replace('Z', '+08:00');
        const r = await write([...events].reverse(), bj); assert.equal(r.idempotent, true); assert.equal(r.stored, 3);
      }],
      ['same timestamp exact request is idempotent', async () => {
        const r = await write(); assert.equal(r.idempotent, true); assert.equal(r.stored, 3);
      }],
      ['same timestamp different payload rejected', async () => {
        const ev = structuredClone(events); ev[0].description += ' changed';
        await assert.rejects(() => write(ev), /part4_dates_same_time_conflict/);
      }],
      ['regressing timestamp rejected', async () => {
        await assert.rejects(() => write(events, new Date(Date.parse(asOf)-1000).toISOString()), /part4_dates_time_regression/);
      }],
      ['empty batch advances watermark without deleting history', async () => {
        const beforeEmpty = await get();
        const r = await write([], later); assert.equal(r.stored, 0);
        assert.deepEqual(await get(), beforeEmpty);
        assert.equal((await write([], later)).idempotent, true);
        await db.exec('savepoint empty_valid');
        await assert.rejects(() => write(events, later), /part4_dates_same_time_conflict/);
        await db.exec('rollback to savepoint empty_valid');
        await assert.rejects(() => write(), /part4_dates_time_regression/);
      }],
      ['partial update checked against retained date rows', async () => {
        const e = { ...events[0], date: '2026-09-13' };
        await assert.rejects(() => write([e], later), /part4_dates_event_order_invalid/);
      }],
      ['period alias cannot bypass date-order comparison', async () => {
        const ev = structuredClone(events); ev[0].date = '2026-09-13'; ev[0].reportPeriod = '2026半年报';
        await assert.rejects(() => write(ev, later), /part4_dates_event_order_invalid/);
      }],
      ['special allocation roundtrip preserves null reportDate', async () => {
        const period = '2026特别分配';
        const ev = events.map(e => ({ ...e, id: e.id.replace('2026-06-30', 'period-'+sha(period).slice(0,16)), reportDate: null, reportPeriod: period }));
        assert.equal((await write(ev, later)).stored, 3);
        const dates = (await get()).events.filter(e => e.reportPeriod === period);
        assert.equal(dates.length, 3); assert.ok(dates.every(e => e.reportDate === null));
      }],
      ['whole batch rollback leaves records and receipt unchanged', async () => {
        const oldResult = await get(); const ev = structuredClone(events);
        ev[0].description += ' valid change'; ev[2].sourceHash = 'invalid';
        await assert.rejects(() => write(ev, later), /part4_dates_/);
        // PostgreSQL errors abort an enclosing transaction, so roll back the test
        // savepoint before comparing actual committed values.
        await db.exec('rollback to savepoint probe');
        assert.deepEqual(await get(), oldResult);
        assert.equal((await write()).idempotent, true);
      }],
    ]) {
      await test(name, async () => {
        await db.exec('begin; savepoint probe');
        try { await action(); } finally { await db.exec('rollback'); }
      }, true);
    }
    if (results.some(x => !x.passed)) throw new Error('state/order matrix failed');
    for (const role of ['anon', 'authenticated', 'service_role']) {
      await auth(db, U, role);
      for (const table of ['personal_part4_dividend_date_events', 'personal_part4_dividend_date_sync_state']) {
        await test(`${role} cannot directly SELECT/INSERT/UPDATE/DELETE date table ${table}`, async () => {
          for (const statement of [
            `select * from public.${table}`,
            `insert into public.${table}(owner_user_id) values('${U}')`,
            `update public.${table} set updated_at=now() where owner_user_id='${U}'`,
            `delete from public.${table} where owner_user_id='${U}'`,
          ]) await assert.rejects(() => db.exec(statement), /permission denied/);
        });
      }
      if (role !== 'authenticated') await test(`${role} cannot call date writer or getter`, async () => {
        await assert.rejects(() => write(), /permission denied/);
        await assert.rejects(() => get(), /permission denied/);
      });
    }
    await test('another authenticated owner cannot read or write first owner dates', async () => {
      await auth(db, V);
      assert.equal((await get()).events.length, 1);
      await assert.rejects(() => write(), /part4_dates_trusted_writer_required/);
      await db.exec('reset role');
      await db.query("insert into public.personal_part4_sync_writer_credentials(owner_user_id,secret_sha256) values($1,encode(extensions.digest($2,'sha256'),'hex'))", [V, SECRET]);
      await auth(db, V);
      await assert.rejects(() => write(), /part4_dates_notice_run_invalid/);
      await auth(db);
    });
    await test('inactive and missing subject cannot write or read dates', async () => {
      await db.exec("select set_config('test.active','false',false)");
      assert.equal(await get(), null);
      await assert.rejects(() => write(), /personal_auth_required/);
      await db.exec("select set_config('test.active','true',false)");
      await auth(db, null);
      assert.equal(await get(), null);
      await assert.rejects(() => write(), /personal_auth_required/);
      await auth(db);
    });
    for (const [name, mutate, hidden] of [
      ['removed current watchlist', `delete from public.personal_watchlist_items where owner_user_id='${U}' and stock_code='600000'`, true],
      ['archived notice', `update public.personal_part4_dividend_notices set archived_at=now() where owner_user_id='${U}'`, true],
      ['changed notice stage', `update public.personal_part4_dividend_notices set stage='proposal' where owner_user_id='${U}'`, true],
      ['changed notice title', `update public.personal_part4_dividend_notices set title='updated title' where owner_user_id='${U}'`, false],
      ['changed watchlist display name', `update public.personal_watchlist_items set display_name='new name' where owner_user_id='${U}'`, false],
      ['run membership missing', `delete from public.personal_part4_dividend_notice_run_items where owner_user_id='${U}' and run_id='synthetic-run'`, false],
      ['stale notice run', `update public.personal_part4_dividend_notice_sync_runs set updated_at=now()-interval '4 days' where owner_user_id='${U}'`, false],
      ['incomplete watchlist coverage', `update public.personal_part4_dividend_notice_sync_runs set expected_watchlist_count=2,scanned_watchlist_count=2 where owner_user_id='${U}'`, false],
    ]) {
      await test(`${name} rejects publication${hidden ? ' and hides retained dates' : ''}`, async () => {
        await db.exec('reset role; begin');
        try {
          await db.exec(mutate); await db.exec('set role authenticated');
          if (hidden) assert.ok(!(await get()).events.some(e => e.id?.startsWith('eastmoney-date:')));
          await assert.rejects(() => write(events, later), /part4_dates_/);
        } finally { await db.exec('rollback'); await auth(db); }
        assert.equal((await get()).events.filter(e => e.id?.startsWith('eastmoney-date:')).length, 3);
      });
    }
    await db.exec('reset role');
    await test('market document and original notice payload remain unchanged', async () => {
      assert.equal(await scalar(db, "select (payload->'events'->0->>'type') as x from public.personal_documents where owner_user_id=$1", [U]), 'legacy-without-id');
      assert.deepEqual(await scalar(db, 'select payload as x from public.personal_part4_dividend_notices where owner_user_id=$1', [U]), notice);
      for (const table of preservedTables) assert.deepEqual(await scalar(db, `select jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text) as x from public.${table} t`), preserved[table], table);
      for (const [p, name] of WRAPPERS) {
        const expected = definition(read(p), name).split('as $$')[1].split('$$;')[0];
        assert.equal(await scalar(db, 'select prosrc as x from pg_proc where oid=$1::regprocedure', [`public.${name}()`]), expected);
      }
    });
    for (const [name, mutation] of [
      ['date table column SELECT grant', 'grant select (payload) on public.personal_part4_dividend_date_events to authenticated'],
      ['date table RLS disabled', 'alter table public.personal_part4_dividend_date_events disable row level security'],
      ['new writer PUBLIC execution', 'grant execute on function public.personal_sync_part4_dividend_dates(text,text,jsonb,jsonb,text) to public'],
      ['new writer source drift', "update pg_proc set prosrc=prosrc||E'\\n--drift' where oid='public.personal_sync_part4_dividend_dates(text,text,jsonb,jsonb,text)'::regprocedure"],
      ['new writer search path', 'alter function public.personal_sync_part4_dividend_dates(text,text,jsonb,jsonb,text) set search_path=public'],
    ]) {
      await test(`postflight detects ${name}`, async () => {
        await db.exec('begin');
        try {
          await db.exec(mutation);
          const r = await db.query(fs.readFileSync(POSTFLIGHT, 'utf8'));
          assert.equal(r.rows.length, 1); assert.ok(Number(r.rows[0].failed_count) > 0);
        } finally { await db.exec('rollback'); }
      });
    }
    await test('migration is single transaction with no top-level business import and no old getter copy', async () => {
      const sql = fs.readFileSync(MIGRATION, 'utf8');
      const topLevel = sql.replace(/\$([a-z_]*)\$[\s\S]*?\$\1\$/g, "''").replace(/--[^\n]*/g, '');
      assert.match(topLevel, /^\s*begin;/i); assert.match(topLevel, /commit;\s*$/i);
      assert.doesNotMatch(topLevel, /\b(insert|update|delete|truncate)\s+(into|from|public\.)/i);
      assert.doesNotMatch(sql, /create (?:or replace )?function public\.personal_get_part4_\w+\(/i);
      assert.doesNotMatch(sql, /\bdelete\s+from\b/i);
    });
    // Each drift test uses a separate real database, never runs the historical
    // migration against Hosted and never changes the candidate source files.
    const drifts = [
      ['getter source drift', "create or replace function public.personal_get_part4() returns jsonb language sql stable security definer set search_path=pg_catalog,public as $$select '{}'::jsonb$$"],
      ['v4 source drift', "create or replace function public.personal_get_part4_v4() returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public as $$begin return '{}'::jsonb; end$$"],
      ['notice writer source drift', "update pg_proc set prosrc=prosrc||E'\\n--drift' where oid='public.personal_sync_part4_dividend_notices(text,text,text,integer,integer,integer,jsonb,text,text,jsonb)'::regprocedure"],
      ['function PUBLIC execution', 'grant execute on function public.personal_get_part4() to public'],
      ['function service-role execution', 'grant execute on function public.personal_get_part4_v4() to service_role'],
      ['function owner drift', 'alter function public.personal_get_part4() owner to authenticated'],
      ['unsafe search path', 'alter function public.personal_get_part4() set search_path=public,pg_catalog'],
      ['not security definer', 'alter function public.personal_get_part4() security invoker'],
      ['notice table grant', 'grant select on public.personal_part4_dividend_notices to authenticated'],
      ['notice RLS disabled', 'alter table public.personal_part4_dividend_notices disable row level security'],
      ['existing date table', 'create table public.personal_part4_dividend_date_events(dummy integer)'],
      ['existing date writer overload', 'create function public.personal_sync_part4_dividend_dates(integer) returns integer language sql as $$select 1$$'],
    ];
    await db.close(); db = null;
    for (const [name, drift] of drifts) {
      await test(`preflight guard rejects ${name}`, async () => {
        const f = await setup();
        try {
          await f.db.exec(drift);
          await assert.rejects(() => f.db.exec(fs.readFileSync(MIGRATION, 'utf8')), /part4_dates_preflight_/);
          await f.db.exec('rollback');
          if (name !== 'existing date table') assert.equal(await scalar(f.db, "select to_regclass('public.personal_part4_dividend_date_events')::text as x"), null);
        } finally { await f.db.close(); }
      }, true);
    }
    if (results.some(x => !x.passed)) throw new Error('preflight guard matrix failed');
  } catch (e) {
    console.error(JSON.stringify({ passed: false, checks: results.filter(x => x.passed).length, message: e.message, code: e.code }));
    process.exitCode = 1;
  } finally {
    if (db) await db.close();
    const report = { passed: !process.exitCode, checks: results.length, results, observations, hostedUsed: false, browserUsed: false, vpsUsed: false, syntheticOnly: true, pgSafeupdateExercised: false, at: new Date().toISOString(), sourceSha256: Object.fromEntries([MIGRATION, POSTFLIGHT, __filename].filter(f => fs.existsSync(f)).map(f => [path.relative(ROOT, f), sha(fs.readFileSync(f))])) };
    if (process.env.PART4_SQL_EVIDENCE_DIR) {
      const dir = path.resolve(process.env.PART4_SQL_EVIDENCE_DIR);
      assert.ok(!dir.startsWith(ROOT + path.sep), 'evidence must stay outside source');
      fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
      const label = process.env.PART4_SQL_PHASE || 'result';
      assert.match(label, /^[a-z0-9_-]+$/);
      fs.writeFileSync(path.join(dir, `${label}.json`), JSON.stringify(report, null, 2) + '\n', { mode: 0o600 });
    }
    console.log(JSON.stringify({ ...report, results: undefined, failed: results.filter(x => !x.passed).map(x => x.name) }));
  }
})();
