#!/usr/bin/env python3
"""Build a private forward patch for implemented dividend-calendar events.

The output contains a complete private market document and must remain outside
Git with restrictive permissions. This utility reads the live Git object,
adds only explicitly supplied authoritative event dates, and prints metadata
only; it never prints the market payload, record IDs, prices, or credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

BJ = ZoneInfo("Asia/Shanghai")
EVENT_TYPES = ("股权登记日", "除权除息日", "派息日")


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


def git_bytes(repo: Path, ref: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def parse_date(value: str, label: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SystemExit(f"{label} must be YYYY-MM-DD")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit(f"{label} is not a real date") from exc
    return value


def build_events(code: str, name: str, period: str, per_share: float, registration: str, ex_date: str, pay_date: str) -> list[dict[str, object]]:
    if not re.fullmatch(r"\d{6}", code):
        raise SystemExit("code must be six digits")
    if not name.strip() or len(name.strip()) > 80:
        raise SystemExit("name must contain 1-80 characters")
    if not period.strip() or len(period.strip()) > 40:
        raise SystemExit("period must contain 1-40 characters")
    if not (per_share > 0):
        raise SystemExit("per-share dividend must be positive")
    dates = {
        "股权登记日": parse_date(registration, "registration-date"),
        "除权除息日": parse_date(ex_date, "ex-date"),
        "派息日": parse_date(pay_date, "pay-date"),
    }
    description = f"{period.strip()} · 每股税前 {per_share:g}元"
    return [
        {
            "date": dates[event_type],
            "code": code,
            "name": name.strip(),
            "type": event_type,
            "amount": None,
            "description": description,
        }
        for event_type in EVENT_TYPES
    ]


def make_patch(market: dict[str, object], events: list[dict[str, object]]) -> tuple[dict[str, object], int]:
    stocks = market.get("stocks")
    old_events = market.get("events")
    if not isinstance(stocks, list) or len(stocks) != 20:
        raise SystemExit("live market must contain exactly 20 stocks")
    if not isinstance(old_events, list):
        raise SystemExit("live market events must be an array")

    merged: dict[str, dict[str, object]] = {}
    for row in old_events:
        if not isinstance(row, dict):
            raise SystemExit("live market contains a non-object event")
        key = f"{row.get('date')}|{row.get('code')}|{row.get('type')}"
        if key in merged:
            raise SystemExit("live market contains duplicate event keys")
        merged[key] = row

    added = 0
    for row in events:
        key = f"{row['date']}|{row['code']}|{row['type']}"
        if key not in merged:
            added += 1
        merged[key] = row

    patched = dict(market)
    patched["events"] = sorted(merged.values(), key=lambda row: (str(row.get("date", "")), str(row.get("code", "")), str(row.get("type", ""))))
    patched["dividendEventPatch"] = {
        "patchedAt": datetime.now(BJ).isoformat(timespec="seconds"),
        "source": "东方财富妙想结构化查数与公告检索",
        "basis": "已实施现金分红事件；只补充 Part 4 日历，不改变上一完整年度正式股息率分子",
    }
    return patched, added


def build_sql(patched: dict[str, object], live_commit: str, username: str, expected_event_count: int, required_keys: list[str]) -> bytes:
    raw_pretty = (json.dumps(patched, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    source_sha = hashlib.sha256(raw_pretty).hexdigest()
    source_bytes = len(raw_pretty)
    payload_expr = json_expr(patched, "market_event_patch")
    required_checks = []
    for key in required_keys:
        date_value, code, event_type = key.split("|", 2)
        required_checks.append(
            "  if not exists (select 1 from jsonb_array_elements((select payload->'events' from public.personal_documents where owner_user_id = target_owner and document_key = 'market')) event where event->>'date' = "
            + sql_text(date_value)
            + " and event->>'code' = "
            + sql_text(code)
            + " and event->>'type' = "
            + sql_text(event_type)
            + ") then raise exception 'Required dividend event missing'; end if;"
        )
    lines = [
        "-- PRIVATE DATA: do not commit, upload, or share this file.",
        "-- Manual Supabase SQL Editor execution only.",
        "-- Forward-only owner-scoped replacement of the private market document.",
        "begin;",
        "do $personal_market_event_patch$",
        "declare",
        "  target_owner uuid;",
        "begin",
        f"  select user_id into target_owner from public.app_usernames where username_norm = {sql_text(username)} and status = 'active';",
        "  if target_owner is null then raise exception 'Target dashboard user is not active'; end if;",
        "  insert into public.personal_import_batches (owner_user_id, source_path, source_commit, source_sha256, source_bytes, source_record_count, stable_id_set_sha256)",
        f"  values (target_owner, 'live/market.json', {sql_text(live_commit)}, {sql_text(source_sha)}, {source_bytes}, null, null)",
        "  on conflict (owner_user_id, source_path, source_sha256) do update set source_bytes = excluded.source_bytes, source_record_count = excluded.source_record_count;",
        "  insert into public.personal_documents (owner_user_id, document_key, payload, source_path, source_sha256)",
        f"  values (target_owner, 'market', {payload_expr}, 'live/market.json', {sql_text(source_sha)})",
        "  on conflict (owner_user_id, document_key) do update set payload = excluded.payload, source_path = excluded.source_path, source_sha256 = excluded.source_sha256, updated_at = now();",
        f"  if (select jsonb_array_length(payload->'stocks') from public.personal_documents where owner_user_id = target_owner and document_key = 'market') <> 20 then raise exception 'Post-patch market stock count mismatch'; end if;",
        f"  if (select jsonb_array_length(payload->'events') from public.personal_documents where owner_user_id = target_owner and document_key = 'market') <> {expected_event_count} then raise exception 'Post-patch calendar event count mismatch'; end if;",
        *required_checks,
        "end;",
        "$personal_market_event_patch$;",
        "commit;",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--live-ref", default="origin/live")
    parser.add_argument("--output", required=True)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--code", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--per-share", required=True, type=float)
    parser.add_argument("--registration-date", required=True)
    parser.add_argument("--ex-date", required=True)
    parser.add_argument("--pay-date", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing private patch: {output}")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output.parent, 0o700)
    if stat.S_IMODE(output.parent.stat().st_mode) != 0o700:
        raise SystemExit("private output directory must be mode 0700")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])?", args.username):
        raise SystemExit("invalid normalized username")

    live_commit = subprocess.run(["git", "rev-parse", args.live_ref], cwd=repo, text=True, capture_output=True, check=True).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", live_commit):
        raise SystemExit("live ref must resolve to a full commit ID")
    market = json.loads(git_bytes(repo, args.live_ref, "market.json").decode("utf-8"))
    events = build_events(args.code, args.name, args.period, args.per_share, args.registration_date, args.ex_date, args.pay_date)
    patched, added = make_patch(market, events)
    old_event_count = len(cast(list[object], market["events"]))
    new_event_count = len(cast(list[object], patched["events"]))
    required_keys = [f"{row['date']}|{row['code']}|{row['type']}" for row in events]
    sql = build_sql(patched, live_commit, args.username, new_event_count, required_keys)

    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(sql)
    except Exception:
        try:
            output.unlink()
        except FileNotFoundError:
            pass
        raise
    os.chmod(output, 0o600)
    print("PERSONAL_MARKET_EVENT_PATCH_BUILT_OK")
    print(f"live_commit={live_commit}")
    print(f"old_event_count={old_event_count}")
    print(f"new_event_count={new_event_count}")
    print(f"added_event_count={added}")
    print("required_event_count=3")
    print(f"output_bytes={output.stat().st_size}")
    print(f"output_sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")
    print("output_mode=600")
    print("private_values_printed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
