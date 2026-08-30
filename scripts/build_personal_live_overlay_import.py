#!/usr/bin/env python3
"""Build a private replacement import from main data plus the live archive.

The SQL output contains personal dashboard records and must remain mode 0600
outside Git. The generator reads Git objects and prints metadata only; it never
prints payloads, record IDs, titles, prices, or credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

COLLECTIONS = {
    "data/stocks.json": (None, "personal_watchlist_items", "code"),
    "live/news-memory.json": ("items", "personal_news_items", "id"),
    "data/trade-records.json": ("records", "personal_trade_records", "id"),
    "data/strategy-feedback.json": ("records", "personal_strategy_feedback", "id"),
    "data/strategy-recommendations.json": ("records", "personal_strategy_recommendations", "id"),
}

DOCUMENT_SOURCES = {
    "part2_config": "data/part2-config.json",
    "market": "live/market.json",
    "news_meta": "live/news-memory.json",
    "trades_meta": "data/trade-records.json",
    "feedback_meta": "data/strategy-feedback.json",
    "recommendations_meta": "data/strategy-recommendations.json",
    "strategy_profile": "data/strategy-profile.json",
    "strategy_analysis": "live/strategy-analysis.json",
    "strategy_analysis_checkpoint": "data/strategy-analysis-checkpoint.json",
    "strategy_backtest_week_day_down_month_mid": "data/strategy-backtest-week-day-down-month-mid.json",
    "strategy_backtest_new_focused_stocks": "data/strategy-backtest-new-focused-stocks.json",
    "strategy_api_health": "live/strategy-api-health.json",
}

TABLES_TO_REPLACE = [
    "personal_watchlist_items",
    "personal_news_items",
    "personal_trade_records",
    "personal_strategy_feedback",
    "personal_strategy_recommendations",
]


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sql_text(value: str | None) -> str:
    if value is None:
        return "null"
    return "'" + value.replace("'", "''") + "'"


def json_expr(value: object, label: str) -> str:
    raw = compact(value)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    tag = f"$personal_{label}_{digest}$"
    if tag in raw:
        raise ValueError("unexpected dollar-quote collision")
    return f"{tag}{raw}{tag}::jsonb"


def valid_code(value: object) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{6}", text) else None


def valid_iso(value: object, date_only: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if date_only:
        return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+\-/ Z]+)?", text) else None


def stable_digest(rows: list[dict[str, Any]], id_key: str) -> str:
    ids = [str(row.get(id_key, "")) for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"missing or duplicate stable ID for {id_key}")
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def git_bytes(repo: Path, ref: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def load_sources(repo: Path, main_ref: str, live_ref: str) -> tuple[dict[str, bytes], str, str]:
    main_commit = subprocess.run(
        ["git", "rev-parse", main_ref], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    live_commit = subprocess.run(
        ["git", "rev-parse", live_ref], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", main_commit) or not re.fullmatch(r"[0-9a-f]{40}", live_commit):
        raise SystemExit("source refs must resolve to full commit IDs")

    raw_sources: dict[str, bytes] = {}
    for source_path in sorted(set(COLLECTIONS) | set(DOCUMENT_SOURCES.values())):
        ref = live_ref if source_path.startswith("live/") else main_ref
        git_path = source_path.split("/", 1)[1] if source_path.startswith("live/") else source_path
        raw = git_bytes(repo, ref, git_path)
        raw_sources[source_path] = raw
    return raw_sources, main_commit, live_commit


def parse_sources(raw_sources: dict[str, bytes]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, str | None], dict[str, dict[str, Any]]]:
    objects: dict[str, Any] = {}
    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    digests: dict[str, str | None] = {}
    metas: dict[str, dict[str, Any]] = {}

    for source_path, raw in raw_sources.items():
        obj = json.loads(raw.decode("utf-8"))
        objects[source_path] = obj
        metas[source_path] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
        if source_path in COLLECTIONS:
            array_key, _, id_key = COLLECTIONS[source_path]
            rows = obj if array_key is None else obj.get(array_key, [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise SystemExit(f"invalid collection shape: {source_path}")
            rows_by_source[source_path] = rows
            digests[source_path] = stable_digest(rows, id_key)
        else:
            digests[source_path] = None

    expected_counts = {
        "data/stocks.json": 20,
        "live/news-memory.json": 452,
        "data/trade-records.json": 384,
        "data/strategy-feedback.json": 9,
        "data/strategy-recommendations.json": 47,
    }
    for source_path, expected in expected_counts.items():
        actual = len(rows_by_source[source_path])
        if actual != expected:
            raise SystemExit(f"unexpected source count: {source_path}={actual}, expected={expected}")

    market=objects.get('live/market.json')
    if not isinstance(market,dict) or not isinstance(market.get('stocks'),list) or not isinstance(market.get('events'),list):
        raise SystemExit('invalid live market document shape')
    if len(market['stocks']) != 20:
        raise SystemExit(f"unexpected live market stock count: {len(market['stocks'])}")
    event_keys=[f"{row.get('date')}|{row.get('code')}|{row.get('type')}" for row in market['events'] if isinstance(row,dict)]
    if len(event_keys) != len(market['events']) or len(event_keys) != len(set(event_keys)):
        raise SystemExit('live market events must have unique date/code/type keys')

    return objects, rows_by_source, digests, metas


def build_sql(
    objects: dict[str, Any],
    rows_by_source: dict[str, list[dict[str, Any]]],
    digests: dict[str, str | None],
    metas: dict[str, dict[str, Any]],
    main_commit: str,
    live_commit: str,
    username: str,
) -> str:
    source_commit = {
        source_path: (live_commit if source_path.startswith("live/") else main_commit)
        for source_path in metas
    }
    market_payload=objects['live/market.json']
    market_stock_count=len(market_payload['stocks'])
    market_event_count=len(market_payload['events'])
    lines = [
        "-- PRIVATE DATA: do not commit, upload, or share this file.",
        "-- Manual Supabase SQL Editor execution only.",
        "-- This is an owner-scoped replacement overlay for the existing personal namespace.",
        "begin;",
        "do $personal_live_overlay$",
        "declare",
        "  target_owner uuid;",
        "begin",
        f"  select user_id into target_owner from public.app_usernames where username_norm = {sql_text(username)} and status = 'active';",
        "  if target_owner is null then raise exception 'Target dashboard user is not active'; end if;",
    ]

    for source_path in sorted(metas):
        meta = metas[source_path]
        rows = rows_by_source.get(source_path)
        count = len(rows) if rows is not None else None
        lines += [
            "  insert into public.personal_import_batches (owner_user_id, source_path, source_commit, source_sha256, source_bytes, source_record_count, stable_id_set_sha256)",
            f"  values (target_owner, {sql_text(source_path)}, {sql_text(source_commit[source_path])}, {sql_text(meta['sha256'])}, {meta['bytes']}, {('null' if count is None else count)}, {sql_text(digests[source_path])})",
            "  on conflict (owner_user_id, source_path, source_sha256) do update set",
            "    source_bytes = excluded.source_bytes, source_record_count = excluded.source_record_count, stable_id_set_sha256 = excluded.stable_id_set_sha256;",
        ]

    for table in TABLES_TO_REPLACE:
        lines.append(f"  delete from public.{table} where owner_user_id = target_owner;")

    for document_key, source_path in DOCUMENT_SOURCES.items():
        payload = objects[source_path]
        array_key = COLLECTIONS.get(source_path, (None, None, None))[0]
        if array_key is not None:
            if not isinstance(payload, dict):
                raise SystemExit(f"invalid metadata document shape: {source_path}")
            payload = {key: value for key, value in payload.items() if key != array_key}
        lines += [
            "  insert into public.personal_documents (owner_user_id, document_key, payload, source_path, source_sha256)",
            f"  values (target_owner, {sql_text(document_key)}, {json_expr(payload, document_key)}, {sql_text(source_path)}, {sql_text(metas[source_path]['sha256'])})",
            "  on conflict (owner_user_id, document_key) do update set payload = excluded.payload, source_path = excluded.source_path, source_sha256 = excluded.source_sha256, updated_at = now();",
        ]

    for row in rows_by_source["data/stocks.json"]:
        source_id = str(row["code"])
        code = valid_code(row.get("code"))
        if code is None:
            raise SystemExit("invalid watchlist code")
        name = str(row.get("name") or source_id).strip()[:80]
        lines += [
            "  insert into public.personal_watchlist_items (owner_user_id, source_id, stock_code, display_name, payload, source_sha256)",
            f"  values (target_owner, {sql_text(source_id)}, {sql_text(code)}, {sql_text(name)}, {json_expr(row, 'watch')}, {sql_text(metas['data/stocks.json']['sha256'])});",
        ]

    collection_specs = [
        ("live/news-memory.json", "personal_news_items", "id", "stock_code, published_at, payload"),
        ("data/trade-records.json", "personal_trade_records", "id", "trade_date, stock_code, created_at_source, payload"),
        ("data/strategy-feedback.json", "personal_strategy_feedback", "id", "recommendation_id, stock_code, created_at_source, payload"),
        ("data/strategy-recommendations.json", "personal_strategy_recommendations", "id", "recommendation_id, stock_code, recommended_at, payload"),
    ]
    value_specs = {
        "personal_news_items": lambda row: [sql_text(valid_code(row.get("code"))), sql_text(valid_iso(row.get("publishedAt"))), json_expr(row, "news")],
        "personal_trade_records": lambda row: [sql_text(valid_iso(row.get("date"), True)), sql_text(valid_code(row.get("code"))), sql_text(valid_iso(row.get("createdAt"))), json_expr(row, "trade")],
        "personal_strategy_feedback": lambda row: [sql_text(str(row.get("recommendationId") or "") or None), sql_text(valid_code(row.get("code"))), sql_text(valid_iso(row.get("createdAt"))), json_expr(row, "feedback")],
        "personal_strategy_recommendations": lambda row: [sql_text(str(row.get("recommendationId") or "") or None), sql_text(valid_code(row.get("code"))), sql_text(valid_iso(row.get("recommendedAt"))), json_expr(row, "recommendation")],
    }
    for source_path, table, id_key, columns in collection_specs:
        for row in rows_by_source[source_path]:
            source_id = str(row.get(id_key) or "")
            if not source_id:
                raise SystemExit(f"missing stable ID: {source_path}")
            values = [sql_text(source_id)] + value_specs[table](row) + [sql_text(metas[source_path]["sha256"])]
            lines.append(
                f"  insert into public.{table} (owner_user_id, source_id, {columns}, source_sha256) values (target_owner, {', '.join(values)});"
            )

    checks = [
        ("personal_watchlist_items", 20),
        ("personal_news_items", 452),
        ("personal_trade_records", 384),
        ("personal_strategy_feedback", 9),
        ("personal_strategy_recommendations", 47),
    ]
    for table, count in checks:
        lines += [
            f"  if (select count(*) from public.{table} where owner_user_id = target_owner) <> {count} then",
            f"    raise exception 'Post-overlay count mismatch: {table}';",
            "  end if;",
        ]
    lines += [
        f"  if (select jsonb_array_length(payload->'stocks') from public.personal_documents where owner_user_id = target_owner and document_key = 'market') <> {market_stock_count} then",
        "    raise exception 'Post-overlay market stock count mismatch';",
        "  end if;",
        f"  if (select jsonb_array_length(payload->'events') from public.personal_documents where owner_user_id = target_owner and document_key = 'market') <> {market_event_count} then",
        "    raise exception 'Post-overlay calendar event count mismatch';",
        "  end if;",
        "end;",
        "$personal_live_overlay$;",
        "commit;",
        "",
    ]
    sql = "\n".join(lines)
    return sql


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--main-ref", default="HEAD")
    parser.add_argument("--live-ref", default="origin/live")
    parser.add_argument("--output", required=True)
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing private import: {output}")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output.parent, 0o700)
    if stat.S_IMODE(output.parent.stat().st_mode) != 0o700:
        raise SystemExit("private output directory must be mode 0700")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])?", args.username):
        raise SystemExit("invalid normalized username")

    raw_sources, main_commit, live_commit = load_sources(repo, args.main_ref, args.live_ref)
    objects, rows_by_source, digests, metas = parse_sources(raw_sources)
    sql = build_sql(objects, rows_by_source, digests, metas, main_commit, live_commit, args.username)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(output, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(sql)
    except Exception:
        try:
            output.unlink()
        except FileNotFoundError:
            pass
        raise
    os.chmod(output, 0o600)
    print("PERSONAL_LIVE_OVERLAY_IMPORT_BUILT_OK")
    print(f"main_commit={main_commit}")
    print(f"live_commit={live_commit}")
    print("source_files=13")
    print(f"market_stocks={len(objects['live/market.json']['stocks'])}")
    print(f"calendar_events={len(objects['live/market.json']['events'])}")
    print("news_items=452")
    print("trade_records=384")
    print("feedback_records=9")
    print("recommendation_records=47")
    print(f"output_bytes={output.stat().st_size}")
    print(f"output_sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")
    print("output_mode=600")
    print("private_values_printed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
