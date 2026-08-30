#!/usr/bin/env python3
"""Build a mode-0600, idempotent SQL import for the legacy personal dashboard.

The generated SQL contains private records. It must stay outside Git and be pasted
manually by the owner into the Supabase SQL Editor only after the schema migration.
The script prints metadata only; it never prints payloads or stable IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path

COLLECTIONS = {
    "data/stocks.json": (None, "personal_watchlist_items", "code"),
    "data/news-memory.json": ("items", "personal_news_items", "id"),
    "data/trade-records.json": ("records", "personal_trade_records", "id"),
    "data/strategy-feedback.json": ("records", "personal_strategy_feedback", "id"),
    "data/strategy-recommendations.json": ("records", "personal_strategy_recommendations", "id"),
}
DOCUMENT_KEYS = {
    "data/part2-config.json": "part2_config",
    "data/market.json": "market",
    "data/news-memory.json": "news_meta",
    "data/trade-records.json": "trades_meta",
    "data/strategy-feedback.json": "feedback_meta",
    "data/strategy-recommendations.json": "recommendations_meta",
    "data/strategy-profile.json": "strategy_profile",
    "data/strategy-analysis.json": "strategy_analysis",
    "data/strategy-analysis-checkpoint.json": "strategy_analysis_checkpoint",
    "data/strategy-backtest-week-day-down-month-mid.json": "strategy_backtest_week_day_down_month_mid",
    "data/strategy-backtest-new-focused-stocks.json": "strategy_backtest_new_focused_stocks",
    "data/strategy-api-health.json": "strategy_api_health",
}


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sql_text(value: str | None) -> str:
    if value is None:
        return "null"
    return "'" + value.replace("'", "''") + "'"


def json_expr(value: object, label: str) -> str:
    raw = compact(value)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    tag = f"$personal_{label}_{digest}$"
    if tag in raw:
        raise ValueError("unexpected dollar-quote collision")
    return f"{tag}{raw}{tag}::jsonb"


def valid_code(value: object) -> str | None:
    text = str(value or "")
    return text if re.fullmatch(r"\d{6}", text) else None


def valid_iso(value: object, date_only: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if date_only:
        return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?", text) else None


def stable_digest(rows: list[dict], id_key: str) -> str:
    ids = [str(row.get(id_key, "")) for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"missing or duplicate stable ID for {id_key}")
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    manifest_path = Path(args.manifest).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing private import: {output}")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output.parent, 0o700)
    if stat.S_IMODE(output.parent.stat().st_mode) != 0o700:
        raise SystemExit("private output directory must be mode 0700")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_commit = str(manifest["source_commit"])
    entries = {item["path"]: item for item in manifest["files"]}
    expected = set(COLLECTIONS) | set(DOCUMENT_KEYS)
    if set(entries) != expected:
        raise SystemExit("manifest source set does not match the approved 13-file allow-list")

    objects: dict[str, object] = {}
    collection_rows: dict[str, list[dict]] = {}
    stable_digests: dict[str, str | None] = {}
    for rel, meta in entries.items():
        path = repo / rel
        raw = path.read_bytes()
        if len(raw) != int(meta["bytes"]) or hashlib.sha256(raw).hexdigest() != meta["sha256"]:
            raise SystemExit(f"source integrity mismatch: {rel}")
        obj = json.loads(raw.decode("utf-8"))
        if rel != "data/stocks.json" and not isinstance(obj, dict):
            raise SystemExit(f"invalid document shape: {rel}")
        objects[rel] = obj
        if rel in COLLECTIONS:
            array_key, _, id_key = COLLECTIONS[rel]
            rows = obj if array_key is None else obj.get(array_key, [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise SystemExit(f"invalid collection shape: {rel}")
            collection_rows[rel] = rows
            stable_digests[rel] = stable_digest(rows, id_key)
            manifest_count = meta.get("primary_count")
            if manifest_count is not None and len(rows) != int(manifest_count):
                raise SystemExit(f"record count mismatch: {rel}")
        else:
            stable_digests[rel] = None

    lines = [
        "-- PRIVATE DATA: do not commit, upload, or share this file.",
        "-- Manual Supabase SQL Editor execution only.",
        "begin;",
        "do $personal_import$",
        "declare",
        "  target_owner uuid;",
        "begin",
        f"  select user_id into target_owner from public.app_usernames where username_norm = {sql_text(args.username)} and status = 'active';",
        "  if target_owner is null then raise exception 'Target dashboard user is not active'; end if;",
    ]

    for rel, meta in entries.items():
        rows = collection_rows.get(rel)
        count = len(rows) if rows is not None else meta.get("primary_count")
        digest = stable_digests[rel]
        lines += [
            "  insert into public.personal_import_batches (owner_user_id, source_path, source_commit, source_sha256, source_bytes, source_record_count, stable_id_set_sha256)",
            f"  values (target_owner, {sql_text(rel)}, {sql_text(source_commit)}, {sql_text(meta['sha256'])}, {int(meta['bytes'])}, {('null' if count is None else int(count))}, {sql_text(digest)})",
            "  on conflict (owner_user_id, source_path, source_sha256) do update set",
            "    source_bytes = excluded.source_bytes, source_record_count = excluded.source_record_count, stable_id_set_sha256 = excluded.stable_id_set_sha256;",
        ]

    for rel, key in DOCUMENT_KEYS.items():
        payload = objects[rel]
        array_key = COLLECTIONS.get(rel, (None, None, None))[0]
        if array_key is not None:
            if not isinstance(payload, dict):
                raise SystemExit(f"invalid metadata document shape: {rel}")
            payload = {k: v for k, v in payload.items() if k != array_key}
        lines += [
            "  insert into public.personal_documents (owner_user_id, document_key, payload, source_path, source_sha256)",
            f"  values (target_owner, {sql_text(key)}, {json_expr(payload, key)}, {sql_text(rel)}, {sql_text(entries[rel]['sha256'])})",
            "  on conflict (owner_user_id, document_key) do update set payload = excluded.payload, source_path = excluded.source_path, source_sha256 = excluded.source_sha256, updated_at = now();",
        ]

    for row in collection_rows["data/stocks.json"]:
        source_id = str(row["code"])
        lines += [
            "  insert into public.personal_watchlist_items (owner_user_id, source_id, stock_code, display_name, payload, source_sha256)",
            f"  values (target_owner, {sql_text(source_id)}, {sql_text(valid_code(row.get('code')))}, {sql_text(str(row.get('name') or source_id)[:80])}, {json_expr(row, 'watch')}, {sql_text(entries['data/stocks.json']['sha256'])})",
            "  on conflict (owner_user_id, source_id) do update set stock_code = excluded.stock_code, display_name = excluded.display_name, payload = excluded.payload, source_sha256 = excluded.source_sha256, updated_at = now();",
        ]

    mappings = [
        ("data/news-memory.json", "personal_news_items", lambda r: [sql_text(valid_code(r.get("code"))), sql_text(valid_iso(r.get("publishedAt"))), json_expr(r, "news")]),
        ("data/trade-records.json", "personal_trade_records", lambda r: [sql_text(valid_iso(r.get("date"), True)), sql_text(valid_code(r.get("code"))), sql_text(valid_iso(r.get("createdAt"))), json_expr(r, "trade")]),
        ("data/strategy-feedback.json", "personal_strategy_feedback", lambda r: [sql_text(str(r.get("recommendationId") or "") or None), sql_text(valid_code(r.get("code"))), sql_text(valid_iso(r.get("createdAt"))), json_expr(r, "feedback")]),
        ("data/strategy-recommendations.json", "personal_strategy_recommendations", lambda r: [sql_text(str(r.get("recommendationId") or "") or None), sql_text(valid_code(r.get("code"))), sql_text(valid_iso(r.get("recommendedAt"))), json_expr(r, "recommendation")]),
    ]
    columns = {
        "personal_news_items": ("stock_code, published_at, payload", "stock_code = excluded.stock_code, published_at = excluded.published_at"),
        "personal_trade_records": ("trade_date, stock_code, created_at_source, payload", "trade_date = excluded.trade_date, stock_code = excluded.stock_code, created_at_source = excluded.created_at_source"),
        "personal_strategy_feedback": ("recommendation_id, stock_code, created_at_source, payload", "recommendation_id = excluded.recommendation_id, stock_code = excluded.stock_code, created_at_source = excluded.created_at_source"),
        "personal_strategy_recommendations": ("recommendation_id, stock_code, recommended_at, payload", "recommendation_id = excluded.recommendation_id, stock_code = excluded.stock_code, recommended_at = excluded.recommended_at"),
    }
    for rel, table, values_fn in mappings:
        id_key = COLLECTIONS[rel][2]
        source_hash = entries[rel]["sha256"]
        cols, updates = columns[table]
        for row in collection_rows[rel]:
            vals = [sql_text(str(row[id_key]))] + values_fn(row) + [sql_text(source_hash)]
            lines += [
                f"  insert into public.{table} (owner_user_id, source_id, {cols}, source_sha256)",
                f"  values (target_owner, {', '.join(vals)})",
                f"  on conflict (owner_user_id, source_id) do update set {updates}, payload = excluded.payload, source_sha256 = excluded.source_sha256, updated_at = now();",
            ]

    expected_counts = {
        "personal_import_batches": 13,
        "personal_watchlist_items": len(collection_rows["data/stocks.json"]),
        "personal_news_items": len(collection_rows["data/news-memory.json"]),
        "personal_trade_records": len(collection_rows["data/trade-records.json"]),
        "personal_strategy_feedback": len(collection_rows["data/strategy-feedback.json"]),
        "personal_strategy_recommendations": len(collection_rows["data/strategy-recommendations.json"]),
        "personal_documents": len(DOCUMENT_KEYS),
    }
    for table, count in expected_counts.items():
        lines += [
            f"  if (select count(*) from public.{table} where owner_user_id = target_owner) <> {count} then",
            f"    raise exception 'Post-import count mismatch: {table}';",
            "  end if;",
        ]
    lines += ["end;", "$personal_import$;", "commit;", ""]

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(output, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines))
    except Exception:
        try:
            output.unlink()
        except FileNotFoundError:
            pass
        raise
    os.chmod(output, 0o600)
    print("PERSONAL_LEGACY_IMPORT_BUILT_OK")
    print(f"source_files={len(entries)}")
    print(f"output_bytes={output.stat().st_size}")
    print(f"output_sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")
    print("output_mode=600")
    print("private_values_printed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
