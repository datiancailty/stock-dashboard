"""Dashboard-only read-only data adapters; contains no account or order API.

Full-batch validation precedes publication. Unknown/stale data never becomes 0.
No arbitrary host override: a provider key must never follow redirects.
"""
from __future__ import annotations
import math
import re
import time
import requests
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

HITHINK = 'https://fuyao.aicubes.cn'
ALLOWED = {('fuyao.aicubes.cn', '/api/a-share/prices/snapshot'),
           ('fuyao.aicubes.cn', '/api/a-share/prices/historical'),
           ('fuyao.aicubes.cn', '/api/a-share/calendar/trading-days'),
           ('push2.eastmoney.com', '/api/qt/ulist.np/get'),
           ('push2delay.eastmoney.com', '/api/qt/ulist.np/get')}

def request_json(url, params, headers, *, get=None, sleep=time.sleep, monotonic=time.monotonic):
    """At most 3 GET attempts / 45s total. Never retry a business write.

    Retry only 429, 4001, provider 5001..5003, 5xx and transient transport.
    Auth/schema/quota/unknown failures do not loop. Raw errors never escape.
    """
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or (parsed.netloc, parsed.path) not in ALLOWED or parsed.query or parsed.fragment:
        raise DataSourceError('data_endpoint_not_allowed')
    if parsed.netloc != 'fuyao.aicubes.cn' and any(k.lower() in {'x-api-key', 'apikey', 'authorization'} for k in headers):
        raise DataSourceError('data_credential_boundary')
    get = get or requests.get
    started = monotonic();last_failure='data_retry_exhausted'
    for attempt in range(3):
        remaining = 45 - (monotonic() - started)
        if remaining <= 0:
            raise DataSourceError('data_retry_budget_exhausted')
        retry_after = None
        try:
            response = get(url, params=params, headers=headers, timeout=min(20, remaining), allow_redirects=False)
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                retry_after = response.headers.get('Retry-After')
                raise DataSourceError('data_rate_limited' if response.status_code==429 else 'data_transient_failure')
            if response.status_code != 200:
                raise DataSourceError('data_http_failure')
            try:
                body = response.json()
            except ValueError:
                raise DataSourceError('data_response_invalid') from None
            if not isinstance(body, dict):
                raise DataSourceError('data_response_invalid')
            if parsed.netloc == 'fuyao.aicubes.cn':
                code = body.get('code')
                if type(code) is int and code in (4001, 5001, 5002, 5003):
                    retry_after = response.headers.get('Retry-After')
                    raise DataSourceError('data_rate_limited' if code==4001 else 'data_transient_failure')
                if type(code) is not int or code != 0:
                    raise DataSourceError('hithink_business_failure')
            return body
        except requests.Timeout:
            last_failure='data_timeout'
        except requests.ConnectionError:
            last_failure='data_transient_failure'
        except requests.RequestException:
            raise DataSourceError('data_transport_failure') from None
        except DataSourceError as error:
            if error.category not in {'data_transient_failure','data_rate_limited'}:
                raise
            last_failure=error.category
        if attempt == 2:
            raise DataSourceError(last_failure)
        delay = 2 ** (attempt + 1)
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except (ValueError, TypeError):
                try:
                    parsed_delay = parsedate_to_datetime(retry_after)
                    delay = (parsed_delay - datetime.now(parsed_delay.tzinfo)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    raise DataSourceError('data_retry_after_invalid') from None
            if not math.isfinite(delay) or delay < 0:
                raise DataSourceError('data_retry_after_invalid')
        if delay >= 45 - (monotonic() - started):
            raise DataSourceError('data_retry_budget_exhausted')
        sleep(delay)
from datetime import datetime
from zoneinfo import ZoneInfo
BJ = ZoneInfo('Asia/Shanghai')

class DataSourceError(ValueError):
    def __init__(self, category, *, retry_after=None):
        self.category = category
        self.retry_after = retry_after
        super().__init__(category)

def read_hithink_key(path=None):
    """Read only the established local owner-only credential. Never eval/source."""
    import os
    import stat
    from pathlib import Path
    value = os.environ.get('HITHINK_FINANCE_API_KEY', '').strip()
    if value:
        return value
    path = Path(path or Path.home()/'Library/Application Support/hithink-finance/credentials.env')
    # Validate every existing path component against symlink redirection.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise DataSourceError('hithink_credential_invalid')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384:
                raise DataSourceError('hithink_credential_invalid')
            lines = f.read().splitlines()
    except (OSError, UnicodeError):
        raise DataSourceError('hithink_credential_unavailable') from None
    values = []
    for line in lines:
        match = re.fullmatch(r'\s*(?:export\s+)?HITHINK_FINANCE_API_KEY\s*=\s*(.*?)\s*', line)
        if match:
            value = match[1]
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if not value or re.search(r'[\s\x00-\x1f\x7f]', value):
                raise DataSourceError('hithink_credential_invalid')
            values.append(value)
    if len(values) != 1:
        raise DataSourceError('hithink_credential_invalid')
    return values[0]

def parse_eastmoney_quotes(body, codes, now, expected_day):
    universe(codes)
    if not isinstance(body, dict) or type(body.get('rc')) is not int or body['rc'] != 0:
        raise DataSourceError('eastmoney_business_failure')
    data = body.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('diff'), list):
        raise DataSourceError('data_response_invalid')
    values, stamps = {}, []
    for row in data['diff']:
        if not isinstance(row, dict) or row.get('f12') not in codes or row['f12'] in values:
            raise DataSourceError('data_coverage_invalid')
        values[row['f12']] = finite_price(row.get('f2'))
        stamp = row.get('f124')
        if type(stamp) not in (int, float):
            raise DataSourceError('data_timestamp_invalid')
        stamps.append(provider_time(stamp * 1000, now, expected_day))
    if set(values) != set(codes):
        raise DataSourceError('data_coverage_incomplete')
    return {'quotes': [{'code': c, 'price': values[c]} for c in codes],
            'source': 'eastmoney_public_snapshot', 'quoteAsOf': min(stamps),
            'quoteTimeRange': [min(stamps), max(stamps)], 'priceMode': 'raw_unadjusted'}

