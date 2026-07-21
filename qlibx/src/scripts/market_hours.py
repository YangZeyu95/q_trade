"""US equity regular-session clock shared by the trading engine and API."""

from datetime import date, datetime, timedelta

import pytz


MARKET_TIMEZONE_NAME = "America/New_York"
MARKET_TZ = pytz.timezone(MARKET_TIMEZONE_NAME)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0


def _nth_weekday(year, month, weekday, occurrence):
    """Return the Nth weekday in a month (Monday is 0)."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year, month, weekday):
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    current = next_month - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def _observed(fixed_holiday):
    if fixed_holiday.weekday() == 5:
        return fixed_holiday - timedelta(days=1)
    if fixed_holiday.weekday() == 6:
        return fixed_holiday + timedelta(days=1)
    return fixed_holiday


def _easter_sunday(year):
    """Gregorian Easter date, used to derive the Good Friday closure."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_offset) // 451
    month = (h + weekday_offset - 7 * m + 114) // 31
    day = (h + weekday_offset - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _market_holiday_name(day):
    """Return the regular US equity-market holiday name, if any."""
    year = day.year
    holidays = {
        _observed(date(year, 1, 1)): "new_year",
        _nth_weekday(year, 1, 0, 3): "martin_luther_king_jr_day",
        _nth_weekday(year, 2, 0, 3): "washington_birthday",
        _easter_sunday(year) - timedelta(days=2): "good_friday",
        _last_weekday(year, 5, 0): "memorial_day",
        _observed(date(year, 7, 4)): "independence_day",
        _nth_weekday(year, 9, 0, 1): "labor_day",
        _nth_weekday(year, 11, 3, 4): "thanksgiving",
        _observed(date(year, 12, 25)): "christmas",
        # A Saturday New Year's Day is observed on Dec 31 of the prior year.
        _observed(date(year + 1, 1, 1)): "new_year",
    }
    if year >= 2022:
        holidays[_observed(date(year, 6, 19))] = "juneteenth"
    return holidays.get(day)


def _is_standard_early_close(day):
    """Return whether the regular US equity session normally closes at 13:00."""
    year = day.year
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {
        thanksgiving + timedelta(days=1),
        date(year, 7, 3),
        date(year, 12, 24),
    }
    return (
        day in candidates
        and day.weekday() < 5
        and _market_holiday_name(day) is None
    )


def _as_market_time(now=None):
    if now is None:
        return datetime.now(MARKET_TZ)
    if now.tzinfo is None:
        return MARKET_TZ.localize(now)
    return now.astimezone(MARKET_TZ)


def get_market_status(now=None):
    """Return the current US regular-session status.

    This intentionally models weekdays and the 09:30-16:00 ET regular
    session only. Extended-hours trading is not enabled here.
    """
    now_et = _as_market_time(now)
    market_open = now_et.replace(
        hour=MARKET_OPEN_HOUR,
        minute=MARKET_OPEN_MINUTE,
        second=0,
        microsecond=0,
    )
    early_close = _is_standard_early_close(now_et.date())
    market_close = now_et.replace(
        hour=13 if early_close else MARKET_CLOSE_HOUR,
        minute=MARKET_CLOSE_MINUTE,
        second=0,
        microsecond=0,
    )

    holiday_name = _market_holiday_name(now_et.date())
    if now_et.weekday() >= 5:
        reason = "weekend"
        is_open = False
    elif holiday_name:
        reason = "holiday"
        is_open = False
    elif now_et < market_open:
        reason = "before_open"
        is_open = False
    elif now_et >= market_close:
        reason = "after_close"
        is_open = False
    else:
        reason = "regular_session"
        is_open = True

    return {
        "is_open": is_open,
        "session": "regular" if is_open else "closed",
        "reason": reason,
        "holiday": holiday_name,
        "timezone": MARKET_TIMEZONE_NAME,
        "local_time": now_et.isoformat(),
        "market_open": market_open.isoformat(),
        "market_close": market_close.isoformat(),
        "early_close": early_close,
        "extended_hours_enabled": False,
    }
