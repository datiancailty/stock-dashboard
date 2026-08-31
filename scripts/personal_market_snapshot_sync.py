#!/usr/bin/env python3
"""Refresh current private-watchlist quotes into the private snapshot ledger.

This is a manual local worker.  It reads the current owner-scoped Part 1 list,
uses the public Eastmoney quote endpoint, requires all symbols to resolve, and
then performs one authenticated trusted-writer RPC.  It never writes GitHub
live files, changes the base market document, invokes Codex, or trades.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PART4_SYNC_PATH = ROOT / "scripts" / "part4_official_announcement_sync.py"
MARKET_PATH = ROOT / "scripts" / "update_market.py"
BEIJING = ZoneInfo("Asia/Shanghai")


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module_load_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description="refresh private market snapshot")
    parser.add_argument("--dry-run", action="store_true", help="read/validate only; do not write private snapshot")
    args = parser.parse_args()
    part4 = load(PART4_SYNC_PATH, "personal_market_part4_adapter")
    market = load(MARKET_PATH, "personal_market_update_adapter")
    try:
        worker, config, access_token = part4.load_private_session()
        stocks = part4.private_watchlist(worker, config, access_token)
        quotes = market.fetch_prices([{"code": stock.code, "name": stock.name} for stock in stocks])
        if len(quotes) != len(stocks):
            raise part4.SyncError("market_quote_coverage_incomplete")
        captured_at = datetime.now(BEIJING).isoformat(timespec="seconds")
        payload = [{"code": stock.code, "price": quotes[stock.code]} for stock in stocks]
        summary = {
            "watchlistCount": len(stocks),
            "quoteCount": len(payload),
            "coverageComplete": len(payload) == len(stocks),
            "captureAt": captured_at,
            "captureTimeMeaning": "私有行情快照采集时间",
            "private_payload_not_emitted": True,
        }
        if args.dry_run:
            print(json.dumps({"status": "audit_ok", **summary}, ensure_ascii=False))
            return 0
        secret = part4.part4_writer_secret(worker, config)
        written = part4.private_rpc(
            worker,
            config,
            access_token,
            "personal_sync_market_snapshot",
            {"p_as_of": captured_at, "p_quotes": payload, "p_writer_secret": secret},
        )
        if not isinstance(written, dict):
            raise part4.SyncError("private_market_snapshot_response_invalid")
        print(json.dumps({"status": "ok", **summary, "published": True, "stored": written.get("stored")}, ensure_ascii=False))
        return 0
    except Exception as error:
        category = getattr(error, "category", "private_market_snapshot_failed")
        print(json.dumps({"status": "error", "category": str(category)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
