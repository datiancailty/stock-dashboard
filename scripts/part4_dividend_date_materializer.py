"""Offline Part 4 calendar candidate from explicit native F10 date fields.

No network, credentials, RPC, fiscal numerator, or historical document writes.
Date DTOs require the separate owner-scoped forward migration and date writer;
the old notice-only RPC still rejects them. Conversion alone is not Hosted or
publication evidence. A scheduled date is not proof of payment.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
import re

DATE_FIELDS = (
    ('EQUITY_RECORD_DATE', 'registration', '股权登记日'),
    ('EX_DIVIDEND_DATE', 'ex_dividend', '除权除息日'),
    ('PAY_CASH_DATE', 'payment', '派息日'),
)
SOURCE = '东方财富公司公告 + F10分红日期核对'


def preference_only_notice(title):
    """Explicit preference-share dividend subject, never a mixed ordinary notice."""
    import unicodedata
    if not isinstance(title, str):
        return False
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', title))
    return bool(re.search(r'优先股.{0,40}(?:股息|派息)', text)) and not re.search(r'A股|普通股', text, re.I)


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?: 00:00:00)?', value):
        raise ValueError('calendar_source_date_invalid')
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        raise ValueError('calendar_source_date_invalid') from None


def materialize_calendar(notices, payment_rows):
    """Preserve normalized notices; add exact INFO_CODE-bound date candidates.

    Accepts native RPT_F10_DIVIDEND_MAIN rows, not a cash-basis/TTM projection.
    The caller must separately prove complete source pagination and owner scope.
    This function proves no scan coverage, full-text verification or publication.
    Missing fields are reported, never imputed. Rows unrelated to the supplied
    notices are ignored; previous calendar storage is never read or replaced.
    """
    if any(not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch)
           for batch in (notices, payment_rows)):
        raise ValueError('calendar_input_shape_invalid')
    dates, missing, excluded = [], [], []
    seen_rows = set()
    for notice in notices:
        aid, code = notice.get('id', ''), notice.get('code', '')
        if (not isinstance(aid, str) or not re.fullmatch(r'eastmoney:AN[0-9]{12,32}', aid)
                or not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code)
                or any(not isinstance(notice.get(k), str) or not 1 <= len(notice[k]) <= limit
                       for k, limit in (('name', 80), ('title', 300)))
                or not isinstance(notice.get('sourceHash'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', notice['sourceHash'])
                or notice.get('stage') not in ('implementation', 'proposal', 'pre_disclosure')
                or (notice.get('stage') == 'implementation' and notice.get('source') != '东方财富公司公告')
                or notice.get('sourceUrl') != f"https://data.eastmoney.com/notices/detail/{code}/{aid.removeprefix('eastmoney:')}.html"):
            raise ValueError('calendar_notice_binding_invalid')
        _day(notice.get('date'))
        if notice.get('stage') != 'implementation':
            continue
        if preference_only_notice(notice['title']):
            excluded.append({'noticeId': notice['id'], 'reason': 'explicit_preference_share_notice'})
            continue
        matched = [row for row in payment_rows if notice['id'] == 'eastmoney:' + str(row.get('INFO_CODE', ''))]
        if not matched:
            raise ValueError('calendar_implementation_row_missing')
        for row in matched:
            code = notice['code']
            market = 'SH' if code.startswith('6') else 'SZ' if code.startswith(('0', '3')) else 'BJ'
            if (row.get('SECURITY_CODE') != code or row.get('SECUCODE') != code + '.' + market
                    or row.get('ASSIGN_OBJECT') not in ('A股股东', '全体股东') or row.get('ASSIGN_PROGRESS') != '实施方案'
                    or _day(row.get('NOTICE_DATE')) != notice['date']):
                raise ValueError('calendar_implementation_identity_conflict')
            # 特别分配 has no fiscal date in the source; preserve null, not June/December.
            period = row.get('REPORT_DATE')
            if not isinstance(period, str) or not re.fullmatch(r'[0-9]{4}\S{1,76}', period):
                raise ValueError('calendar_report_period_invalid')
            special = row.get('REPORT_TIME') is None and re.fullmatch(r'\d{4}特别分配', period)
            report = None if special else _day(row.get('REPORT_TIME'))
            if report is not None and report[:4] != period[:4]:
                raise ValueError('calendar_report_period_conflict')
            period_key = report or 'period-' + hashlib.sha256(period.encode()).hexdigest()[:16]
            identity = (notice['id'], code, period_key)
            if identity in seen_rows:
                raise ValueError('calendar_duplicate_event_id')
            seen_rows.add(identity)
            known = [_day(row[field]) for field, _, _ in DATE_FIELDS if row.get(field) not in (None, '', '-')]
            if any(day < notice['date'] for day in known) or known != sorted(known):
                raise ValueError('calendar_source_date_conflict')
            for field, kind, label in DATE_FIELDS:
                if row.get(field) in (None, '', '-'):
                    missing.append({'noticeId': notice['id'], 'reportDate': report, 'dateField': field})
                    continue
                day = _day(row[field])
                evidence = {'schemaVersion': 1, 'noticeSourceHash': notice['sourceHash'],
                            'row': row, 'dateField': field}
                digest = hashlib.sha256(json.dumps(evidence, ensure_ascii=False,
                                                  sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                dates.append({
                    'id': f"eastmoney-date:{row['INFO_CODE']}:{notice['code']}:{period_key}:{kind}",
                    'date': day, 'code': notice['code'], 'name': notice['name'],
                    'type': label, 'stage': 'implementation', 'title': notice['title'],
                    'description': f"结构化日期核对 · {row['REPORT_DATE']} · {label}",
                    'source': SOURCE, 'sourceUrl': notice['sourceUrl'], 'sourceHash': digest,
                    'noticeId': notice['id'], 'noticeDate': notice['date'],
                    'reportDate': report, 'reportPeriod': period, 'dateField': field,
                })
    events = deepcopy(notices) + dates
    if len({e['id'] for e in events}) != len(events):
        raise ValueError('calendar_duplicate_event_id')
    return {'status': 'local_candidate', 'requiresHostedMigration': True,
            'events': sorted(events, key=lambda e: (e['date'], e['code'], e['id'])),
            'implementationDateCount': len(dates), 'missingDates': missing,
            'excludedImplementationNotices': excluded}
