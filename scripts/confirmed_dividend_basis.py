"""Pure, independent paid A-share cash basis; never fills a forward slot.

Amounts are CNY/share, pre-tax. Window is (calendar-same-day last year, today].
Ordinary proof uses a complete RPT_SHAREBONUS_DET scan cross-verified with F10
explicit payment dates; bound implementation notices can strengthen cash proof.
Original public announcements also ground narrowly registered non-cash F10
classifications. No ex-date-to-payment inference or FX conversion.
This module performs no I/O and is not a historical point-in-time archive.
"""
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
import re
import unicodedata
from zoneinfo import ZoneInfo

from public_forward_basis import PLAN, NUM, body_stock_codes, compact, finite, iso_day, per_share

SOURCE = 'eastmoney_public_implemented_a_share'
BJ = ZoneInfo('Asia/Shanghai')
DAY = r'\d{4}[-/]\d{1,2}[-/]\d{1,2}'

# Immutable, source-reviewed classification, not an age-based exclusion list.
# PDF pp. 1-2 prove warrant distribution, not a cash dividend. Publication date
# is the notice-index date (the PDF itself is signed 2006-05-15 on p. 3).
NONCASH_F10_EVENTS = (MappingProxyType({
    'SECURITY_CODE': '600900', 'SECUCODE': '600900.SH',
    'sourceUrl': 'https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?type=web&code=SH600900',
    'NOTICE_DATE': '2006-05-16', 'EQUITY_RECORD_DATE': '2006-05-17',
    'REPORT_DATE': '2005其他分配', 'ASSIGN_PROGRESS': '实施方案', 'IS_PAYCASH': '0',
    'announcementUrl': 'https://data.eastmoney.com/notices/detail/600900/AN201202260003958582.html',
    'pdfUrl': 'https://pdf.dfcfw.com/pdf/H2_AN201202260003958582_1.pdf?1645883719000.pdf',
    'pdfSha256': '58ad25531a1ae7ee01c214cda1713d74103a099270276ad9f9a823eb41e9fa2d',
    'facts': '中国长江电力认股权证发行公告(2006-014)：每10股派发1.5份认股权证；发行价格人民币0元/份；登记日2006-05-17。',
}),)


def _verified_noncash_f10(payment, code):
    """Match only an unmatched F10 row; never classify an RPT cash row."""
    if payment.get('SECURITY_CODE') != code or not all(
            field in payment and payment[field] in (None, '')
            for field in ('IMPL_PLAN_PROFILE', 'PAY_CASH_DATE', 'EX_DIVIDEND_DATE')):
        return False
    for event in NONCASH_F10_EVENTS:
        if not all(payment.get(field) == event[field] for field in (
                'SECURITY_CODE', 'SECUCODE', 'sourceUrl', 'REPORT_DATE', 'ASSIGN_PROGRESS', 'IS_PAYCASH')):
            continue
        try:
            if all(iso_day(payment.get(field)) == event[field]
                   for field in ('NOTICE_DATE', 'EQUITY_RECORD_DATE')):
                return True
        except ValueError:
            pass
    return False


def _notice_text(doc, code):
    """Validate a collector-bound implementation document's local identity."""
    aid = doc.get('id', '')
    title = compact(unicodedata.normalize('NFKC', doc.get('title', '')))
    if (doc.get('code') != code or not re.fullmatch(r'AN\d{12,32}', aid)
            or doc.get('sourceUrl') != f'https://data.eastmoney.com/notices/detail/{code}/{aid}.html'
            or type(doc.get('pageCount')) is not int or doc['pageCount'] < 1
            or not isinstance(doc.get('pages'), list) or len(doc['pages']) != doc['pageCount']
            or not all(isinstance(p, str) and p for p in doc['pages'])):
        raise ValueError('confirmed_notice_identity_conflict')
    text = compact(unicodedata.normalize('NFKC', ''.join(doc['pages'])))
    if re.search(r'H股公告|港股公告', title) or set(body_stock_codes(text)) != {code}:
        raise ValueError('confirmed_notice_identity_conflict')
    if title.split(':')[-1] not in text:
        raise ValueError('confirmed_payment_evidence_missing')
    return text, title


