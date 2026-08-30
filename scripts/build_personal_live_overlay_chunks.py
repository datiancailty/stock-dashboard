#!/usr/bin/env python3
"""Split the private Stage 2 overlay into SQL Editor-sized transactions.

The input and output contain private personal data and must stay outside Git
with restrictive permissions. This utility prints metadata only; it never
prints SQL payloads, record IDs, titles, prices, or credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
from pathlib import Path

DEFAULT_MAX_BYTES = 180_000
EXPECTED_TABLES = {
    "personal_import_batches": 13,
    "personal_watchlist_items": 21,  # 20 inserts + 1 reset delete
    "personal_documents": 12,
    "personal_news_items": 453,  # 452 inserts + 1 reset delete
    "personal_trade_records": 385,  # 384 inserts + 1 reset delete
    "personal_strategy_feedback": 10,  # 9 inserts + 1 reset delete
    "personal_strategy_recommendations": 48,  # 47 inserts + 1 reset delete
    "checks": 7,
}


def parse_units(text: str) -> tuple[list[str], list[list[str]]]:
    lines = text.splitlines()
    do_indexes = [
        i for i, line in enumerate(lines) if line.strip().lower() == "do $personal_live_overlay$"
    ]
    if len(do_indexes) != 1:
        raise SystemExit("expected exactly one Stage 2 overlay DO block")
    do_index = do_indexes[0]
    end_marker = "$personal_live_overlay$;"
    try:
        end_index = next(
            i for i in range(do_index + 1, len(lines)) if lines[i].strip().lower() == end_marker
        )
    except StopIteration as exc:
        raise SystemExit("overlay DO block terminator not found") from exc

    body = lines[do_index + 1 : end_index]
    header: list[str] = []
    units: list[list[str]] = []
    i = 0
    while i < len(body):
        line = body[i]
        if line.strip().lower() == "end;":
            # This is the closing END of the original DO block, not a data unit.
            i += 1
        elif (
            line.startswith("declare")
            or line.startswith("  target_owner")
            or line.startswith("begin")
            or line.startswith("  select ")
            or line.startswith("  if target_owner is null")
        ):
            header.append(line)
            i += 1
        elif line.startswith("  insert into public."):
            unit = [line]
            i += 1
            while not unit[-1].rstrip().endswith(";"):
                if i >= len(body):
                    raise SystemExit("unterminated INSERT unit")
                unit.append(body[i])
                i += 1
            units.append(unit)
        elif line.startswith("  delete from public."):
            units.append([line])
            i += 1
        elif line.startswith("  if "):
            unit = [line]
            i += 1
            while True:
                if i >= len(body):
                    raise SystemExit("unterminated validation unit")
                unit.append(body[i])
                current = body[i].strip().lower()
                i += 1
                if current == "end if;":
                    break
            units.append(unit)
        elif not line.strip():
            i += 1
        else:
            raise SystemExit(f"unrecognized overlay line at body offset {i}")

    required_header = ["declare", "  target_owner uuid;", "begin"]
    if header[:3] != required_header or not any(
        line.startswith("  if target_owner is null") for line in header
    ):
        raise SystemExit("overlay header contract mismatch")
    return header, units


def unit_key(unit: list[str]) -> str:
    match = re.match(r"\s*(?:insert into|delete from) public\.([a-z_]+)", unit[0], re.I)
    if match:
        return match.group(1)
    if unit[0].lstrip().startswith("if "):
        return "checks"
    raise SystemExit("cannot classify overlay unit")


def make_chunk(index: int, total: int, header: list[str], units: list[list[str]]) -> str:
    tag = f"personal_live_chunk_{index:02d}"
    body: list[str] = [
        "-- PRIVATE DATA: do not commit, upload, or share this file.",
        "-- Manual Supabase SQL Editor execution only.",
        f"-- Stage 2 private overlay chunk {index:02d}/{total:02d}; execute in filename order.",
        "begin;",
        f"do ${tag}$",
        *header,
    ]
    for unit in units:
        body.extend(unit)
    body.extend(["end;", f"${tag}$;", "commit;", ""])
    return "\n".join(body)


def build_chunks(text: str, max_bytes: int) -> list[tuple[str, list[list[str]]]]:
    if max_bytes < 10_000:
        raise SystemExit("max-bytes is too small")
    header, units = parse_units(text)
    counts: dict[str, int] = {}
    for unit in units:
        key = unit_key(unit)
        counts[key] = counts.get(key, 0) + 1
    if counts != EXPECTED_TABLES:
        raise SystemExit(f"unexpected overlay unit counts: {counts}")

    groups: list[list[list[str]]] = []
    current: list[list[str]] = []
    for unit in units:
        candidate = current + [unit]
        # Use a high estimate for the wrapper. The actual size is checked
        # after grouping, so this only controls packing efficiency.
        estimate = len(make_chunk(0, 99, header, candidate).encode("utf-8"))
        if current and estimate > max_bytes:
            groups.append(current)
            current = [unit]
        else:
            current = candidate
    if current:
        groups.append(current)

    result: list[tuple[str, list[list[str]]]] = []
    total = len(groups)
    for index, group in enumerate(groups):
        sql = make_chunk(index, total, header, group)
        size = len(sql.encode("utf-8"))
        if size > max_bytes:
            raise SystemExit(f"unit too large for SQL Editor chunk: {size} bytes")
        result.append((sql, group))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args()

    source = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    if not source.is_file():
        raise SystemExit(f"private overlay input not found: {source}")
    if stat.S_IMODE(source.stat().st_mode) != 0o600:
        raise SystemExit("private overlay input must be mode 0600")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    if stat.S_IMODE(output_dir.stat().st_mode) != 0o700:
        raise SystemExit("private chunk directory must be mode 0700")
    if any(output_dir.iterdir()):
        raise SystemExit(f"refusing to write into non-empty directory: {output_dir}")

    text = source.read_text(encoding="utf-8")
    chunks = build_chunks(text, args.max_bytes)
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    total_units = 0
    for index, (sql, units) in enumerate(chunks):
        name = f"personal-live-overlay-stage2-{index:02d}.sql"
        path = output_dir / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(sql)
        except Exception:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
        os.chmod(path, 0o600)
        total_units += len(units)
        print(
            f"chunk={index:02d} bytes={path.stat().st_size} units={len(units)} "
            f"first={unit_key(units[0])} last={unit_key(units[-1])} mode=600"
        )
    print("PERSONAL_LIVE_OVERLAY_CHUNKS_BUILT_OK")
    print(f"source_sha256={source_digest}")
    print(f"chunks={len(chunks)}")
    print(f"units={total_units}")
    print(f"max_bytes={args.max_bytes}")
    print(f"output_dir_mode={stat.S_IMODE(output_dir.stat().st_mode):o}")
    print("private_values_printed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