def fetch_quotes(codes, now, *, expected_day, request=None, key_reader=None):
    """HiThink primary, timestamp-validated public fallback, never MX.

    Discard a partial primary batch instead of mixing it into another source.
    Both failing means no snapshot returned: caller must preserve old state.
    expected_day must be an independently verified exchange trading date.
    """
    wanted = universe(codes)
    request = request or request_json
    errors = []
    try:
        key = (key_reader or read_hithink_key)()
        body = request(HITHINK+'/api/a-share/prices/snapshot', {'thscodes': ','.join(wanted)}, {'X-api-key': key})
        result = parse_hithink_quotes(body, codes, now, expected_day)
        return {**result, 'fallbackReasons': [], 'mxInvoked': False}
    except DataSourceError as error:
        errors.append(error.category)
    for host in ('push2.eastmoney.com', 'push2delay.eastmoney.com'):
        try:
            body = request('https://'+host+'/api/qt/ulist.np/get',
                           {'fltt': '2', 'invt': '2', 'fields': 'f12,f2,f124',
                            'secids': ','.join(('1.' if c.startswith('6') else '0.')+c for c in codes)},
                           {'Referer': 'https://quote.eastmoney.com/', 'User-Agent': 'Mozilla/5.0'})
            result = parse_eastmoney_quotes(body, codes, now, expected_day)
            return {**result, 'fallbackReasons': errors, 'mxInvoked': False}
        except DataSourceError as error:
            errors.append(error.category)
    raise DataSourceError('data_all_quote_sources_failed_preserving')

def latest_trading_day(now, *, request=None, key_reader=None, completed=True):
    return _calendar_trading_day(now, request=request, key_reader=key_reader,
                                 cutoff=(16, 0) if completed else (0, 0))

def quote_trading_day(now, *, request=None, key_reader=None):
    """Quotes may be dated today from the 09:15 Beijing opening auction.

    Before that boundary use the preceding verified session, including holidays.
    This is not the 16:00 completed-daily-bar rule used for technical history.
    Naive caller times are interpreted as Beijing time, never the host timezone.
    """
    now = now.replace(tzinfo=BJ) if now.tzinfo is None else now.astimezone(BJ)
    return _calendar_trading_day(now, request=request, key_reader=key_reader, cutoff=(9, 15))

