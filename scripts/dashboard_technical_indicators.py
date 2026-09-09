"""Pure, offline Dashboard BOLL(20,2); sample stddev (n-1).

Input is one security's already-adjusted daily closes on a consistent price
scale. No fetching, factor inference, file writes, or VPS strategy imports.
"""
from datetime import date
import math
import statistics


def _day(value):
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise ValueError('date_must_be_iso_day')
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('invalid_iso_day') from exc
    if value != day.isoformat():
        raise ValueError('date_must_be_iso_day')
    return day


def validate_daily_closes(rows, *, as_of, expected_trade_dates):
    """Reject disorder, duplicates, future days, nonpositive/nonfinite closes.

    Calendar is the exact expected per-security session set for the supplied
    window (exclude documented suspensions). None explicitly means unchecked;
    weekday inference, sorting, deduplication and forward-filling are forbidden.
    Returns new date/close dictionaries; never modifies input.
    """
    cutoff = _day(as_of)
    result = []
    previous = None
    for row in rows:
        if not isinstance(row, dict) or not {'date', 'close'} <= row.keys():
            raise ValueError('daily_row_shape_invalid')
        day = _day(row['date'])
        close = row['close']
        if type(close) not in (int, float) or not math.isfinite(close) or close <= 0:
            raise ValueError('close_must_be_positive_finite_number')
        if day > cutoff:
            raise ValueError('future_daily_bar')
        if previous is not None and day <= previous:
            raise ValueError('daily_dates_not_strictly_increasing')
        result.append({'date': day, 'close': float(close)})
        previous = day
    if expected_trade_dates is not None:
        expected = [_day(value) for value in expected_trade_dates]
        if any(day > cutoff for day in expected) or expected != sorted(set(expected)):
            raise ValueError('expected_calendar_invalid')
        if {row['date'] for row in result} != set(expected):
            raise ValueError('daily_calendar_coverage_mismatch')
    return result


def compute_boll(rows, *, timeframe, as_of, adjustment, include_current_period,
                 expected_trade_dates):
    """Return BOLL and audit metadata; never silently shorten the warmup.

    ``adjustment`` labels supplied prices; it does NOT perform adjustment.
    ``include_current_period=False`` excludes the calendar period containing
    as_of (even Friday/month-end); True uses only rows through as_of. Thus
    partial periods never see later closes. Input bars must already have the
    intended EOD/provisional status; this function never inserts live quotes.
    An explicit None calendar leaves continuity unverified. Numerical success
    is not supplier equivalence/PIT proof or permission to publish/trade.
    """
    if timeframe not in ('day', 'week', 'month'):
        raise ValueError('unsupported_timeframe')
    if adjustment not in ('none', 'forward', 'backward'):
        raise ValueError('unsupported_adjustment')
    if type(include_current_period) is not bool:
        raise ValueError('include_current_period_must_be_bool')
    rows = list(rows)
    for row in rows:
        if isinstance(row, dict) and 'adjustment' in row and row['adjustment'] != adjustment:
            raise ValueError('mixed_adjustment_basis')
    cutoff = _day(as_of)
    normalized = validate_daily_closes(rows, as_of=cutoff, expected_trade_dates=expected_trade_dates)
    def period_key(day):
        if timeframe == 'day':
            return day
        if timeframe == 'week':
            return day.isocalendar()[:2]
        return (day.year, day.month)
    current_key = period_key(cutoff)
    grouped = {}
    for row in normalized:
        key = period_key(row['date'])
        if not include_current_period and key == current_key:
            continue
        grouped[key] = (row['date'], row['close'])
    sample = list(grouped.values())[-20:]
    result = {
        'status': 'ok' if len(sample) == 20 else 'insufficient_history',
        'asOf': sample[-1][0].isoformat() if sample else None,
        'calculationAsOf': cutoff.isoformat(),
        'basis': {'none': '未复权', 'forward': '前复权', 'backward': '后复权'}[adjustment]
                 + {'day': '日K', 'week': '周K', 'month': '月K'}[timeframe],
        'timeframe': timeframe, 'adjustment': adjustment,
        'dashboardForwardBasis': adjustment == 'forward',
        'priceAdjustmentPerformed': False,
        'includeCurrentPeriod': include_current_period,
        'currentPeriodIncluded': current_key in grouped,
        'periodPolicy': 'through_as_of' if include_current_period else 'exclude_as_of_calendar_period',
        'calendarValidation': 'not_checked' if expected_trade_dates is None else 'exact_match',
        'period': 20, 'multiplier': 2, 'stddev': 'sample',
        'sampleCount': len(sample), 'availablePeriodCount': len(grouped),
        'middle': None, 'upper': None, 'lower': None,
    }
    if len(sample) < 20:
        return result
    values = [close for day, close in sample]
    middle = statistics.mean(values)
    sigma = statistics.stdev(values)
    result.update(middle=round(middle, 3), upper=round(middle + 2 * sigma, 3), lower=round(middle - 2 * sigma, 3))
    return result