def payment_notice(doc, row):
    """Read the A-share cash claim; do not infer payment from ex-dividend day."""
    text, title = _notice_text(doc, row['SECURITY_CODE'])
    report = iso_day(row['REPORT_DATE'])
    kinds = {'12-31': '年度|末期', '06-30': '半年度|中期', '03-31': '一季度|第一季度', '09-30': '三季度|第三季度'}
    period = kinds.get(report[5:])
    if not period or not re.search(report[:4] + r'年?(?:A股)?(?:' + period + r').*实施公告', title):
        raise ValueError('confirmed_notice_identity_conflict')
    if title.split(':')[-1] not in text:
        raise ValueError('confirmed_payment_evidence_missing')
    cash_pattern = r'A股每股现金红利人民币(' + NUM + r')元\(含税\)'
    if len({Decimal(x) for x in re.findall(cash_pattern, text)}) > 1:
        raise ValueError('confirmed_currency_amount_conflict')
    cash = re.search(cash_pattern, text)
    if not cash:
        bare = re.search(r'A股每股现金红利(' + NUM + r')元\(含税\)', text)
        explicit = re.search(r'A股股息将以人民币支付[^。]*金额为每股人民币(' + NUM + r')元\(含税', text)
        if bare and explicit:
            if Decimal(bare[1]) != Decimal(explicit[1]):
                raise ValueError('confirmed_currency_amount_conflict')
            cash = bare
    if not cash:
        raise ValueError('confirmed_payment_evidence_missing')
    return Decimal(cash[1]), _payment_dates(doc, text, row)[2]


def _payment_dates(doc, text, row=None):
    pattern = (r'股份类别股权登记日最后交易日除权\(息\)日现金红利发放日'
               r'A股(' + DAY + r')[－—-](' + DAY + r')(' + DAY + r')')
    dates = {tuple(date(*map(int, re.split('[-/]', v))).isoformat() for v in match) for match in re.findall(pattern, text)}
    if len(dates) > 1:
        raise ValueError('confirmed_payment_date_conflict')
    table = re.search(pattern, text)
    if not table:
        raise ValueError('confirmed_payment_evidence_missing')
    paid = '-'.join(f'{int(p):02}' if i else p for i, p in enumerate(re.split('[-/]', table[3])))
    registration, ex_day = [date(*map(int, re.split('[-/]', table[i]))).isoformat() for i in (1, 2)]
    paid = iso_day(paid)
    row = row or {}
    if (registration > ex_day or ex_day > paid or iso_day(doc['date']) > registration
            or (row.get('EX_DIVIDEND_DATE') and iso_day(row['EX_DIVIDEND_DATE']) != ex_day)
            or (row.get('EQUITY_RECORD_DATE') and iso_day(row['EQUITY_RECORD_DATE']) != registration)):
        raise ValueError('confirmed_payment_date_conflict')
    return registration, ex_day, paid


def _stock_only_plan(plan):
    # Explicit share bonus/transfer only; absence of a payment date is expected.
    return bool(re.fullmatch(r'10(?:送' + NUM + r'(?:股)?(?:转(?:增)?' + NUM + r'(?:股)?)?|转(?:增)?' + NUM + r'(?:股)?)', compact(plan)))


def _f10_payment(payment, code):
    """PAY_CASH_DATE is the F10 page's explicit 派息日, not its ex-date fallback."""
    secu = payment.get('SECUCODE', '')
    if (payment.get('SECURITY_CODE') != code or not re.fullmatch(re.escape(code) + r'\.(SH|SZ|BJ)', secu)
            or payment.get('sourceUrl') != 'https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?type=web&code=' + secu[-2:] + code):
        raise ValueError('confirmed_payment_identity_conflict')
    if payment.get('ASSIGN_PROGRESS') != '实施方案' or not payment.get('PAY_CASH_DATE'):
        raise ValueError('confirmed_payment_evidence_missing')
    paid = iso_day(payment['PAY_CASH_DATE'])
    published = iso_day(payment['NOTICE_DATE'])
    ex_day = iso_day(payment.get('EX_DIVIDEND_DATE'), optional=True)
    registration = iso_day(payment.get('EQUITY_RECORD_DATE'), optional=True)
    if (published > paid or (ex_day and ex_day > paid)
            or (registration and (registration > paid or (ex_day and registration > ex_day)))):
        raise ValueError('confirmed_payment_date_conflict')
    plan = re.fullmatch(r'10派(' + NUM + r')元(?:[（(]含税[）)])?', compact(payment.get('IMPL_PLAN_PROFILE')))
    amount = per_share(plan[1]) if plan else None
    return amount, paid


