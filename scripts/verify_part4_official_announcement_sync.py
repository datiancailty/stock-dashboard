#!/usr/bin/env python3
"""Offline regression checks for the private Part 4 official-notice sync.

This test uses only synthetic sources and repository code.  It does not load a
private Worker config, Keychain item, Supabase session, personal watchlist,
market document, announcement payload, or network resource.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNC_PATH = ROOT / "scripts" / "part4_official_announcement_sync.py"
MARKET_PATH = ROOT / "scripts" / "update_market.py"
MIGRATION_PATH = ROOT / "supabase" / "migrations" / "20260831010000_personal_part4_official_dividend_notices.sql"
PREFLIGHT_PATH = ROOT / "supabase" / "verification" / "personal_part4_official_dividend_notices_preflight.sql"
POSTFLIGHT_PATH = ROOT / "supabase" / "verification" / "personal_part4_official_dividend_notices_postflight.sql"
POST_SYNC_PATH = ROOT / "supabase" / "verification" / "personal_part4_official_dividend_notices_post_sync.sql"
FUTURE_WORKER_PATH = ROOT / "scripts" / "personal_future_dividend_grid_sync.py"
QUOTE_WORKER_PATH = ROOT / "scripts" / "personal_market_snapshot_sync.py"
APP_PATH = ROOT / "assets" / "app.js"
INDEX_PATH = ROOT / "index.html"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot_import:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def mx_payload(dtos: list[dict]) -> dict:
    return {"data": {"data": {"searchDataResultDTO": {"dataTableDTOList": dtos}}}}


def direct_raw(art_code: str, title: str, columns: list[str], day: str = "2026-08-29") -> dict:
    return {
        "art_code": art_code,
        "title": title,
        "notice_date": f"{day} 00:00:00",
        "columns": [{"column_name": item} for item in columns],
    }


def main() -> None:
    sync = load(SYNC_PATH, "part4_official_sync_test")
    market = load(MARKET_PATH, "part4_legacy_market_test")
    start = sync.date(2026, 8, 1)
    end = sync.date(2026, 8, 31)
    stock = sync.Stock(code="000333", name="合成标的")

    plan = sync.normalize_direct_event(
        stock,
        direct_raw("AN202608281828644258", "合成标的:关于2026年中期利润分配方案的公告", ["分配预案"]),
        start,
        end,
    )
    assert plan is not None
    assert plan["type"] == "分红方案公告" and plan["stage"] == "proposal"
    assert plan["id"] == "eastmoney:AN202608281828644258"
    assert len(plan["sourceHash"]) == 64 and all(ch in "0123456789abcdef" for ch in plan["sourceHash"])
    assert plan["sourceUrl"].endswith("/000333/AN202608281828644258.html")

    board = sync.normalize_direct_event(
        stock,
        direct_raw("AN202608281828644247", "合成标的:董事会会议决议公告", ["分配预案", "董事会决议公告"]),
        start,
        end,
    )
    assert board is not None and board["type"] == "分红相关决议"

    generic = sync.normalize_direct_event(
        stock,
        direct_raw("AN202608281828644246", "合成标的:2026年半年度报告", ["半年度报告全文"]),
        start,
        end,
    )
    assert generic is None, "generic report must not be silently promoted to a dividend notice"

    old_fetch = getattr(sync, "fetch_notice_page")
    calls: list[int] = []

    def fake_fetch(code: str, page: int) -> dict:
        calls.append(page)
        if page == 1:
            return {
                "list": [direct_raw("AN202608281828644258", "合成标的:关于中期利润分配方案的公告", ["分配预案"])],
                "page_size": 1,
                "total_hits": 2,
            }
        return {
            "list": [direct_raw("AN202607010000000001", "合成标的:旧公告", [], "2026-07-01")],
            "page_size": 1,
            "total_hits": 2,
        }

    setattr(sync, "fetch_notice_page", fake_fetch)
    events, candidates, coverage, count = sync.scan_stock(stock, start, end)
    setattr(sync, "fetch_notice_page", old_fetch)
    assert calls == [1, 2]
    assert coverage.complete and coverage.error is None and coverage.pages == 2
    assert len(events) == 1 and not candidates and count == 1

    def fake_unordered_fetch(code: str, page: int) -> dict:
        if page == 1:
            return {"list":[direct_raw("AN202607010000000001","合成标的:旧公告",[],"2026-07-01")],"page_size":1,"total_hits":2}
        return {"list":[direct_raw("AN202608281828644258","合成标的:关于中期利润分配方案的公告",["分配预案"])],"page_size":1,"total_hits":2}
    setattr(sync, "fetch_notice_page", fake_unordered_fetch)
    _, _, unordered_coverage, _ = sync.scan_stock(stock, start, end)
    setattr(sync, "fetch_notice_page", old_fetch)
    assert not unordered_coverage.complete and unordered_coverage.error == "official_notice_cross_page_order_invalid", "unordered pages must fail closed"

    def fake_duplicate_fetch(code: str, page: int) -> dict:
        if page == 1:
            return {"list":[direct_raw("AN202608281828644258","合成标的:关于中期利润分配方案的公告",["分配预案"]),direct_raw("AN202608281828644257","合成标的:分红方案公告",["分配预案"])],"page_size":2,"total_hits":4}
        return {"list":[direct_raw("AN202608281828644257","合成标的:分红方案公告",["分配预案"]),direct_raw("AN202608281828644256","合成标的:权益分派实施公告",["权益分派"])],"page_size":2,"total_hits":4}
    setattr(sync, "fetch_notice_page", fake_duplicate_fetch)
    _, _, duplicate_coverage, _ = sync.scan_stock(stock, start, end)
    setattr(sync, "fetch_notice_page", old_fetch)
    assert not duplicate_coverage.complete and duplicate_coverage.error == "official_notice_pagination_duplicate_identifier", "cross-page duplicate IDs must fail closed"
    assert sync.event_type_for("不实施利润分配的公告", ["分配预案"])[1] == "proposal"

    structured = mx_payload(
        [
            {
                "table": {"headName": ["2026年中期分配"], "f_progress": ["预披露"], "f_plan": ["现金分红金额占净利润的比例为35%"]},
                "nameMap": {"f_progress": "方案进度", "f_plan": "分红方案"},
            }
        ]
    )
    assert sync.extract_structured_interim_pre_disclosure(stock, structured, 2026) == "现金分红金额占净利润的比例为35%"
    assert sync.period_kind("2026年中期分配") == (2026, "interim")
    assert sync.period_kind("2026中报") == (2026, "interim")

    supplement = {**plan, "source": sync.STRUCTURED_SOURCE, "type": "中期分红预披露", "stage": "pre_disclosure"}
    merged = sync.merge_events([plan], [supplement])
    assert len(merged) == 1 and merged[0]["source"] == sync.DIRECT_SOURCE

    try:
        sync.ensure_complete_coverage([stock], [sync.ScanCoverage(code=stock.code, pages=1, complete=False, error="timeout")])
    except sync.SyncError as exc:
        assert exc.category == "official_notice_coverage_incomplete"
    else:
        raise AssertionError("incomplete coverage was accepted")

    assert market.dividend_period("2026年中期分配") == (2026, "interim")
    assert market.dividend_period("2026中期分配") == (2026, "interim")

    payload_blank = mx_payload(
        [
            {
                "title": "合成标的 000333",
                "table": {
                    "headName": ["2026年中期分配"],
                    "每股股利(税前,元)": ["0.2"],
                    "分红方案": ["10派2元"],
                    "方案进度": [""],
                    "股权登记日": ["2026-08-29"],
                    "除权除息日": ["2026-08-30"],
                    "派息日": ["2026-08-31"],
                },
            }
        ]
    )
    _, blank_events = market.parse(payload_blank, [{"code": "000333", "name": "合成标的"}], 2026, {"stocks": [], "events": []}, {"000333": 10}, {}, {})
    assert not blank_events, "blank progress must not become implementation evidence"
    assert market.implementation_status("实施分配")
    assert not market.implementation_status("不实施分配")
    assert not market.implementation_status("预案")
    assert market.planned_status("股东大会通过")
    assert market.planned_status("董事会预案")
    assert not market.planned_status("审核中")
    assert not market.planned_status("预披露")
    assert not market.planned_status("取消分配")

    payload_two_periods = mx_payload(
        [
            {
                "title": "合成标的 000333",
                "table": {
                    "headName": ["2026年度分配", "2026年中期分配"],
                    "每股股利(税前,元)": ["0.2", "0.1"],
                    "分红方案": ["10派2元", "10派1元"],
                    "方案进度": ["实施分配", "实施分配"],
                    "股权登记日": ["2026-08-29", "2026-08-29"],
                    "除权除息日": ["2026-08-30", "2026-08-30"],
                    "派息日": ["2026-08-31", "2026-08-31"],
                },
            }
        ]
    )
    _, same_day_events = market.parse(payload_two_periods, [{"code": "000333", "name": "合成标的"}], 2026, {"stocks": [], "events": []}, {"000333": 10}, {}, {})
    assert len(same_day_events) == 6, "same-day annual/interim events must not collide"

    future_payload = mx_payload(
        [{"title":"合成标的 000333","table":{"headName":["2027年中期分配"],"每股股利(税前,元)":["0.3"],"分红方案":["10派3元"],"方案进度":["股东大会通过"]}}]
    )
    future_stocks, _ = market.parse(future_payload, [{"code":"000333","name":"合成标的"}], 2026, {"stocks":[],"events":[]}, {"000333":10}, {}, {})
    assert future_stocks[0]["futureDividend"] == 0.3 and future_stocks[0]["annualDividend"] == 0 and future_stocks[0]["interimDividend"] == 0
    rebuilt_previous={"stocks":[{**future_stocks[0],"futureDividend":0,"futureDividendStatus":None}],"events":[]}
    rebuilt_stocks, _ = market.parse(future_payload, [{"code":"000333","name":"合成标的"}], 2026, rebuilt_previous, {"000333":10}, {}, {})
    assert rebuilt_stocks[0]["futureDividend"] == 0.3, "repeated complete snapshot must not double future dividend"
    market.assert_dividend_dto_coverage(market.result_dtos(future_payload), [{"code":"000333","name":"合成标的"}])
    try:
        market.assert_dividend_dto_coverage([], [{"code":"000333","name":"合成标的"}])
    except RuntimeError as exc:
        assert str(exc) == "dividend_source_coverage_incomplete"
    else:
        raise AssertionError("missing structured stock response was accepted")
    assert market.dividend_query_batches([{"code":str(i).zfill(6)} for i in range(11)]) and len(market.dividend_query_batches([{"code":str(i).zfill(6)} for i in range(11)])) == 3

    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    preflight = PREFLIGHT_PATH.read_text(encoding="utf-8")
    postflight = POSTFLIGHT_PATH.read_text(encoding="utf-8")
    post_sync = POST_SYNC_PATH.read_text(encoding="utf-8")
    assert migration.count("begin;") == 1 and migration.count("commit;") == 1 and migration.count("$$") >= 6 and migration.count("$$") % 2 == 0
    assert "personal_part4_dividend_notices" in migration
    assert "personal_part4_dividend_notice_sync_runs" in migration
    assert "part4_sync_watchlist_coverage_incomplete" in migration
    assert "part4_sync_event_source_url_invalid" in migration
    assert "sourceHash" in migration
    assert "PART4_WRITER_KEYCHAIN_SERVICE" in SYNC_PATH.read_text(encoding="utf-8")
    assert "part4_writer_secret" in SYNC_PATH.read_text(encoding="utf-8")
    assert "p_watchlist_codes" in SYNC_PATH.read_text(encoding="utf-8")
    assert "part4_sync_watchlist_set_changed" in migration
    assert "worker_declared_input_sha256" in migration
    assert "p_worker_declared_input_sha256" in SYNC_PATH.read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in migration
    assert "create or replace function public.personal_replace_watchlist" in migration
    assert "DISCLOSED_FUTURE_PROGRESS_ALLOWLIST" in MARKET_PATH.read_text(encoding="utf-8")
    market_snapshot_sql = migration.split("create or replace function public.personal_sync_market_snapshot", 1)[1].split("create or replace function public.personal_sync_future_dividend_grid", 1)[0]
    future_grid_sql = migration.split("create or replace function public.personal_sync_future_dividend_grid", 1)[1].split("create or replace function public.personal_get_part4", 1)[0]
    assert market_snapshot_sql.index("pg_advisory_xact_lock") < market_snapshot_sql.index("select count(*) into v_expected_count"), "quote lock must precede current-watchlist validation"
    assert future_grid_sql.index("pg_advisory_xact_lock") < future_grid_sql.index("select count(*) into v_expected_count"), "future-grid lock must precede current-watchlist validation"
    assert "personal_snapshot:" in migration
    assert "coalesce(b.stock, jsonb_build_object('code', w.stock_code, 'name', w.display_name))" in migration
    assert "from jsonb_array_elements(coalesce(d.payload->'events'" in migration
    assert "official_notice_pagination_duplicate_identifier" in SYNC_PATH.read_text(encoding="utf-8")
    assert "market_snapshot_time_regression" in migration
    assert "personal_part4_watchlist_archive_after_delete" in migration
    assert "part4_sync_trusted_writer_required" in migration
    assert "personal_part4_dividend_notice_run_items" in migration
    assert "personal_market_quote_snapshots" in migration
    assert "personal_future_dividend_grid_snapshots" in migration
    assert "personal_sync_future_dividend_grid" in migration
    assert "future_dividend_record_shape_invalid" in migration
    assert "future_dividend_time_regression" in migration
    assert "status = '已公告待实施'" in migration
    assert "future_dividend = 0 and status is null" in migration
    assert "personal_sync_market_snapshot" in migration
    assert QUOTE_WORKER_PATH.exists() and FUTURE_WORKER_PATH.exists()
    future_worker = FUTURE_WORKER_PATH.read_text(encoding="utf-8")
    quote_worker = QUOTE_WORKER_PATH.read_text(encoding="utf-8")
    assert "assert_dividend_dto_coverage" in future_worker
    assert "personal_sync_future_dividend_grid" in future_worker
    assert "personal_sync_market_snapshot" in quote_worker
    assert "market_quote_coverage_incomplete" in quote_worker
    assert "market_snapshot_coverage_incomplete" in migration
    assert "archived_at" in migration
    assert "update public.personal_documents" not in migration.lower()
    assert "public.vps_" not in migration.lower()
    assert "notice_ledger_already_present" in preflight
    assert "authenticated_has_no_direct_ledger_table_privileges" in postflight
    assert "future_dividend_rpc_authenticated_only_with_strict_future_contract" in postflight
    assert "part4_read_rpc_filters_to_current_private_watchlist_and_overlays_private_snapshots" in postflight
    assert "base_private_market_document_matches_migration_fingerprint" in postflight
    assert "latest_run_source_ids_all_exist_in_active_ledger" in post_sync
    assert "current_watchlist_has_complete_private_quote_snapshot" in post_sync
    assert "current_watchlist_has_complete_private_future_grid_snapshot" in post_sync
    assert "base_market_document_fingerprint_matches_migration_baseline" in post_sync

    app = APP_PATH.read_text(encoding="utf-8")
    index = INDEX_PATH.read_text(encoding="utf-8")
    assert "原公告 ↗" in app
    assert "data\\.eastmoney\\.com\\/notices\\/detail" in app
    assert "assets/app.js?v=20260831-part4-official-notice-1" in index

    print("PART4_OFFICIAL_ANNOUNCEMENT_SYNC_OFFLINE_PROBE_OK")
    print("official_title_and_column_classification=true")
    print("generic_report_not_silently_promoted=true")
    print("pagination_and_coverage_gate_fail_closed=true")
    print("structured_pre_disclosure_is_explicitly_labeled=true")
    print("legacy_blank_progress_no_longer_means_implemented=true")
    print("same_day_annual_and_interim_events_preserved=true")
    print("no_private_config_keychain_session_or_network_used=true")


if __name__ == "__main__":
    main()
