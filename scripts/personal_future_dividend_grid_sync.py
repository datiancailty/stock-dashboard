#!/usr/bin/env python3
"""Refresh Part 2/3 forward (announced-but-unimplemented) dividend grid data.

The official Part 4 ledger remains the factual announcement calendar.  This
manual local worker separately reads structured dividend fields for every
current private Part 1 symbol, requires complete batch coverage, and writes
only future-grid values through a trusted-writer RPC.  Formal dividend fields,
market document, trading and GitHub live files are not modified.
"""
from __future__ import annotations

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
    part4 = load(PART4_SYNC_PATH, "personal_dividend_part4_adapter")
    market = load(MARKET_PATH, "personal_dividend_market_adapter")
    try:
        worker, config, access_token = part4.load_private_session()
        watchlist = part4.private_watchlist(worker, config, access_token)
        base = part4.private_rpc(worker, config, access_token, "personal_get_part4", {})
        if not isinstance(base, dict) or not isinstance(base.get("stocks"), list):
            raise part4.SyncError("private_market_shape_invalid")
        year = datetime.now(BEIJING).year - 1
        payloads = []
        batches = market.dividend_query_batches([{"code": s.code, "name": s.name} for s in watchlist])
        for batch in batches:
            payloads.append(market.api_query("、".join(item["name"] for item in batch), year))
        dtos = []
        for payload in payloads:
            dtos.extend(market.result_dtos(payload))
        market.assert_dividend_dto_coverage(dtos, [{"code": s.code, "name": s.name} for s in watchlist])
        previous = dict(base)
        previous["stocks"] = [{**item, "futureDividend": 0, "futureDividendStatus": None} for item in base["stocks"] if isinstance(item, dict)]
        prices = {str(item.get("code")): market.number(item.get("price")) for item in previous["stocks"]}
        parsed, _ = market.parse(
            {"data": {"data": {"searchDataResultDTO": {"dataTableDTOList": dtos}}}},
            [{"code": s.code, "name": s.name} for s in watchlist], year, previous, prices, {}, {}
        )
        by_code = {str(item.get("code")): item for item in parsed}
        records = [{"code": s.code, "futureDividend": float(by_code[s.code].get("futureDividend") or 0), "status": by_code[s.code].get("futureDividendStatus")} for s in watchlist]
        as_of = datetime.now(BEIJING).isoformat(timespec="seconds")
        secret = part4.part4_writer_secret(worker, config)
        written = part4.private_rpc(worker, config, access_token, "personal_sync_future_dividend_grid", {"p_as_of": as_of, "p_records": records, "p_writer_secret": secret})
        if not isinstance(written, dict):
            raise part4.SyncError("private_future_dividend_response_invalid")
        print(json.dumps({"status":"ok","watchlistCount":len(watchlist),"coveredCount":len(records),"coverageComplete":len(records)==len(watchlist),"published":True,"stored":written.get("stored"),"private_payload_not_emitted":True},ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"status":"error","category":str(getattr(error,"category","private_future_dividend_failed"))},ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