def _calendar_trading_day(now, *, request=None, key_reader=None, cutoff):
    request = request or request_json
    key = (key_reader or read_hithink_key)()
    body = request(HITHINK+'/api/a-share/calendar/trading-days', {}, {'X-api-key': key})
    if not isinstance(body, dict) or type(body.get('code')) is not int or body['code'] != 0:
        raise DataSourceError('data_calendar_failed')
    data = body.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('item'), list) or not data['item']:
        raise DataSourceError('data_calendar_invalid')
    # Calendar's data-ready timestamp must belong to this collection date.
    provider_time(data.get('timestamp'), now, now.astimezone(BJ).date().isoformat())
    dates=[]
    for row in data['item']:
        if not isinstance(row, dict) or not isinstance(row.get('date'), str) or not re.fullmatch(r'\d{8}', row['date']):
            raise DataSourceError('data_calendar_invalid')
        try:
            day = datetime.strptime(row['date'], '%Y%m%d').replace(tzinfo=BJ)
        except ValueError:
            raise DataSourceError('data_calendar_invalid') from None
        if day.date() > now.astimezone(BJ).date() or (dates and row['date'] <= dates[-1]):
            raise DataSourceError('data_calendar_invalid')
        if type(row.get('date_ms')) is not int or row['date_ms'] != int(day.timestamp()*1000):
            raise DataSourceError('data_calendar_invalid')
        dates.append(row['date'])
    if (now.astimezone(BJ).hour, now.astimezone(BJ).minute) < cutoff:
        dates=[d for d in dates if d < now.astimezone(BJ).strftime('%Y%m%d')]
    if not dates:raise DataSourceError('data_calendar_no_completed_session')
    return datetime.strptime(dates[-1], '%Y%m%d').date().isoformat()

def canonical(code):
    if not isinstance(code, str) or not re.fullmatch(r'(?:[036]\d{5})', code):
        raise DataSourceError('data_universe_invalid')
    return code + ('.SH' if code.startswith('6') else '.SZ')

def universe(codes):
    if not isinstance(codes, list) or not 1 <= len(codes) <= 50 or len(set(codes)) != len(codes):
        raise DataSourceError('data_universe_invalid')
    return {canonical(c): c for c in codes}

def finite_price(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise DataSourceError('data_price_invalid')
    return float(value)

def provider_time(value, now, expected_day):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise DataSourceError('data_timestamp_invalid')
    try:
        stamp = datetime.fromtimestamp(value/1000, BJ)
    except (ValueError, OSError, OverflowError):
        raise DataSourceError('data_timestamp_invalid') from None
    if stamp.date().isoformat() != expected_day or (stamp - now.astimezone(BJ)).total_seconds() > 300:
        raise DataSourceError('data_quote_stale_or_future')
    return stamp.isoformat(timespec='seconds')

def history_ready_time(value,now,last_day):
    if type(value) not in (int,float) or not math.isfinite(value):raise DataSourceError('data_timestamp_invalid')
    try:stamp=datetime.fromtimestamp(value/1000,BJ)
    except (ValueError,OSError,OverflowError):raise DataSourceError('data_timestamp_invalid') from None
    if stamp.date().isoformat()<last_day or (stamp-now.astimezone(BJ)).total_seconds()>300:
        raise DataSourceError('data_history_stale_or_future')
    return stamp.isoformat(timespec='seconds')

def parse_hithink_quotes(body, codes, now, expected_day):
    wanted = universe(codes)
    if not isinstance(body, dict) or type(body.get('code')) is not int or body['code'] != 0:
        raise DataSourceError('hithink_business_failure')
    data = body.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('item'), list):
        raise DataSourceError('data_response_invalid')
    # Envelope timestamp is provider readiness, NOT a security trade date.
    stamp = provider_time(data.get('timestamp'), now, now.astimezone(BJ).date().isoformat())
    values = {}
    for row in data['item']:
        if not isinstance(row, dict) or row.get('thscode') not in wanted or row['thscode'] in values:
            raise DataSourceError('data_coverage_invalid')
        values[row['thscode']] = finite_price(row.get('last_price'))
    if set(values) != set(wanted):
        raise DataSourceError('data_coverage_incomplete')
    return {'quotes': [{'code': wanted[s], 'price': values[s]} for s in wanted],
            'source': 'hithink_snapshot', 'quoteAsOf': stamp, 'priceMode': 'raw_unadjusted',
            'quoteTimestampKind':'provider_ready_time','exchangeQuoteDate':None}
