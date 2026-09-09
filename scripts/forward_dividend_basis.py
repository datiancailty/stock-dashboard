"""Offline Part 3 basis: latest fiscal annual distribution + latest interim.

Public API: calculate_forward_basis(events, codes=()) -> {code: result}.
This module accepts NORMALIZED event dictionaries, not aggregated stock rows or
payment-calendar events returned by update_market.parse (those lose period,
revision and source evidence). No imports of workers, I/O, network or credentials.

Input contract (caller must retain source facts when normalizing MX DTO rows):
* code: nonempty string, retaining leading zeros; year: integer fiscal year;
  kind: 'annual' or 'interim'. Announcement/payment year is NOT fiscal year.
* amount: nonnegative finite pretax CNY PER SHARE; decimal strings preferred.
  A '10派X元' DTO plan must be divided by ten by the upstream normalizer.
* status: 'implemented', or 'announced' for an explicitly announced quantitative
  plan pending implementation; 'no_distribution' for explicit non-distribution;
  'cancelled'/'not_announced'/'negated' block calculation. Unknown/raw statuses
  are missing, never fuzzy substring-matched. Pre-disclosure is not assumed to
  be an announced quantitative plan merely because it contains a number.
* amount_scope: 'distribution' MUST mean this individual annual/interim payment,
  not the whole fiscal-year total. 'full_year', absent or unknown scope remains
  ambiguous, including zero/non-distribution. Never infer scope from an annual
  label, and never subtract an unrelated/new interim from a full-year amount.
  Resolve an explicit source breakdown upstream, retaining that evidence.
* source: nonempty dict containing DTO snapshot or retrievable source fields
  (e.g. title/code/row_index/column names/announcement URL/scope evidence).
* optional revision: nonnegative integer, comparable within a fiscal period;
  otherwise published_at: ISO date or timezone-aware ISO datetime. Revisions
  take precedence only when ALL competing rows have a comparable revision.
  Dates and instants cannot be mixed; timestamps without a zone are unordered.

Each kind independently picks the greatest supplied fiscal year (no TTM and no
calendar-year cutoff), then the latest comparable version. Older interim data
is replaced, never added. Identical semantic copies coalesce with all evidence;
contradictions in a tied version are conflict, unordered versions ambiguous.
No fallback to older good data when the newest period/version is unusable.
Caller supplies the as-of evidence set; this is not a forecasting or fetching
engine and cannot attest that the input includes the newest real announcement.

Output amount is Decimal only when BOTH components are usable, otherwise None.
Explicit no_distribution alone supplies zero; absent evidence never does.
Result status is ready/conflict/ambiguous/negated/missing (in that precedence).
components retain chosen period/status/amount/raw/source plus evidence for all
latest-version candidates; audit retains every normalized row and original raw
row, including replaced periods and invalid-period blockers. For conflicts the
representative raw/source is not a winner: consult the entire evidence list.
Pass the expected universe in codes to surface stocks with no input as missing.
Invalid/missing event code raises ValueError because it cannot be safely assigned
to a stock. Other incomplete facts remain explicit states. No input is mutated.
Decimal serialization for UI/storage is intentionally left to a future adapter.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import re


USABLE = {'implemented', 'announced', 'no_distribution'}


def _amount(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def _normalize(event):
    if not isinstance(event.get('code'), str) or not event['code'].strip():
        raise ValueError('event code must be a nonempty string (preserve leading zeros)')
    item = {**deepcopy(event), 'raw': deepcopy(event), 'amount': _amount(event.get('amount'))}
    item['period_valid'] = (type(event.get('year')) is int and 1900 <= event['year'] <= 9999
                            and event.get('kind') in ('annual', 'interim'))
    status = event.get('status')
    reason = None
    if status in {'cancelled', 'not_announced', 'negated'}:
        status, reason = 'negated', 'explicit_negative_status'
    elif status == 'no_distribution':
        if event.get('amount') is not None and item['amount'] != 0:
            status, reason = 'conflict', 'no_distribution_with_nonzero_or_invalid_amount'
        else:
            item['amount'] = Decimal(0)
    elif status not in {'implemented', 'announced'}:
        status, reason = 'missing', 'unknown_announcement_status'
    elif item['amount'] is None:
        status, reason = 'missing', 'missing_or_invalid_amount'
    if status in USABLE and event.get('amount_scope') != 'distribution':
        status, reason = 'ambiguous', 'distribution_amount_not_explicit_full_year_may_overlap_interim'
    if not item['period_valid']:
        status, reason = 'missing', 'invalid_fiscal_period'
    elif not isinstance(event.get('source'), dict) or not event['source']:
        status, reason = 'missing', 'missing_source_evidence'
    item.update(status=status, reason=reason)
    return item


def _published(value):
    """Compare dates only with dates, aware instants only with aware instants."""
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return ('date', datetime.strptime(value, '%Y-%m-%d').toordinal())
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            return ('instant', parsed.astimezone(timezone.utc).timestamp())
    except ValueError:
        pass
    return None


def _select(candidates):
    """Resolve latest version inside one (stock, fiscal year, kind) group."""
    pool = candidates
    ordered = len(pool) == 1
    revisions = [e.get('revision') for e in pool]
    dates = [_published(e.get('published_at')) for e in pool]
    dated = [(e, d) for e, d in zip(pool, dates) if d is not None]
    if all(type(v) is int and v >= 0 for v in revisions):
        pool = [e for e in pool if e['revision'] == max(revisions)]
        ordered = True
    elif len(dated) == len(pool) and len({d[0] for _, d in dated}) == 1:
        latest = max(d[1] for _, d in dated)
        pool = [e for e, d in dated if d[1] == latest]
        ordered = True
    signatures = {(e['status'], e['amount'], e.get('amount_scope'), e.get('reason')) for e in pool}
    selected = deepcopy(pool[0])
    selected['evidence'] = [deepcopy(e['raw']) for e in pool]
    if len(signatures) > 1:
        selected.update(status='conflict' if ordered else 'ambiguous', amount=None,
                        reason='conflicting_latest_version' if ordered else 'version_order_unknown')
    return selected


def calculate_forward_basis(events, codes=()):
    """Return per-stock Decimal amounts and source-preserving components."""
    events = [_normalize(e) for e in events]
    result = {}
    for code in dict.fromkeys([*codes, *(e['code'] for e in events)]):
        audit = [e for e in events if e['code'] == code]
        components = {}
        for kind in ('annual', 'interim'):
            candidates = [e for e in audit if e['period_valid'] and e['kind'] == kind]
            latest_year = max((e['year'] for e in candidates), default=None)
            components[kind] = _select([e for e in candidates if e['year'] == latest_year]) if candidates else {
                'code': code, 'kind': kind, 'year': None, 'amount': None,
                'status': 'missing', 'reason': 'no_period_evidence', 'source': None}
        issues = [e for e in audit if not e['period_valid']]
        failures = {e['status'] for e in [*components.values(), *issues]} - USABLE
        status = next((s for s in ('conflict', 'ambiguous', 'negated', 'missing') if s in failures), 'ready')
        result[code] = {'amount': sum((e['amount'] for e in components.values()), Decimal(0)) if status == 'ready' else None,
                        'status': status, 'components': components, 'audit': audit}
    return result
