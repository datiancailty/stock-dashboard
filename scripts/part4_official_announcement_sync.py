#!/usr/bin/env python3
"""Synchronize Part 4 dividend notices from the official Eastmoney notice index.

This is a trusted local/private worker.  It deliberately does not use GitHub
Pages, GitHub Actions, browser storage, an LLM, or a public ``live`` JSON
artifact as the Part 4 source of truth.

Primary source:
  Eastmoney's public company-announcement index, paginated per current private
  watchlist code.  Every page in the requested window must be fetched before a
  sync can write.  Incomplete coverage fails closed and leaves private data
  unchanged.

Optional secondary source:
  The existing structured dividend query may corroborate a *pre-disclosed*
  interim distribution hidden inside a generic report/board notice.  It never
  changes the formal dividend numerator and is labelled ``中期分红预披露``.
  It is not used to discover ordinary official dividend-plan notices.

Authentication reuses the already configured local private Worker session:
refresh token stays in macOS Keychain and this script prints only aggregate,
sanitary status.  It never calls Codex and cannot trade.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_WORKER_PATH = ROOT / "scripts" / "plus_strategy_worker.py"
NOTICE_API = "https://np-anotice-stock.eastmoney.com/api/security/ann"
MX_DATA_API = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"
BEIJING = ZoneInfo("Asia/Shanghai")
NOTICE_PAGE_SIZE = 100
MAX_PAGES_PER_STOCK = 200
MAX_PARALLEL_NOTICE_REQUESTS = 3
MAX_STRUCTURED_CANDIDATES = 12
PART4_WRITER_KEYCHAIN_SERVICE = "com.datiancailty.stock-dashboard.part4-writer-v1"
PART4_WRITER_SECRET_BYTES = 32
CODE_RE = re.compile(r"^\d{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ART_CODE_RE = re.compile(r"^AN\d{12,32}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")

# Title and official-column matching is intentionally narrow.  A generic annual
# report or board resolution alone does not become a dividend notice.  The
# optional structured reconciliation is the only path for a labelled
# ``中期分红预披露`` record when a report contains a verified distribution policy.
DIVIDEND_TITLE_TERMS = (
    "分红",
    "利润分配",
    "权益分派",
    "派息",
    "派发现金",
    "现金红利",
    "现金股利",
    "股利分配",
)
DIVIDEND_COLUMN_TERMS = ("分配预案", "权益分派", "分红")
GENERIC_INTERIM_DISCLOSURE_TERMS = ("半年度报告", "董事会", "监事会")
IMPLEMENTATION_TITLE_REJECT_TERMS = ("不实施", "未实施", "取消", "终止", "预案", "拟", "待实施", "预披露")
DIRECT_SOURCE = "东方财富公司公告"
STRUCTURED_SOURCE = "东方财富公司公告 + 结构化分红核对"


class SyncError(RuntimeError):
    """A safe category that may be shown in local worker output."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class Stock:
    code: str
    name: str


@dataclass(frozen=True)
class ScanCoverage:
    code: str
    pages: int
    complete: bool
    error: str | None


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def now_bj() -> datetime:
    return datetime.now(BEIJING)


