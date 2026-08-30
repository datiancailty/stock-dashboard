-- Personal dashboard legacy migration — owner-scoped, read-only acceptance stage.
-- MANUAL HOSTED SQL EXECUTION ONLY. This forward migration does not import data,
-- create users, change vps_* objects, enable trading, or retire the legacy admin path.

begin;

create table if not exists public.personal_import_batches (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  source_path text not null,
  source_commit text not null check (source_commit ~ '^[0-9a-f]{40}$'),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  source_bytes bigint not null check (source_bytes >= 0),
  source_record_count bigint check (source_record_count is null or source_record_count >= 0),
  stable_id_set_sha256 text check (stable_id_set_sha256 is null or stable_id_set_sha256 ~ '^[0-9a-f]{64}$'),
  imported_at timestamptz not null default now(),
  import_version smallint not null default 1 check (import_version = 1),
  primary key (owner_user_id, source_path, source_sha256),
  constraint personal_import_batches_path check (source_path ~ '^data/[a-z0-9][a-z0-9._-]*\.json$')
);

create table if not exists public.personal_documents (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  document_key text not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  source_path text not null,
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  imported_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, document_key),
  constraint personal_documents_key check (document_key in (
    'part2_config', 'market', 'news_meta', 'trades_meta', 'feedback_meta',
    'recommendations_meta', 'strategy_profile', 'strategy_analysis',
    'strategy_analysis_checkpoint', 'strategy_backtest_week_day_down_month_mid',
    'strategy_backtest_new_focused_stocks', 'strategy_api_health'
  ))
);

create table if not exists public.personal_watchlist_items (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  source_id text not null,
  stock_code text not null check (stock_code ~ '^[0-9]{6}$'),
  display_name text not null check (char_length(display_name) between 1 and 80),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  imported_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, source_id),
  unique (owner_user_id, stock_code)
);

create table if not exists public.personal_news_items (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  source_id text not null,
  stock_code text check (stock_code is null or stock_code ~ '^[0-9]{6}$'),
  published_at timestamptz,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  imported_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, source_id)
);

create table if not exists public.personal_trade_records (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  source_id text not null,
  trade_date date,
  stock_code text check (stock_code is null or stock_code ~ '^[0-9]{6}$'),
  created_at_source timestamptz,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  imported_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, source_id)
);

create table if not exists public.personal_strategy_feedback (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  source_id text not null,
  recommendation_id text,
  stock_code text check (stock_code is null or stock_code ~ '^[0-9]{6}$'),
  created_at_source timestamptz,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  imported_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, source_id)
);

create table if not exists public.personal_strategy_recommendations (
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  source_id text not null,
  recommendation_id text,
  stock_code text check (stock_code is null or stock_code ~ '^[0-9]{6}$'),
  recommended_at timestamptz,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  imported_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (owner_user_id, source_id)
);

alter table public.personal_import_batches enable row level security;
alter table public.personal_documents enable row level security;
alter table public.personal_watchlist_items enable row level security;
alter table public.personal_news_items enable row level security;
alter table public.personal_trade_records enable row level security;
alter table public.personal_strategy_feedback enable row level security;
alter table public.personal_strategy_recommendations enable row level security;

revoke all on table public.personal_import_batches from PUBLIC, anon, authenticated, service_role;
revoke all on table public.personal_documents from PUBLIC, anon, authenticated, service_role;
revoke all on table public.personal_watchlist_items from PUBLIC, anon, authenticated, service_role;
revoke all on table public.personal_news_items from PUBLIC, anon, authenticated, service_role;
revoke all on table public.personal_trade_records from PUBLIC, anon, authenticated, service_role;
revoke all on table public.personal_strategy_feedback from PUBLIC, anon, authenticated, service_role;
revoke all on table public.personal_strategy_recommendations from PUBLIC, anon, authenticated, service_role;

create index if not exists personal_news_owner_code_idx
  on public.personal_news_items (owner_user_id, stock_code, published_at desc nulls last);
create index if not exists personal_trades_owner_date_idx
  on public.personal_trade_records (owner_user_id, trade_date desc nulls last, source_id);
create index if not exists personal_feedback_owner_created_idx
  on public.personal_strategy_feedback (owner_user_id, created_at_source desc nulls last, source_id);
create index if not exists personal_recommendations_owner_time_idx
  on public.personal_strategy_recommendations (owner_user_id, recommended_at desc nulls last, source_id);

-- Policies are defense in depth. Browser roles still have no direct table privileges;
-- authenticated browser access is only through the narrow SECURITY DEFINER RPCs below.
do $policy$
declare
  table_name text;
begin
  foreach table_name in array array[
    'personal_import_batches', 'personal_documents', 'personal_watchlist_items',
    'personal_news_items', 'personal_trade_records',
    'personal_strategy_feedback', 'personal_strategy_recommendations'
  ] loop
    execute format('drop policy if exists %I on public.%I', table_name || '_owner_select', table_name);
    execute format(
      'create policy %I on public.%I for select to authenticated using (owner_user_id = auth.uid())',
      table_name || '_owner_select', table_name
    );
  end loop;
