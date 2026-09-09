#!/usr/bin/env python3
"""Separate Part 3 basis; latest annual distribution + latest interim, never TTM.

Legacy MX DTOs are source evidence, NOT update_market.parse's lossy stock fields.
Annual labels do not prove distribution scope: require explicit 本次/末期/不含中期
wording OR a per-share entitlement row with registration and settlement dates.
Full-year or unspecified annual scope stays ambiguous. No allocation,
subtraction, extrapolation, price fetch, AI, or formal-field mutation.
"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import re
import unicodedata
from zoneinfo import ZoneInfo

import update_market as market
from forward_dividend_basis import calculate_forward_basis, USABLE

NUMBER = r'(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)'
PLAN_RE = re.compile(r'(?<![\d.])(?:每)?10(?:股)?派(?:发)?(?:现金红利)?\s*(' + NUMBER + r')\s*元')
POLICY_RE = re.compile(r'政策|规划|预测|预计|估算|不低于|不超过|至少|至多|约|至|~|～|税后|扣税|美元|港元|港币')
ZERO_RE = re.compile(r'不分配|不分红|不派(?:发)?(?:现金红利|现金股利|现金股息)|不进行(?:现金)?(?:利润)?分配')


def plan_amount(plan, pretax=None):
    """No fuzzy number extraction; policy, ranges and multiple plans stay null."""
    compact = re.sub(r'\s+', '', plan)
    if POLICY_RE.search(compact):
        return None
    matches = PLAN_RE.findall(compact)
    if len(matches) == 1:
        value = Decimal(matches[0]) / 10
    elif compact in ('', '-', '--', '无', 'None') and re.fullmatch(NUMBER, str(pretax)):
        value = Decimal(str(pretax))
    else:
        return None
    return value if 0 < value < 1000000 and value == value.quantize(Decimal('.000000000001')) else None


def publication_date(value, as_of):
    """Missing stays null; invalid/future dates cannot silently order revisions."""
    if value in (None, '', '-', '--'):
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value)):
            later = date.fromisoformat(value) > as_of.astimezone(ZoneInfo('Asia/Shanghai')).date()
        else:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError()
            later = parsed > as_of
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError('forward_basis_publication_date_invalid') from error
    if later:
        raise ValueError('forward_basis_publication_after_as_of')
    return value


def corroborate(events, code, period, plan, as_of):
    """Existing Part 4 fact: same issuer, fiscal slot and exact quantified plan.

    A same-code policy notice alone is NOT enough. Do not infer fiscal year from
    its notice date, nor use another year's official event to approve this row.
    """
    def compact(value):
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value)))
    matches = []
    for event in events:
        if (not isinstance(event, dict) or event.get('code') != code
                or event.get('type') != '中期分红预披露' or event.get('stage') != 'pre_disclosure'
                or market.dividend_period(event.get('title')) != period):
            continue
        identity = re.fullmatch(r'eastmoney:(AN\d{12,32})', str(event.get('id', '')))
        if not identity or event.get('sourceUrl') != f'https://data.eastmoney.com/notices/detail/{code}/{identity.group(1)}.html':
            continue
        evidence_plan = str(event.get('description', '')).removeprefix('结构化分红核对 · ')
        if compact(evidence_plan) != compact(plan) or plan_amount(evidence_plan) is None:
            continue
        if publication_date(event.get('date'), as_of) is not None:
            matches.append(event)
    if len({e['date'] for e in matches}) > 1:
        raise ValueError('forward_basis_official_date_conflict')
    return deepcopy(sorted(matches, key=lambda e: e['id'])[0]) if matches else None


def normalize_dtos(dtos):
    """Normalize actual MX cross-sectional issuer rows before coverage checks.

    The time period comes only from explicit field granularity and exact fiscal
    start/end dates, never title-leading issuer or the calendar collection year.
    Keep original row identity/index and period metadata in source field evidence.
    """
    normalized = []
    issuer_re = re.compile(r'^(.+?)\s*\((\d{5,6})\.(SH|SZ|BJ|HK)\)(?:\(最新年结日\d{2}-\d{2}\))?$')
    for dto in dtos:
        if not isinstance(dto, dict):
            raise ValueError('forward_basis_dto_invalid')
        table = dto.get('table') or {}
        heads = table.get('headName', [])
        if not isinstance(heads, list):
            raise ValueError('forward_basis_headers_invalid')
        # Annual aggregate count/sum is not an individual distribution record.
        if (dto.get('field') or {}).get('returnSourceCode') == 'CASHAMTCS':
            continue
        field = dto.get('field') or {}
        # This annual-only scalar matrix has no distribution status/dates.
        # Its leading dto.code names only the first column issuer. Do not turn
        # it into an invalid-period event or use it to claim stock coverage.
        if (dto.get('dataPositionEnum') == 'EntityLeftTimeTop'
                and field.get('returnSourceCode') == 'SFCFJCXG'
                and field.get('fixedParamValue') == 'AssignType=1,CurType=2'):
            columns = {k: v for k, v in table.items() if k != 'headName'}
            if (not heads or not columns or not all(re.fullmatch(r'20\d{2}', str(h)) for h in heads)
                    or not all(issuer_re.fullmatch(k) and isinstance(v, list) and len(v) == len(heads)
                               for k, v in columns.items())):
                raise ValueError('forward_basis_auxiliary_layout_invalid')
            continue
        matches = [issuer_re.fullmatch(str(h).strip()) for h in heads]
        if not any(matches):
            normalized.append(dto)
            continue
        if not all(matches):
            raise ValueError('forward_basis_mixed_row_dimensions')
        field = dto.get('field') or {}
        try:
            start = date.fromisoformat(field['startDate'][:10])
            end = date.fromisoformat(field['endDate'][:10])
            if start.year != end.year or (start.month, start.day) != (1, 1):
                raise ValueError()
            kind = field['dateGranularity']
            if kind == 'YEAR' and (end.month, end.day) == (12, 31):
                period = f'{start.year}年报'
            elif kind == 'HALF_YEAR' and (end.month, end.day) == (6, 30):
                period = f'{start.year}中报'
            else:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ValueError('forward_basis_cross_section_period_invalid') from None
        if any(isinstance(v, list) and len(v) != len(heads) for v in table.values()):
            raise ValueError('forward_basis_column_length_invalid')
        seen = set()
        for i, match in enumerate(matches):
            assert match is not None
            name, code, exchange = match.groups()
            if exchange == 'HK':
                continue  # names-only provider queries may also return H shares
            if len(code) != 6:
                raise ValueError('forward_basis_issuer_code_invalid')
            if code in seen:
                raise ValueError('forward_basis_duplicate_issuer_rows')
            seen.add(code)
            row = deepcopy(dto)
            row['code'] = f'{code}.{exchange}'
            row['title'] = f'{name}({code}.{exchange})的分红字段'
            row['dataPositionEnum'] = 'NormalizedIssuerPeriod'
            row['table'] = {k: [v[i]] if isinstance(v, list) else deepcopy(v) for k, v in table.items()}
            row['table']['headName'] = [period]
            row['field'] = {**deepcopy(field), '_originalLayout': {
                'headName': heads[i], 'rowIndex': i, 'title': dto.get('title'),
                'layout': dto.get('dataPositionEnum')}}
            normalized.append(row)
    return normalized


def build_records(dtos, stocks, official_events, as_of):
    """Pure DTO normalization and JSON-safe records; input is never mutated."""
    try:
        observed = datetime.fromisoformat(as_of.replace('Z', '+00:00'))
        if observed.tzinfo is None:
            raise ValueError()
    except (ValueError, AttributeError) as error:
        raise ValueError('forward_basis_as_of_invalid') from error
    if (not 1 <= len(stocks) <= 50 or any(not isinstance(s.get('code'), str)
            or not re.fullmatch(r'\d{6}', s['code']) or not s.get('name') for s in stocks)
            or len({s['code'] for s in stocks}) != len(stocks)):
        raise ValueError('forward_basis_watchlist_invalid')
    dtos = normalize_dtos(dtos)
    market.assert_dividend_dto_coverage(dtos, stocks)
    events = []
    for dto in dtos:
        if not isinstance(dto, dict):
            raise ValueError('forward_basis_dto_invalid')
        labels = str(dto.get('title', '')) + ' ' + str(dto.get('code', ''))
        codes = {s['code'] for s in stocks if s['code'] in labels or s['name'] in labels}
        explicit_codes = set(re.findall(r'(?<!\d)\d{6}(?!\d)', labels))
        if len(codes) > 1 or (codes and explicit_codes - codes):
            raise ValueError('forward_basis_identity_conflict')
        if not codes:
            continue
        code = next(iter(codes))
        table = dto.get('table') or {}
        heads = table.get('headName', [])
        if not isinstance(heads, list):
            raise ValueError('forward_basis_headers_invalid')
        for column in table.values():
            if isinstance(column, list) and len(column) != len(heads):
                raise ValueError('forward_basis_column_length_invalid')
        plans = market.table_values(dto, '分红方案')
        progress = market.table_values(dto, '方案进度', '分红方案进度')
        pretax = market.table_values(dto, '每股股利(税前,元)', '每股股利(税前)')
        declared = market.table_values(dto, '每股股利(税前,已宣告)', '每股股利(税前，已宣告)')
        for i, header in enumerate(heads):
            period = market.dividend_period(header)
            plan = str(plans[i] if i < len(plans) else '')
            raw_status = str(progress[i] if i < len(progress) else '')
            raw_pretax = pretax[i] if i < len(pretax) else None
            kind = period[1] if period else None
            selected_field = '每股股利(税前)'
            if kind == 'interim' and plan_amount('', raw_pretax) is None and i < len(declared):
                raw_pretax = declared[i]
                selected_field = '每股股利(税前,已宣告)'
            amount = plan_amount(plan, raw_pretax)
            scope_text = re.sub(r'\s+', '', str(header) + ' ' + plan)
            scope = 'distribution' if kind == 'interim' or re.search(r'本次|末期|不含已派中期', scope_text) else 'unknown'
            distribution_dates = {}
            for date_kind, label in (('registration', '股权登记日'), ('ex_dividend', '除权除息日'), ('payment', '派息日')):
                col = market.table_values(dto, label)
                value = col[i] if col else None
                if value not in (None, '', '-', '--'):
                    try:
                        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                            raise ValueError()
                        date.fromisoformat(value)
                    except (ValueError, TypeError) as error:
                        raise ValueError('forward_basis_distribution_date_invalid') from error
                else:
                    value = None
                distribution_dates[date_kind] = value
            reg = distribution_dates['registration']
            settlements = [v for k, v in distribution_dates.items() if k != 'registration' and v]
            if reg and any(reg > v for v in settlements):
                raise ValueError('forward_basis_distribution_date_conflict')
            # A per-share entitlement row with registration + settlement dates
            # describes this payment, unlike an undated annual total/report row.
            if reg and settlements and re.fullmatch(NUMBER, str(raw_pretax)):
                scope = 'distribution'
                scope_text += ' | dated_per_share_entitlement'
            if re.search(r'全年|年度累计|年度合计|(?<!不)含中期|包括中期|包含中期', scope_text):
                scope = 'full_year'
            status = 'implemented' if market.implementation_status(raw_status) else 'announced' if market.planned_status(raw_status) or raw_status.strip() == '股东大会预案' else 'unknown'
            if status == 'implemented' and any(date.fromisoformat(v) > observed.astimezone(ZoneInfo('Asia/Shanghai')).date() for v in distribution_dates.values() if v):
                status = 'announced'  # upstream stage can mean scheduled, not paid
            if raw_status.strip() in ('取消', '终止', '不实施', '未通过', '未公告'):
                status = 'cancelled'
            if ZERO_RE.search(plan + raw_status) and not POLICY_RE.search(plan):
                if status in ('implemented', 'announced') or ZERO_RE.fullmatch(raw_status.strip()):
                    status = 'no_distribution'
                    if amount is None:
                        amount = Decimal(0)
            date_columns = [market.table_values(dto, label) for label in ('公告日期', '公告日', '预案公告日', '方案公告日')]
            published_dates = {publication_date(col[i], observed) for col in date_columns if col and col[i] not in (None, '', '-', '--')}
            if len(published_dates) > 1:
                raise ValueError('forward_basis_publication_date_conflict')
            published = next(iter(published_dates), None)
            official, normalization_reason = None, None
            if raw_status.strip() == '预披露':
                official = corroborate(official_events, code, period, plan, observed) if amount is not None and kind == 'interim' else None
                if official is None:
                    status, normalization_reason = 'unknown', 'pre_disclosure_not_exactly_corroborated'
                elif published is not None and published != official['date']:
                    raise ValueError('forward_basis_publication_date_conflict')
                else:
                    published = official['date']
            events.append({'code': code, 'year': period[0] if period else None, 'kind': kind,
                           'amount': amount, 'status': status, 'amount_scope': scope,
                           'published_at': published,
                           'source': {'dto_title': dto.get('title'), 'header': header,
                                      'raw_plan': plan, 'raw_progress': raw_status, 'raw_pretax': raw_pretax,
                                      'dto_code': dto.get('code'), 'field': {**deepcopy(dto.get('field') or {}), '_selectedPerShareField': selected_field},
                                      'name_map': deepcopy(dto.get('nameMap', {})), 'published_at': published,
                                      'normalization_reason': normalization_reason, 'official_notice': official,
                                      'distribution_dates': distribution_dates,
                                      'scope_evidence': scope_text, 'row_index': i}})
    results = calculate_forward_basis(events, codes=[s['code'] for s in stocks])
    records = []
    for code, result in results.items():
        components = {}
        for kind, item in result['components'].items():
            components[kind] = {
                'year': item.get('year'), 'kind': kind,
                'amount': float(item['amount']) if item['status'] in USABLE and item['amount'] is not None else None,
                'status': item['status'], 'reason': (item.get('source') or {}).get('normalization_reason') or item.get('reason'),
                'amount_scope': item.get('amount_scope', 'unknown'),
                'published_at': item.get('published_at'), 'source': deepcopy(item.get('source')),
                'evidence': [deepcopy(e.get('source')) for e in item.get('evidence', [])],
            }
        records.append({'code': code, 'amount': float(result['amount']) if result['amount'] is not None else None,
                        'status': result['status'], 'reason': ('invalid_fiscal_period' if any(not e['period_valid'] for e in result['audit']) else next((c['reason'] for c in components.values() if c['status'] not in USABLE), None)),
                        'components': components, 'asOf': as_of})
    return records


def query_forward(names, year):
    """Same legacy provider and request contract, explicit forward metrics."""
    query=(f'{names}（仅A股）{year}年度分配、{year}年中期分配和{year+1}年中期分配的现金分红明细，'
           '每期分别列出分红方案、方案进度、每股股利(税前)、每股股利(税前,已宣告)、股权登记日、除权除息日、派息日。'
           '不使用全年累计金额替代单次分配；未公告或明确不分配请保留原始状态。')
    response=market.requests.post(market.API,headers={'apikey':market.os.environ['MX_APIKEY'],'Content-Type':'application/json'},json={'toolQuery':query},timeout=45)
    response.raise_for_status()
    payload=response.json()
    if payload.get('status') != 0:
        raise ValueError('forward_basis_provider_failed')
    message = str(((payload.get('data') or {}).get('data') or {}).get('message') or '')
    if '本周' in message and '上限' in message:
        raise ValueError('forward_basis_provider_weekly_quota_exhausted')
    if not market.result_dtos(payload):
        raise ValueError('forward_basis_provider_empty_result')
    return payload


def run(part4, *, publish=False, now=None, query_fn=None):
    """Runtime-only I/O via the existing authenticated session/provider helpers.

    Dry-run still reads the private watchlist and provider, but never obtains a
    writer capability or writes anything. Only aggregate status reaches stdout.
    """
    worker, config, token = part4.load_private_session()
    watchlist = part4.private_watchlist(worker, config, token)
    stocks = [{'code': s.code, 'name': s.name} for s in watchlist]
    base = part4.private_rpc(worker, config, token, 'personal_get_part4', {})
    if (not isinstance(base, dict) or not isinstance(base.get('stocks'), list)
            or not isinstance(base.get('events', []), list)
            or len(base['stocks']) != len(stocks)
            or {s.get('code') for s in base['stocks'] if isinstance(s, dict)} != {s['code'] for s in stocks}):
        raise ValueError('forward_basis_private_market_coverage_invalid')
    start = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    query_fn = query_fn or query_forward
    dtos = []
    batches = market.dividend_query_batches(stocks)
    for batch in batches:
        payload = query_fn('、'.join(s['name']+'('+s['code']+')' for s in batch), start.astimezone(ZoneInfo('Asia/Shanghai')).year - 1)
        batch_dtos = normalize_dtos(market.result_dtos(payload))
        market.assert_dividend_dto_coverage(batch_dtos, batch)
        dtos.extend(batch_dtos)
    as_of = (now or datetime.now(ZoneInfo('Asia/Shanghai'))).isoformat(timespec='seconds')
    records = build_records(dtos, stocks, base.get('events', []), as_of)
    if publish:
        secret = part4.part4_writer_secret(worker, config)
        written = part4.private_rpc(worker, config, token, 'personal_sync_forward_basis',
                                   {'p_as_of': as_of, 'p_records': records, 'p_writer_secret': secret})
        if not isinstance(written, dict) or type(written.get('stored')) is not int or written['stored'] != len(records):
            raise ValueError('forward_basis_publish_response_invalid')
        readback = part4.private_rpc(worker, config, token, 'personal_get_part4_v2', {})
        actual = {s.get('code'): s.get('forwardBasis') for s in readback.get('stocks', [])} if isinstance(readback, dict) else {}
        expected = {r['code']: {k: v for k, v in r.items() if k != 'code'} for r in records}
        if actual != expected:
            raise ValueError('forward_basis_publish_readback_mismatch')
    counts = {status: sum(r['status'] == status for r in records) for status in ('ready', 'missing', 'ambiguous', 'conflict', 'negated')}
    return {'status': 'ok', 'watchlistCount': len(stocks), 'coveredCount': len(records),
            'coverageComplete': True, 'queryBatches': len(batches), 'readyCount': counts['ready'],
            'statusCounts': counts, 'published': publish, 'readbackVerified': publish,
            'asOf': as_of, 'private_payload_not_emitted': True}


def parse_args(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true', help='default: authenticated reads only; aggregate output')
    modes.add_argument('--publish', action='store_true', help='explicitly publish the separate basis and verify v2 readback')
    return parser.parse_args(argv)


def main(argv=None):
    import json
    args = parse_args(argv)
    try:
        import part4_official_announcement_sync as part4
        summary = run(part4, publish=args.publish)
        print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as error:
        # Never print transport errors/private records/credentials in tracebacks.
        category = str(error) if isinstance(error, ValueError) and re.fullmatch(r'forward_basis_[a-z_]+', str(error)) else 'forward_basis_sync_failed'
        print(json.dumps({'status': 'error', 'category': category, 'private_payload_not_emitted': True}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