def parse_iso_date(value: str, label: str) -> date:
    if not DATE_RE.fullmatch(str(value or "")):
        raise SyncError(f"{label}_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise SyncError(f"{label}_invalid") from error
    if parsed.isoformat() != value:
        raise SyncError(f"{label}_invalid")
    return parsed


def load_private_worker_module() -> Any:
    spec = importlib.util.spec_from_file_location("part4_private_worker_adapter", PRIVATE_WORKER_PATH)
    if spec is None or spec.loader is None:
        raise SyncError("private_worker_adapter_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_private_session() -> tuple[Any, dict[str, str], str]:
    worker = load_private_worker_module()
    try:
        config = worker.load_config(worker.DEFAULT_WORKER_DIR)
        access_token = worker.refresh_session(config)
    except Exception as error:
        category = getattr(error, "category", "private_session_unavailable")
        raise SyncError(str(category)) from error
    return worker, config, access_token


def part4_writer_secret(worker: Any, config: dict[str, str]) -> str:
    """Load the trusted-writer capability from Keychain, never config or output."""
    try:
        secret = worker.keychain_read(PART4_WRITER_KEYCHAIN_SERVICE, config["username"])
    except Exception as error:
        raise SyncError("part4_writer_not_initialized") from error
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,160}", secret):
        raise SyncError("part4_writer_secret_invalid")
    return secret


def initialize_part4_writer(worker: Any, config: dict[str, str], *, rotate: bool) -> str:
    """Generate a local-only writer capability and return its non-secret digest."""
    import secrets
    if not rotate:
        try:
            worker.keychain_read(PART4_WRITER_KEYCHAIN_SERVICE, config["username"])
        except Exception:
            pass
        else:
            raise SyncError("part4_writer_already_initialized")
    secret = secrets.token_urlsafe(PART4_WRITER_SECRET_BYTES)
    try:
        worker.keychain_write(PART4_WRITER_KEYCHAIN_SERVICE, config["username"], secret)
    except Exception as error:
        raise SyncError("part4_writer_keychain_write_failed") from error
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def private_rpc(worker: Any, config: dict[str, str], access_token: str, name: str, body: dict[str, Any]) -> Any:
    try:
        return worker.private_rpc(config, access_token, name, body)
    except Exception as error:
        category = getattr(error, "category", "private_rpc_failed")
        raise SyncError(str(category)) from error


def private_watchlist(worker: Any, config: dict[str, str], access_token: str) -> list[Stock]:
    payload = private_rpc(worker, config, access_token, "personal_get_part1", {})
    if not isinstance(payload, dict) or not isinstance(payload.get("watchlist"), list):
        raise SyncError("private_watchlist_shape_invalid")
    result: list[Stock] = []
    seen: set[str] = set()
    for item in payload["watchlist"]:
        if not isinstance(item, dict):
            raise SyncError("private_watchlist_item_invalid")
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        if not CODE_RE.fullmatch(code) or not name or len(name) > 80 or code in seen:
            raise SyncError("private_watchlist_item_invalid")
        seen.add(code)
        result.append(Stock(code=code, name=name))
    if not result or len(result) > 50:
        raise SyncError("private_watchlist_count_invalid")
    return sorted(result, key=lambda item: item.code)


def safe_curl_json(url: str) -> dict[str, Any]:
    """Use macOS/system curl with verified TLS; do not weaken certificate checks."""
    temporary = tempfile.NamedTemporaryFile(prefix="part4-official-notice-", suffix=".json", delete=False)
    temporary.close()
    target = Path(temporary.name)
    try:
        last_error: str | None = None
        for attempt in range(1, 4):
            try:
                completed = subprocess.run(
                    [
                        "/usr/bin/curl",
                        "--fail",
                        "--silent",
                        "--show-error",
                        "--location",
                        "--connect-timeout",
                        "15",
                        "--max-time",
                        "45",
                        "-A",
                        "Mozilla/5.0",
                        "-e",
                        "https://data.eastmoney.com/",
                        url,
                        "-o",
                        str(target),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=55,
                    check=False,
                )
                if completed.returncode == 0:
                    payload = json.loads(target.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        return payload
                    last_error = "json_shape"
                else:
                    last_error = f"curl_{completed.returncode}"
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
                last_error = "transport_or_json"
            if attempt < 3:
                time.sleep(attempt)
        raise SyncError("official_notice_transport_failed") from RuntimeError(last_error or "unknown")
    finally:
        target.unlink(missing_ok=True)


def announcement_url(code: str, art_code: str) -> str:
    return f"https://data.eastmoney.com/notices/detail/{code}/{art_code}.html"


def normalize_columns(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            label = str(item.get("column_name") or "").strip()
            if label:
                result.append(label[:80])
    return result


def official_notice_date(raw: dict[str, Any]) -> str | None:
    value = str(raw.get("notice_date") or raw.get("noticeDate") or "")[:10]
    try:
        return parse_iso_date(value, "official_notice_date").isoformat()
    except SyncError:
        return None


def is_direct_dividend_notice(title: str, columns: Iterable[str]) -> bool:
    return any(term in title for term in DIVIDEND_TITLE_TERMS) or any(
        any(term in column for term in DIVIDEND_COLUMN_TERMS) for column in columns
    )


def event_type_for(title: str, columns: Iterable[str]) -> tuple[str, str]:
    column_text = " ".join(columns)
    implementation = "实施" in title and not any(term in title for term in IMPLEMENTATION_TITLE_REJECT_TERMS)
    if "权益分派" in title or "权益分派" in column_text:
        return "权益分派公告", "implementation" if implementation else "proposal"
    if "董事会" in title or "监事会" in title:
        return "分红相关决议", "proposal"
    return "分红方案公告", "implementation" if implementation else "proposal"


def source_evidence_hash(stock: Stock, art_code: str, event_date: str, title: str, columns: list[str]) -> str:
    """Stable hash of the source-index fields used for this calendar record."""
    return sha256_json(
        {
            "source": "eastmoney_official_announcement_index",
            "artCode": art_code,
            "stockCode": stock.code,
            "noticeDate": event_date,
            "title": title,
            "columns": columns,
        }
    )


def normalize_direct_event(stock: Stock, raw: dict[str, Any], window_start: date, window_end: date) -> dict[str, Any] | None:
    event_date = official_notice_date(raw)
    if event_date is None:
        return None
    parsed_date = date.fromisoformat(event_date)
    if not (window_start <= parsed_date <= window_end):
        return None
    art_code = str(raw.get("art_code") or raw.get("artCode") or "").strip()
    title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()
    columns = normalize_columns(raw.get("columns"))
    if not ART_CODE_RE.fullmatch(art_code) or not title or len(title) > 300:
        return None
    if not is_direct_dividend_notice(title, columns):
        return None
    event_type, stage = event_type_for(title, columns)
    return {
        "id": f"eastmoney:{art_code}",
        "date": event_date,
        "code": stock.code,
        "name": stock.name,
        "type": event_type,
        "stage": stage,
        "title": title,
        "description": f"官方公告 · {title}",
        "source": DIRECT_SOURCE,
        "sourceUrl": announcement_url(stock.code, art_code),
        "sourceHash": source_evidence_hash(stock, art_code, event_date, title, columns),
    }


def normalize_generic_candidate(stock: Stock, raw: dict[str, Any], window_start: date, window_end: date) -> dict[str, Any] | None:
    event_date = official_notice_date(raw)
    if event_date is None or not (window_start <= date.fromisoformat(event_date) <= window_end):
        return None
    art_code = str(raw.get("art_code") or raw.get("artCode") or "").strip()
    title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()
    if not ART_CODE_RE.fullmatch(art_code) or not title:
        return None
    columns = normalize_columns(raw.get("columns"))
    if any(term in title for term in GENERIC_INTERIM_DISCLOSURE_TERMS):
        return {
            "code": stock.code,
            "name": stock.name,
            "date": event_date,
            "art_code": art_code,
            "title": title,
            "source_hash": source_evidence_hash(stock, art_code, event_date, title, columns),
        }
    return None


def fetch_notice_page(code: str, page: int) -> dict[str, Any]:
    params = {
        "sr": "-1",
        "page_size": str(NOTICE_PAGE_SIZE),
        "page_index": str(page),
        "ann_type": "A",
        "client_source": "web",
        "f_node": "0",
        "s_node": "0",
        "stock_list": code,
    }
    payload = safe_curl_json(f"{NOTICE_API}?{urlencode(params)}")
    if payload.get("success") not in (1, True, "1"):
        raise SyncError("official_notice_response_failed")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise SyncError("official_notice_response_shape_invalid")
    return data


def scan_stock(stock: Stock, window_start: date, window_end: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], ScanCoverage, int]:
    direct: list[dict[str, Any]] = []
    generic: list[dict[str, Any]] = []
    seen_page_fingerprints: set[str] = set()
    seen_art_codes: set[str] = set()
    page = 1
    official_notice_count = 0
    declared_total_hits: int | None = None
    declared_page_size: int | None = None
    previous_page_oldest: date | None = None
    try:
        while page <= MAX_PAGES_PER_STOCK:
            data = fetch_notice_page(stock.code, page)
            rows = data.get("list") or []
            if not isinstance(rows, list):
                raise SyncError("official_notice_page_rows_invalid")
            try:
                total_hits = int(data.get("total_hits"))
                page_size = int(data.get("page_size"))
            except (TypeError, ValueError) as error:
                raise SyncError("official_notice_page_metadata_invalid") from error
            if total_hits < 0 or page_size < 1 or page_size > NOTICE_PAGE_SIZE or len(rows) > page_size:
                raise SyncError("official_notice_page_metadata_invalid")
            if declared_total_hits is None:
                declared_total_hits, declared_page_size = total_hits, page_size
            elif total_hits != declared_total_hits or page_size != declared_page_size:
                raise SyncError("official_notice_page_metadata_changed")
            expected_pages = max(1, (total_hits + page_size - 1) // page_size)
            if expected_pages > MAX_PAGES_PER_STOCK:
                raise SyncError("official_notice_page_cap_exceeded")
            if page < expected_pages and not rows:
                raise SyncError("official_notice_page_missing")
            if page == expected_pages and len(rows) != max(0, declared_total_hits - declared_page_size * (page - 1)):
                raise SyncError("official_notice_page_count_mismatch")
            identifiers = [str(item.get("art_code") or item.get("artCode") or "") for item in rows if isinstance(item, dict)]
            if len(identifiers) != len(rows) or any(not ART_CODE_RE.fullmatch(value) for value in identifiers):
                raise SyncError("official_notice_page_identifier_invalid")
            if len(set(identifiers)) != len(identifiers) or any(value in seen_art_codes for value in identifiers):
                raise SyncError("official_notice_pagination_duplicate_identifier")
            seen_art_codes.update(identifiers)
            fingerprint = sha256_json(identifiers)
            if fingerprint in seen_page_fingerprints and rows:
                raise SyncError("official_notice_pagination_repeated")
            seen_page_fingerprints.add(fingerprint)
            page_dates: list[date] = []
            for raw in rows:
                if not isinstance(raw, dict):
                    raise SyncError("official_notice_row_invalid")
                text_date = official_notice_date(raw)
                if text_date is None:
                    raise SyncError("official_notice_row_date_invalid")
                row_date = date.fromisoformat(text_date)
                page_dates.append(row_date)
                if window_start <= row_date <= window_end:
                    official_notice_count += 1
                event = normalize_direct_event(stock, raw, window_start, window_end)
                if event is not None:
                    direct.append(event)
                candidate = normalize_generic_candidate(stock, raw, window_start, window_end)
                if candidate is not None:
                    generic.append(candidate)
            if any(page_dates[index] < page_dates[index + 1] for index in range(len(page_dates) - 1)):
                raise SyncError("official_notice_page_order_invalid")
            if previous_page_oldest is not None and page_dates and previous_page_oldest < page_dates[0]:
                raise SyncError("official_notice_cross_page_order_invalid")
            if page_dates:
                previous_page_oldest = page_dates[-1]
            if page == expected_pages:
                return direct, generic, ScanCoverage(stock.code, page, True, None), official_notice_count
            page += 1
        return direct, generic, ScanCoverage(stock.code, MAX_PAGES_PER_STOCK, False, "page_cap_reached"), official_notice_count
    except SyncError as error:
        return direct, generic, ScanCoverage(stock.code, page, False, error.category), official_notice_count


def scan_watchlist(stocks: list[Stock], window_start: date, window_end: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ScanCoverage], int]:
    direct: list[dict[str, Any]] = []
    generic: list[dict[str, Any]] = []
    coverage: list[ScanCoverage] = []
    official_notice_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL_NOTICE_REQUESTS) as pool:
        futures = [pool.submit(scan_stock, stock, window_start, window_end) for stock in stocks]
        for future in concurrent.futures.as_completed(futures):
            events, candidates, result, count = future.result()
            direct.extend(events)
            generic.extend(candidates)
            coverage.append(result)
            official_notice_count += count
    direct_by_id: dict[str, dict[str, Any]] = {}
    for item in direct:
        key = str(item["id"])
        existing = direct_by_id.get(key)
        if existing is not None and existing != item:
            raise SyncError("official_notice_id_conflict")
        direct_by_id[key] = item
    generic_by_id: dict[str, dict[str, Any]] = {}
    for item in generic:
        key = f"eastmoney:{item['art_code']}"
        if key not in direct_by_id:
            generic_by_id[key] = item
    return (
        sorted(direct_by_id.values(), key=lambda item: (item["date"], item["code"], item["id"])),
        sorted(generic_by_id.values(), key=lambda item: (item["date"], item["code"], item["art_code"])),
        sorted(coverage, key=lambda item: item.code),
        official_notice_count,
    )


def dto_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    outer = payload.get("data")
    inner = outer.get("data") if isinstance(outer, dict) else None
    search = inner.get("searchDataResultDTO") if isinstance(inner, dict) else None
    value = search.get("dataTableDTOList") if isinstance(search, dict) else None
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def table_values(dto: dict[str, Any], *labels: str) -> list[Any]:
    raw_table = dto.get("table")
    table: dict[str, Any] = raw_table if isinstance(raw_table, dict) else {}
    for label in labels:
        value = table.get(label)
        if isinstance(value, list):
            return value
    raw_name_map = dto.get("nameMap")
    if isinstance(raw_name_map, list):
        name_map: dict[str, Any] = {str(index): value for index, value in enumerate(raw_name_map)}
    elif isinstance(raw_name_map, dict):
        name_map = raw_name_map
    else:
        name_map = {}
    wanted = {str(label).strip() for label in labels}
    for key, mapped in name_map.items():
        value = table.get(str(key))
        if str(mapped).strip() in wanted and isinstance(value, list):
            return value
    return []


def period_kind(label: Any) -> tuple[int, str] | None:
    text = re.sub(r"\s+", "", str(label or ""))
    match = re.search(r"(20\d{2})(?:年)?(年度分配|年报|年度|中期分配|中报|半年度|中期)", text)
    if not match:
        return None
    return int(match.group(1)), "annual" if match.group(2) in ("年度分配", "年报", "年度") else "interim"


def structured_dividend_payload(stock: Stock, previous_complete_year: int) -> dict[str, Any]:
    key = os.getenv("MX_APIKEY")
    if not key:
        raise SyncError("mx_apikey_missing")
    query = (
        f"{stock.name}{previous_complete_year}年度及{previous_complete_year + 1}年中期现金分红明细，"
        "列出年度分配和中期分配的方案进度、每股股利税前、分红方案、股权登记日、除权除息日、派息日"
    )
    try:
        response = requests.post(
            MX_DATA_API,
            headers={"apikey": key, "Content-Type": "application/json"},
            json={"toolQuery": query},
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise SyncError("structured_dividend_transport_failed") from error
    if not isinstance(payload, dict) or payload.get("status") != 0:
        raise SyncError("structured_dividend_response_failed")
    return payload


def extract_structured_interim_pre_disclosure(stock: Stock, payload: dict[str, Any], year: int) -> str | None:
    plans: list[str] = []
    for dto in dto_list(payload):
        raw_table = dto.get("table")
        table: dict[str, Any] = raw_table if isinstance(raw_table, dict) else {}
        raw_heads = table.get("headName")
        heads: list[Any] = raw_heads if isinstance(raw_heads, list) else []
        progress = table_values(dto, "方案进度", "分红方案进度")
        plan_values = table_values(dto, "分红方案")
        for index, label in enumerate(heads):
            parsed = period_kind(label)
            if parsed != (year, "interim"):
                continue
            status = str(progress[index] if index < len(progress) else "").strip()
            plan = re.sub(r"\s+", " ", str(plan_values[index] if index < len(plan_values) else "")).strip()
            if "预披露" in status and "现金分红" in plan:
                plans.append(plan[:300])
    return sorted(set(plans))[0] if plans else None


def structured_pre_disclosures(
    candidates: list[dict[str, Any]], window_end: date, *, enabled: bool, requested_codes: set[str]
) -> tuple[list[dict[str, Any]], int, int]:
    if not enabled:
        return [], 0, 0
    if not requested_codes:
        raise SyncError("structured_codes_required")
    by_code: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        code = str(item["code"])
        if code in requested_codes:
            by_code.setdefault(code, []).append(item)
    if set(by_code) != requested_codes:
        raise SyncError("structured_candidate_not_found")
    if len(by_code) > MAX_STRUCTURED_CANDIDATES:
        raise SyncError("structured_candidate_cap_exceeded")
    results: list[dict[str, Any]] = []
    checked = 0
    failures = 0
    for code in sorted(by_code):
        entries = sorted(by_code[code], key=lambda item: (str(item["date"]), str(item["title"])), reverse=True)
        source_item = next((item for item in entries if "半年度报告" in str(item["title"])), entries[0])
        stock = Stock(code=code, name=str(source_item["name"]))
        checked += 1
        try:
            payload = structured_dividend_payload(stock, window_end.year - 1)
            plan = extract_structured_interim_pre_disclosure(stock, payload, window_end.year)
        except SyncError as error:
            raise SyncError("structured_dividend_corroboration_failed") from error
        if not plan:
            raise SyncError("structured_dividend_corroboration_missing")
        results.append(
            {
                "id": f"eastmoney:{source_item['art_code']}",
                "date": str(source_item["date"]),
                "code": stock.code,
                "name": stock.name,
                "type": "中期分红预披露",
                "stage": "pre_disclosure",
                "title": f"{stock.name}: {window_end.year}中期分红预披露",
                "description": f"结构化分红核对 · {plan}",
                "source": STRUCTURED_SOURCE,
                "sourceUrl": announcement_url(stock.code, str(source_item["art_code"])),
                "sourceHash": str(source_item["source_hash"]),
            }
        )
    return results, checked, failures


def ensure_complete_coverage(stocks: list[Stock], coverage: list[ScanCoverage]) -> None:
    expected = {stock.code for stock in stocks}
    got = {item.code for item in coverage}
    if got != expected or any(not item.complete or item.error for item in coverage):
        raise SyncError("official_notice_coverage_incomplete")


def merge_events(direct: list[dict[str, Any]], supplements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for event in [*direct, *supplements]:
        key = str(event["id"])
        existing = merged.get(key)
        # A direct plan notice is stronger than the generic-report structured
        # supplement sharing the same official article ID.
        if existing is not None and existing.get("source") == DIRECT_SOURCE:
            continue
        merged[key] = event
    return sorted(merged.values(), key=lambda item: (item["date"], item["code"], item["id"]))


def aggregate_summary(
    stocks: list[Stock],
    coverage: list[ScanCoverage],
    official_notice_count: int,
    direct: list[dict[str, Any]],
    supplements: list[dict[str, Any]],
    structured_checked: int,
    structured_failures: int,
    window_start: date,
    window_end: date,
) -> dict[str, Any]:
    events = merge_events(direct, supplements)
    type_counts: dict[str, int] = {}
    for item in events:
        label = str(item["type"])
        type_counts[label] = type_counts.get(label, 0) + 1
    return {
        "windowStart": window_start.isoformat(),
        "windowEnd": window_end.isoformat(),
        "watchlistCount": len(stocks),
        "scannedWatchlistCount": sum(1 for item in coverage if item.complete and not item.error),
        "coverageComplete": len(stocks) == len(coverage) and all(item.complete and not item.error for item in coverage),
        "officialNoticeCount": official_notice_count,
        "selectedDividendNoticeCount": len(events),
        "directOfficialDividendNoticeCount": len(direct),
        "structuredPreDisclosureCount": len(supplements),
        "structuredCandidateCount": structured_checked,
        "structuredFailureCount": structured_failures,
        "selectedByType": type_counts,
        "source": "eastmoney_official_announcement_api",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Part 4 official dividend-announcement scanner/synchronizer")
    parser.add_argument("command", choices=("audit", "sync", "init-writer"))
    parser.add_argument("--from", dest="window_start", help="YYYY-MM-DD inclusive")
    parser.add_argument("--to", dest="window_end", help="YYYY-MM-DD inclusive")
    parser.add_argument("--rotate-writer", action="store_true", help="replace the local Keychain writer capability")
    parser.add_argument(
        "--include-structured-pre-disclosures",
        action="store_true",
        help="corroborate generic interim disclosures with the existing structured dividend source",
    )
    parser.add_argument(
        "--structured-code",
        action="append",
        default=[],
        metavar="CODE",
        help="six-digit code to explicitly corroborate as a structured interim pre-disclosure; repeatable",
    )
    parser.add_argument("--dry-run", action="store_true", help="scan only; never invoke the private write RPC")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "init-writer":
            if args.window_start or args.window_end or args.structured_code or args.include_structured_pre_disclosures or args.dry_run:
                raise SyncError("part4_writer_init_arguments_invalid")
            worker = load_private_worker_module()
            try:
                config = worker.load_config(worker.DEFAULT_WORKER_DIR)
            except Exception as error:
                raise SyncError("private_worker_config_unavailable") from error
            digest = initialize_part4_writer(worker, config, rotate=bool(args.rotate_writer))
            print(json.dumps({"status":"writer_initialized","writerSecretSha256":digest,"secret_not_emitted":True},ensure_ascii=False))
            return 0
        if not args.window_start or not args.window_end:
            raise SyncError("window_range_required")
        window_start = parse_iso_date(args.window_start, "window_start")
        window_end = parse_iso_date(args.window_end, "window_end")
        if window_start > window_end or (window_end - window_start).days > 366:
            raise SyncError("window_range_invalid")
        requested_structured_codes = {str(code).strip() for code in args.structured_code}
        if any(not CODE_RE.fullmatch(code) for code in requested_structured_codes):
            raise SyncError("structured_code_invalid")
        if requested_structured_codes and not args.include_structured_pre_disclosures:
            raise SyncError("structured_flag_required")
        if args.include_structured_pre_disclosures and not requested_structured_codes:
            raise SyncError("structured_codes_required")
        worker, config, access_token = load_private_session()
        stocks = private_watchlist(worker, config, access_token)
        direct, candidates, coverage, official_notice_count = scan_watchlist(stocks, window_start, window_end)
        ensure_complete_coverage(stocks, coverage)
        supplements, structured_checked, structured_failures = structured_pre_disclosures(
            candidates,
            window_end,
            enabled=bool(args.include_structured_pre_disclosures),
            requested_codes=requested_structured_codes,
        )
        events = merge_events(direct, supplements)
        summary = aggregate_summary(
            stocks,
            coverage,
            official_notice_count,
            direct,
            supplements,
            structured_checked,
            structured_failures,
            window_start,
            window_end,
        )
        if args.command == "audit" or args.dry_run:
            print(json.dumps({"status": "audit_ok", **summary, "private_payload_not_emitted": True}, ensure_ascii=False))
            return 0
        run_id = f"part4-{window_end.isoformat()}-{now_bj().strftime('%H%M%S')}"
        if not RUN_ID_RE.fullmatch(run_id):
            raise SyncError("run_id_invalid")
        input_sha = sha256_json(
            {
                "schemaVersion": 1,
                "windowStart": window_start.isoformat(),
                "windowEnd": window_end.isoformat(),
                "watchlistCodes": [stock.code for stock in stocks],
                "officialNoticeCount": official_notice_count,
                "events": events,
            }
        )
        writer_secret = part4_writer_secret(worker, config)
        written = private_rpc(
            worker,
            config,
            access_token,
            "personal_sync_part4_dividend_notices",
            {
                "p_run_id": run_id,
                "p_window_start": window_start.isoformat(),
                "p_window_end": window_end.isoformat(),
                "p_expected_watchlist_count": len(stocks),
                "p_scanned_watchlist_count": summary["scannedWatchlistCount"],
                "p_official_notice_count": official_notice_count,
                "p_events": events,
                "p_worker_declared_input_sha256": input_sha,
                "p_writer_secret": writer_secret,
                "p_watchlist_codes": [stock.code for stock in stocks],
            },
        )
        if not isinstance(written, dict):
            raise SyncError("private_sync_response_invalid")
        print(
            json.dumps(
                {
                    "status": "ok",
                    **summary,
                    "published": True,
                    "privateWrite": {
                        "requested": written.get("requested"),
                        "stored": written.get("stored"),
                        "calendarNoticeCount": written.get("calendar_notice_count"),
                    },
                    "private_payload_not_emitted": True,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except SyncError as error:
        print(json.dumps({"status": "error", "category": error.category}, ensure_ascii=False))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "error", "category": "cancelled"}, ensure_ascii=False))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