end;
$policy$;

create or replace function public.personal_current_user_is_active()
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select auth.uid() is not null and exists (
    select 1 from public.app_usernames
     where user_id = auth.uid() and status = 'active'
  );
$$;
revoke all on function public.personal_current_user_is_active() from PUBLIC, anon, service_role;
grant execute on function public.personal_current_user_is_active() to authenticated;

create or replace function public.personal_get_migration_state()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select case when not public.personal_current_user_is_active() then null else jsonb_build_object(
    'schema_version', 1,
    'source_files', (select count(*) from public.personal_import_batches b where b.owner_user_id = auth.uid()),
    'watchlist_count', (select count(*) from public.personal_watchlist_items w where w.owner_user_id = auth.uid()),
    'news_count', (select count(*) from public.personal_news_items n where n.owner_user_id = auth.uid()),
    'trade_count', (select count(*) from public.personal_trade_records t where t.owner_user_id = auth.uid()),
    'feedback_count', (select count(*) from public.personal_strategy_feedback f where f.owner_user_id = auth.uid()),
    'recommendation_count', (select count(*) from public.personal_strategy_recommendations r where r.owner_user_id = auth.uid()),
    'latest_imported_at', (select max(b.imported_at) from public.personal_import_batches b where b.owner_user_id = auth.uid())
  ) end;
$$;
revoke all on function public.personal_get_migration_state() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_migration_state() to authenticated;

create or replace function public.personal_get_part1()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select case when not public.personal_current_user_is_active() then null else jsonb_build_object(
    'watchlist', coalesce((
      select jsonb_agg(w.payload order by w.stock_code)
        from public.personal_watchlist_items w where w.owner_user_id = auth.uid()
    ), '[]'::jsonb)
  ) end;
$$;
revoke all on function public.personal_get_part1() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_part1() to authenticated;

create or replace function public.personal_get_part2()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select case when not public.personal_current_user_is_active() then null else (
    select d.payload from public.personal_documents d
     where d.owner_user_id = auth.uid() and d.document_key = 'part2_config'
  ) end;
$$;
revoke all on function public.personal_get_part2() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_part2() to authenticated;

create or replace function public.personal_get_part4()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select case when not public.personal_current_user_is_active() then null else (
    select d.payload from public.personal_documents d
     where d.owner_user_id = auth.uid() and d.document_key = 'market'
  ) end;
$$;
revoke all on function public.personal_get_part4() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_part4() to authenticated;

create or replace function public.personal_get_part5()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select case when not public.personal_current_user_is_active() then null else
    coalesce((select d.payload from public.personal_documents d
      where d.owner_user_id = auth.uid() and d.document_key = 'news_meta'), '{}'::jsonb)
    || jsonb_build_object('items', coalesce((
      select jsonb_agg(n.payload order by n.published_at desc nulls last, n.source_id)
        from public.personal_news_items n where n.owner_user_id = auth.uid()
    ), '[]'::jsonb)) end;
$$;
revoke all on function public.personal_get_part5() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_part5() to authenticated;

create or replace function public.personal_get_part6()
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
  select case when not public.personal_current_user_is_active() then null else jsonb_build_object(
    'trades', coalesce((select d.payload from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'trades_meta'), '{}'::jsonb)
      || jsonb_build_object('records', coalesce((select jsonb_agg(t.payload order by t.trade_date desc nulls last, t.source_id) from public.personal_trade_records t where t.owner_user_id = auth.uid()), '[]'::jsonb)),
    'feedback', coalesce((select d.payload from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'feedback_meta'), '{}'::jsonb)
      || jsonb_build_object('records', coalesce((select jsonb_agg(f.payload order by f.created_at_source desc nulls last, f.source_id) from public.personal_strategy_feedback f where f.owner_user_id = auth.uid()), '[]'::jsonb)),
    'recommendations', coalesce((select d.payload from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'recommendations_meta'), '{}'::jsonb)
      || jsonb_build_object('records', coalesce((select jsonb_agg(r.payload order by r.recommended_at desc nulls last, r.source_id) from public.personal_strategy_recommendations r where r.owner_user_id = auth.uid()), '[]'::jsonb)),
    'profile', (select d.payload from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'strategy_profile'),
    'analysis', (select d.payload from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'strategy_analysis'),
    'checkpoint', (select d.payload from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'strategy_analysis_checkpoint'),
    'focused_study', (select d.payload from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'strategy_backtest_week_day_down_month_mid'),
    'focused_study_new', (select d.payload from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'strategy_backtest_new_focused_stocks'),
    'api_health', (select d.payload from public.personal_documents d where d.owner_user_id = auth.uid() and d.document_key = 'strategy_api_health')
  ) end;
$$;
revoke all on function public.personal_get_part6() from PUBLIC, anon, service_role;
grant execute on function public.personal_get_part6() to authenticated;

commit;
