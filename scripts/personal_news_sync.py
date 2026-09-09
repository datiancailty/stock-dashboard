#!/usr/bin/env python3
"""Private Part 5 news-search refresh; never invokes legacy main/public files.

Pure cleaning, identification, fingerprinting and numeric extraction are the
working update_news.py baseline. Search hits are NOT independently verified
company announcements (Part 4 remains separate). No AI/strategy/trading work.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import requests
from datetime import datetime, timedelta
import update_news as legacy
import part4_official_announcement_sync as part4


class NewsSyncError(RuntimeError):
    """Only fixed, sanitized categories reach the CLI."""


def timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})', value
    ):
        raise NewsSyncError('news_timestamp_invalid')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise NewsSyncError('news_timestamp_invalid') from None


def plan_batches(stocks: list[dict], old: dict, now: datetime) -> list[dict]:
    """Two-day overlap; newly tracked codes get previous-year-Jan-1 history.

    Coverage means every planned <=5-symbol search batch succeeded, not that
    the search service is exhaustive or that returned articles are official.
    """
    if not isinstance(stocks, list) or len(stocks) > 50 or not isinstance(old, dict) or not isinstance(old.get('items'), list):
        raise NewsSyncError('news_private_shape_invalid')
    seen = set()
    for stock in stocks:
        if not isinstance(stock, dict) or not isinstance(stock.get('code'), str) or not re.fullmatch(r'[0-9]{6}', stock['code']) or stock['code'] in seen:
            raise NewsSyncError('news_watchlist_invalid')
        if not isinstance(stock.get('name'), str) or not 1 <= len(stock['name'].strip()) <= 80 or stock['name'] != stock['name'].strip():
            raise NewsSyncError('news_watchlist_invalid')
        seen.add(stock['code'])
    tracked = old.get('trackedStockCodes', [])
    if not isinstance(tracked, list) or any(not isinstance(c, str) or not re.fullmatch(r'[0-9]{6}', c) for c in tracked) or len(tracked) > 50 or len(set(tracked)) != len(tracked):
        raise NewsSyncError('news_tracking_invalid')
    if any(not isinstance(i, dict) or not isinstance(i.get('id'), str) for i in old['items']):
        raise NewsSyncError('news_history_invalid')
    last = timestamp(old['lastScanAt']) if 'lastScanAt' in old else None
    if last and (last >= now or last.year < 2000):
        raise NewsSyncError('news_scan_not_monotonic')
    history = f'{now.astimezone(legacy.BJ).year - 1}-01-01'
    incremental = (last.astimezone(legacy.BJ) - timedelta(days=2)).date().isoformat() if last else history
    groups = {'incremental': [], 'history': []}
    for stock in stocks:
        groups['incremental' if last and stock['code'] in tracked else 'history'].append(stock['code'])
    return [{'kind': kind, 'since': incremental if kind == 'incremental' else history, 'codes': codes[i:i + 5]}
            for kind, codes in groups.items() for i in range(0, len(codes), 5)]


def validate_item(item: dict, now: datetime) -> None:
    """Fail the whole search on invalid new matched evidence, never skip it."""
    bounds = {'title': (1, 500), 'source': (1, 200), 'type': (1, 80), 'url': (0, 2048),
              'summary': (0, 421), 'calculation': (1, 2000), 'name': (1, 80)}
    for key, (low, high) in bounds.items():
        text = item[key]
        if not isinstance(text, str) or not low <= len(text) <= high or text != text.strip() or re.search(r'[\x00-\x1f\x7f]', text):
            raise NewsSyncError('news_item_invalid_preserving')
    if item['url'] and not re.fullmatch(r'https?://[^\s/]+[^\s]*', item['url']):
        raise NewsSyncError('news_item_invalid_preserving')
    published = item['publishedAt']
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?', published):
        raise NewsSyncError('news_item_timestamp_invalid')
    try:
        parsed = datetime.fromisoformat(published.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=legacy.BJ)
        if parsed.year < 2000 or parsed > now + timedelta(minutes=5):
            raise ValueError('range')
    except ValueError:
        raise NewsSyncError('news_item_timestamp_invalid') from None
    for key, value in item['inputs'].items():
        if key == 'quotes':
            if not isinstance(value, list) or len(value) > 3 or any(not isinstance(q, str) or len(q) > 2000 for q in value):
                raise NewsSyncError('news_item_invalid_preserving')
        elif value is not None and (type(value) not in (int, float) or not 0 <= value <= 1e16):
            raise NewsSyncError('news_item_invalid_preserving')
    dps = item['estimatedDividendPerShare']
    if dps is not None and (type(dps) not in (int, float) or not 0 <= dps <= 1e6):
        raise NewsSyncError('news_item_invalid_preserving')


def map_items(raw_items: list[dict], stocks: list[dict], old_items: list[dict], now: datetime) -> list[dict]:
    """The exact legacy id -> source_id mapping; first observation wins."""
    known = {item['id'] for item in old_items}
    additions = []
    for raw in raw_items:
        stock = legacy.identify_stock(raw, stocks)
        if not stock:
            continue
        uid = legacy.item_id(stock, raw)
        if uid in known:
            continue
        title, content = legacy.clean_text(raw.get('title')), legacy.clean_text(raw.get('content'))
        url = str(raw.get('jumpUrl') or '').strip()
        additions.append({
            'id': uid, 'code': stock['code'], 'name': stock['name'], 'title': title,
            'publishedAt': str(raw.get('date') or ''), 'type': str(raw.get('informationType') or 'NEWS'),
            'source': legacy.clean_text(raw.get('source') or raw.get('insName') or '东方财富资讯'),
            'url': url if url.startswith(('http://', 'https://')) else '',
            'summary': content[:420] + ('…' if len(content) > 420 else ''),
            'firstSeenAt': now.isoformat(timespec='seconds'),
            **legacy.extract_estimate(content, title),
            'sourceClass': 'news_search', 'officialVerified': False,
        })
        validate_item(additions[-1], now)
        known.add(uid)
    return additions


def query_news(stocks: list[dict], since: str) -> list[dict]:
    """Legacy endpoint, query text and auth; malformed success is NOT zero hits.

    Unlike legacy.query_news's permissive .get(..., []) fallback, every nesting
    level and the final array must actually exist before scan success advances.
    MX_APIKEY must be provisioned by the authorized caller; no auth setup here.
    """
    names = '、'.join(stock['name'] for stock in stocks)
    query = (f'{names}自{since}以来，与现金分红、利润分配、分红派息实施、股东回报规划、'
             '分红比例承诺相关的最新公司公告和权威新闻。优先公司公告，保留原始来源链接。')
    try:
        key = os.environ['MX_APIKEY']
        if not key:
            raise ValueError('missing key')
        response = requests.post(legacy.API, headers={'apikey': key, 'Content-Type': 'application/json'},
                                 json={'query': query}, timeout=45)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or type(payload.get('status')) is not int or payload['status'] != 0:
            raise ValueError('status')
        inner = payload['data']['data']
        messages = [node.get('message', '') for node in (payload, payload.get('data', {}), inner) if isinstance(node, dict)]
        if any(re.search(r'达到上限|已达上限|额度|限流|quota|rate.?limit', str(message), re.I) for message in messages):
            raise ValueError('provider_business_limit')
        rows = inner['llmSearchResponse']['data']
        if not isinstance(rows, list) or len(rows) > 1000 or any(not isinstance(r, dict) for r in rows):
            raise ValueError('rows')
        return rows
    except Exception:
        raise NewsSyncError('news_search_failed_preserving') from None


def query_public_notices(stocks, since, *, now=None):
    """Public company-notice index only, not MX search or broad media coverage.

    Full bounded window per symbol; title/URL are index observations, not
    independently verified amounts. Missing full text remains empty/unknown.
    Do not update the scan watermark if pagination/identity fails.
    """
    now = now or datetime.now(legacy.BJ)
    try:
        start = datetime.strptime(since, '%Y-%m-%d').date().isoformat()
        end = now.astimezone(legacy.BJ).date().isoformat()
        if start > end: raise ValueError()
        result=[]
        for stock in stocks:
            observed=set();previous=None;initial=None;complete=False
            for page in range(1, 21):
                data=part4.fetch_notice_page(stock['code'],page)
                size,total=data.get('page_size'),data.get('total_hits')
                if type(size) is not int or not 1<=size<=100 or type(total) is not int or total<0:
                    raise ValueError()
                if initial is None:initial=(size,total)
                if initial!=(size,total):raise ValueError()
                if 'page_index' in data and data['page_index']!=page:raise ValueError()
                notes=data.get('list')
                if not isinstance(notes,list) or len(notes)!=max(0,min(size,total-(page-1)*size)):raise ValueError()
                reached=False
                for note in notes:
                    day=datetime.strptime(str(note.get('notice_date',''))[:10],'%Y-%m-%d').date().isoformat()
                    if previous and day>previous:raise ValueError()
                    previous=day
                    aid=note.get('art_code');title=note.get('title')
                    if not isinstance(aid,str) or not re.fullmatch(r'AN\d{12,32}',aid) or aid in observed:raise ValueError()
                    observed.add(aid)
                    if day<start:reached=True;continue
                    if day>end:continue
                    if not isinstance(title,str) or not title.strip():raise ValueError()
                    securities=note.get('codes')
                    if not isinstance(securities,list) or stock['code'] not in {s.get('stock_code') for s in securities if isinstance(s,dict)}:raise ValueError()
                    if re.search(r'分红|利润分配|权益分派|派息|股东回报',title):
                        result.append({'code':stock['code'],'title':title,'content':'','date':day,
                            'jumpUrl':f'https://data.eastmoney.com/notices/detail/{stock["code"]}/{aid}.html',
                            'informationType':'公告索引','source':'东方财富公司公告索引（非全文核验）'})
                if reached or page*size>=total:complete=True;break
            if not complete:raise ValueError()
        return result
    except Exception:
        raise NewsSyncError('news_public_index_failed_preserving') from None


def sync(*, publish: bool = False, query_fn=None, clock=None) -> dict:
    """Read only Part1/Part5; stage everything in memory, publish once or not at all.

    Dry-run still needs the private session and search credential, but never
    loads the writer secret, changes metadata or writes files. CLI summaries
    deliberately exclude symbols, articles, RPC bodies and exception details.
    """
    clock = clock or (lambda: datetime.now(legacy.BJ))
    query_fn = query_fn or query_public_notices
    try:
        worker, config, token = part4.load_private_session()
        part1 = part4.private_rpc(worker, config, token, 'personal_get_part1', {})
        old = part4.private_rpc(worker, config, token, 'personal_get_part5', {})
    except Exception:
        raise NewsSyncError('news_private_read_failed_preserving') from None
    stocks = part1.get('watchlist') if isinstance(part1, dict) else None
    if not isinstance(stocks, list):
        raise NewsSyncError('news_private_shape_invalid')
    started = clock()
    batches = plan_batches(stocks, old, started)
    snapshot = stocks
    stocks = [{'code': s['code'], 'name': s['name']} for s in snapshot]
    summary = {'status': 'ok', 'published': False, 'readbackVerified': False, 'coverageComplete': True,
               'coverageMeaning': 'successful_search_batches_not_exhaustive_results',
               'watchlistCount': len(stocks), 'attemptedBatchCount': len(batches),
               'successfulBatchCount': 0, 'new': 0, 'stored': 0,
               'private_payload_not_emitted': True}
    if not batches:
        return dict(summary, status='skipped', category='news_empty_watchlist_preserving')
    raw_items = []
    for batch in batches:
        selected = [s for s in stocks if s['code'] in batch['codes']]
        try:
            raw = query_fn(selected, batch['since'])
            if not isinstance(raw, list) or len(raw) > 1000 or any(not isinstance(r, dict) for r in raw):
                raise ValueError('invalid response')
            raw_items.extend(raw)
        except Exception:
            raise NewsSyncError('news_search_failed_preserving') from None
    completed = clock()
    additions = map_items(raw_items, stocks, old['items'], completed)
    if len(additions) > 1000:
        raise NewsSyncError('news_item_limit_preserving')
    summary.update(successfulBatchCount=len(batches), new=len(additions))
    if query_fn is query_public_notices:
        summary.update(source='public_company_notice_index', mxInvoked=False,
                       coverageMeaning='company_notice_index_window_not_broad_media_search')
    if publish:
        try:
            body = {
                'p_scan_started_at': started.isoformat(timespec='seconds'),
                'p_scan_completed_at': completed.isoformat(timespec='seconds'),
                'p_expected_last_scan_at': old.get('lastScanAt'),
                'p_watchlist': stocks, 'p_watchlist_snapshot': snapshot, 'p_batches': batches, 'p_items': additions,
                'p_writer_secret': part4.part4_writer_secret(worker, config),
            }
            result = part4.private_rpc(worker, config, token, 'personal_sync_news', body)
            if not isinstance(result, dict) or result.get('status') != 'ok' or result.get('lastScanAt') != body['p_scan_started_at'] or type(result.get('stored')) is not int or not 0 <= result['stored'] <= len(additions):
                raise ValueError('invalid response')
        except Exception:
            # A lost response may mean the atomic transaction committed. Never
            # claim rollback; re-read Part5 and recompute on the next invocation.
            raise NewsSyncError('news_publish_failed_or_unconfirmed') from None
        try:
            readback = part4.private_rpc(worker, config, token, 'personal_get_part5', {})
            if not isinstance(readback, dict) or not isinstance(readback.get('items'), list):
                raise ValueError('shape')
            if timestamp(readback.get('lastScanAt')) < timestamp(body['p_scan_started_at']):
                raise ValueError('scan not visible')
            stored_items = {i['id']: i for i in readback['items']}
            if len(stored_items) != len(readback['items']):
                raise ValueError('duplicate ids')
            # Preserve all prior payloads, not merely a count; new ids must also
            # be present with the intended provenance. Never print the evidence.
            for item in old['items'] + additions:
                if stored_items.get(item['id']) != item:
                    raise ValueError('history or additions mismatch')
            tracked = readback.get('trackedStockCodes')
            if not isinstance(tracked, list) or sorted(tracked) != sorted(s['code'] for s in stocks):
                raise ValueError('watchlist changed after publish')
        except Exception:
            raise NewsSyncError('news_publish_readback_unconfirmed') from None
        summary.update(published=True, stored=result['stored'], readbackVerified=True)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Private Part 5 search; default dry-run still reads private data and searches')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--publish', action='store_true', help='Explicitly authorize the single private news RPC write')
    mode.add_argument('--dry-run', action='store_true', help='Explicit no-write mode (the default)')
    args = parser.parse_args(argv)
    try:
        result = sync(publish=args.publish)
    except NewsSyncError as error:
        result = {'status': 'error', 'category': str(error), 'published': False,
                  'coverageComplete': False, 'private_payload_not_emitted': True}
    except Exception:
        result = {'status': 'error', 'category': 'news_unexpected_failure', 'published': False,
                  'coverageComplete': False, 'private_payload_not_emitted': True}
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result['status'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