def build_confirmed_records(rows, stocks, as_of, *, coverage=None, notices=(), payment_rows=()):
    """Return independent candidates. See collect_confirmed() for coverage proof.

    coverage: {complete:true, scope:'all_distribution_history', source:SOURCE,
               asOf:<same aware timestamp>, codes:[...], rowCount:<all rows>}.
    notices use the index/content-bound public collector document contract.
    Missing/partial coverage or unverified payment means amount=null, not zero.
    """
    try:
        moment = datetime.fromisoformat(as_of.replace('Z', '+00:00'))
        if moment.tzinfo is None or moment.year < 2:
            raise ValueError()
        moment = moment.astimezone(BJ)
    except (ValueError, AttributeError, OverflowError):
        raise ValueError('confirmed_as_of_invalid') from None
    end = moment.date()
    start = end.replace(year=end.year - 1, day=min(end.day, monthrange(end.year - 1, end.month)[1]))
    if (not isinstance(stocks, list) or not 1 <= len(stocks) <= 50
            or any(not isinstance(s, dict) or not isinstance(s.get('code'), str) or not re.fullmatch(r'\d{6}', s['code']) for s in stocks)
            or len({s['code'] for s in stocks}) != len(stocks)):
        raise ValueError('confirmed_watchlist_invalid')
    expected = sorted(s['code'] for s in stocks)
    if (not isinstance(rows, list) or any(not isinstance(r, dict) or r.get('SECURITY_CODE') not in expected for r in rows)
            or any(not isinstance(p, dict) or p.get('SECURITY_CODE') not in expected for p in payment_rows)
            or any(not isinstance(d, dict) or d.get('code') not in expected for d in notices)):
        raise ValueError('confirmed_public_identity_invalid')
    complete = (isinstance(coverage, dict) and coverage.get('complete') is True
                and coverage.get('scope') == 'all_distribution_history'
                and coverage.get('source') == SOURCE and coverage.get('asOf') == as_of
                and coverage.get('codes') == expected
                and type(coverage.get('rowCount')) is int and coverage['rowCount'] == len(rows))
    result = []
    for stock in stocks:
        components = []
        total = Decimal(0)
        issues = []
        seen = {}
        used_notices = set()
        used_payments = set()
        for row in rows:
            if row['SECURITY_CODE'] != stock['code']:
                continue
            if row.get('ASSIGN_PROGRESS') != '实施分配':
                if row.get('ASSIGN_PROGRESS') not in ('董事会决议通过', '股东大会决议通过', '预披露'):
                    issues.append('confirmed_distribution_stage_missing')
                continue
            docs = [d for d in notices if d.get('code') == stock['code'] and d.get('date') == iso_day(row['NOTICE_DATE'])]
            try:
                if (not re.fullmatch(re.escape(stock['code']) + r'\.(SH|SZ|BJ)', row.get('SECUCODE', ''))
                        or iso_day(row['REPORT_DATE']) > iso_day(row['NOTICE_DATE'])
                        or iso_day(row['NOTICE_DATE']) > end.isoformat()):
                    raise ValueError('confirmed_table_identity_or_date_conflict')
                if _stock_only_plan(row.get('IMPL_PLAN_PROFILE')) and row.get('PRETAX_BONUS_RMB') in (None, '', 0, '0'):
                    used_payments.update(i for i, p in enumerate(payment_rows) if p.get('SECURITY_CODE') == stock['code'] and p.get('NOTICE_DATE') == row.get('NOTICE_DATE') and _stock_only_plan(p.get('IMPL_PLAN_PROFILE')))
                    continue
                matched = [(i, p) for i, p in enumerate(payment_rows) if p.get('SECURITY_CODE') == stock['code']
                           and iso_day(p.get('NOTICE_DATE')) == iso_day(row['NOTICE_DATE']) and p.get('ASSIGN_PROGRESS') == '实施方案']
                payment_claims = set()
                for i, p in matched:
                    try:
                        claim = _f10_payment(p, stock['code'])
                    except ValueError as error:
                        if str(error) == 'confirmed_payment_evidence_missing':
                            continue
                        raise
                    for field in ('EX_DIVIDEND_DATE', 'EQUITY_RECORD_DATE'):
                        if row.get(field) and p.get(field) and iso_day(row[field]) != iso_day(p[field]):
                            raise ValueError('confirmed_payment_date_conflict')
                    payment_claims.add(claim)
                    used_payments.add(i)
                if payment_claims and all(paid <= start.isoformat() for _, paid in payment_claims):
                    continue
                if len(payment_claims) > 1:
                    raise ValueError('confirmed_payment_version_conflict')
                if payment_claims:
                    cash, paid = next(iter(payment_claims))
                    # Explicit paid dates outside the window do not require an
                    # old full-text notice or historical amount normalization.
                    if paid <= start.isoformat() or paid > end.isoformat():
                        continue
                claims = set()
                for d in docs:
                    try:
                        claims.add(payment_notice(d, row))
                    except ValueError as error:
                        if not payment_claims or str(error) != 'confirmed_payment_evidence_missing':
                            raise
                if len(claims) > 1 or (claims and payment_claims and claims != payment_claims):
                    raise ValueError('confirmed_payment_version_conflict')
                if not claims and not payment_claims:
                    raise ValueError('confirmed_payment_evidence_missing')
                cash, paid = next(iter(claims or payment_claims))
                evidence_url = min(d['sourceUrl'] for d in docs) if claims else f"https://data.eastmoney.com/yjfp/detail/{stock['code']}.html"
                amount = per_share(row['PRETAX_BONUS_RMB'])
                plan = PLAN.fullmatch(compact(row.get('IMPL_PLAN_PROFILE')))
                if not plan or per_share(plan[1]) != amount or cash != amount:
                    raise ValueError('confirmed_amount_conflict')
            except ValueError as error:
                issues.append(str(error) if str(error).startswith('confirmed_') else 'confirmed_amount_conflict')
                continue
            used_notices.update(d['id'] for d in docs)
            period = iso_day(row['REPORT_DATE'])
            identity = (paid, amount)
            if period in seen:
                if seen[period] != identity:
                    issues.append('confirmed_payment_version_conflict')
                continue
            seen[period] = identity
            if start.isoformat() < paid <= end.isoformat() and amount > 0:
                components.append({'reportDate': iso_day(row['REPORT_DATE']), 'paymentDate': paid,
                                   'amount': float(amount), 'sourceUrl': evidence_url,
                                   'plan': row['IMPL_PLAN_PROFILE'] + ('' if claims else '；公开分红表＋F10派息日核对')})
                total += amount
        for i, payment in enumerate(payment_rows):
            if i in used_payments or payment.get('SECURITY_CODE') != stock['code'] or payment.get('ASSIGN_PROGRESS') != '实施方案':
                continue
            if _verified_noncash_f10(payment, stock['code']):
                continue
            try:
                if _stock_only_plan(payment.get('IMPL_PLAN_PROFILE')):
                    continue
                _, paid = _f10_payment(payment, stock['code'])
                if start.isoformat() < paid <= end.isoformat():
                    issues.append('confirmed_table_payment_coverage_incomplete')
            except ValueError as error:
                issues.append(str(error) if str(error).startswith('confirmed_') else 'confirmed_payment_date_conflict')
        for doc in notices:
            if doc.get('code') != stock['code'] or doc.get('id') in used_notices:
                continue
            try:
                text, _ = _notice_text(doc, stock['code'])
                paid = _payment_dates(doc, text)[2]
                if iso_day(doc['date']) > end.isoformat():
                    raise ValueError('confirmed_notice_future_conflict')
                if start.isoformat() < paid <= end.isoformat():
                    issues.append('confirmed_table_notice_coverage_incomplete')
            except ValueError as error:
                issues.append(str(error) if str(error).startswith('confirmed_') else 'confirmed_payment_date_conflict')
        components.sort(key=lambda c: (c['paymentDate'], c['reportDate'], c['sourceUrl']))
        if finite(total) is None or Decimal(str(float(total))) != total:
            issues.append('confirmed_total_amount_conflict')
        if not complete:
            issues.append('confirmed_window_coverage_incomplete')
        conflict = next((e for e in issues if e.endswith('conflict')), None)
        status = 'conflict' if conflict else 'missing' if issues else 'ready'
        result.append({'code': stock['code'], 'asOf': moment.isoformat(timespec='seconds'),
                       'amount': float(total) if status == 'ready' else None, 'status': status,
                       'reason': conflict or (issues[0] if issues else None),
                       'windowStart': start.isoformat(), 'windowEnd': end.isoformat(),
                       'components': components, 'source': SOURCE})
    return result
