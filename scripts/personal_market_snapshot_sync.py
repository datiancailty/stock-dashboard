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


def sync(*, adapter=None, publish=False, now=None):
    import dashboard_data_sources as sources
    part4 = adapter or load(PART4_SYNC_PATH, "personal_market_part4_adapter")
    worker, config, token = part4.load_private_session()
    stocks = part4.private_watchlist(worker, config, token)
    now = now or datetime.now(BEIJING)
    expected = sources.quote_trading_day(now)
    result = sources.fetch_quotes([s.code for s in stocks], now, expected_day=expected)
    payload = result['quotes']
    captured_at = datetime.now(BEIJING).isoformat(timespec='seconds')
    summary = {'status': 'audit_ok', 'published': False, 'readbackVerified': False,
               'watchlistCount': len(stocks), 'quoteCount': len(payload), 'coverageComplete': True,
               'captureAt': captured_at, 'quoteAsOf': result['quoteAsOf'], 'tradingDate': expected,
               'source': result['source'], 'fallbackReasons': result['fallbackReasons'], 'mxInvoked': False,
               'quoteTimeMeaning': '供应商数据时间；HiThink为快照就绪时间，不是逐股票成交时间',
               'captureTimeMeaning': '私有行情快照采集时间，不是交易所逐笔时间',
               'private_payload_not_emitted': True}
    if not publish:
        return summary
    secret = part4.part4_writer_secret(worker, config)
    written = part4.private_rpc(worker, config, token, 'personal_sync_market_snapshot',
        {'p_as_of': captured_at, 'p_quotes': payload, 'p_writer_secret': secret})
    if not isinstance(written, dict) or type(written.get('stored')) is not int or written['stored'] != len(payload):
        raise sources.DataSourceError('data_publish_unconfirmed')
    back = part4.private_rpc(worker, config, token, 'personal_get_part4', {})
    rows = back.get('stocks') if isinstance(back, dict) else None
    if not isinstance(rows, list) or len(rows) != len(payload) or any(not isinstance(s, dict) for s in rows):
        raise sources.DataSourceError('data_publish_readback_mismatch')
    actual = {s.get('code'): s.get('price') for s in rows}
    if actual != {s['code']: s['price'] for s in payload}:
        raise sources.DataSourceError('data_publish_readback_mismatch')
    try:
        stamp = datetime.fromisoformat(str(back.get('updatedAt')).replace('Z', '+00:00'))
        if stamp != datetime.fromisoformat(captured_at):
            raise ValueError()
    except (ValueError, TypeError):
        raise sources.DataSourceError('data_publish_readback_mismatch') from None
    return dict(summary, status='ok', published=True, readbackVerified=True, stored=written['stored'])


def main() -> int:
    parser = argparse.ArgumentParser(description="HiThink primary/public fallback private market snapshot")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--dry-run', action='store_true')
    group.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(sync(publish=args.publish), ensure_ascii=False));return 0
    except Exception as error:
        import re
        category = getattr(error, 'category', 'private_market_snapshot_failed')
        if not isinstance(category, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,100}', category):
            category = 'private_market_snapshot_failed'
        print(json.dumps({'status':'error', 'category':category, 'published':False}));return 2

if __name__ == "__main__":
    raise SystemExit(main())
