"""
User guide, part 8: trading calendars.

A TRADING CALENDAR says when a market trades. It is a plain row holding the regular session times in the calendar's own
time zone and the regular weekly pattern (a weekmask, Monday first; Monday to Friday unless told otherwise). Nothing is
stored for an ordinary day. Only what breaks the pattern is stored, as an EXCEPTION for one date: a holiday, or a special
session with its own open and close (an early close, a late open).

Exceptions are about their own date, so they have no effective range, but they do have a knowledge range: exchanges
announce closures late, amend them and occasionally cancel them, and an order run must be reproducible with what we
believed when it ran. So every question takes known_at_datetime.

Exchanges have a default calendar; a futures root can override it. Dates below are illustrative.
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

import bullpen_sec_master.futures_root as futures_root
import bullpen_sec_master.settlement_type as settlement_types
import bullpen_sec_master.trading_calendar as trading_calendar

from .conftest import BEGIN_DATE, FIRST_KNOWN_DATETIME

CALENDAR_LOADED_DATETIME: datetime = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
NOW_DATETIME: datetime = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
FRIDAY_DATE: date = date(2026, 11, 13)
MONDAY_DATE: date = date(2026, 11, 16)
THANKSGIVING_DATE: date = date(2026, 11, 26)
DAY_BEFORE_THANKSGIVING_DATE: date = date(2026, 11, 25)
DAY_AFTER_THANKSGIVING_DATE: date = date(2026, 11, 27)


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def xnys_calendar(session) -> trading_calendar.TradingCalendar:
    """The NYSE: Monday to Friday, 09:30 to 16:00 New York time."""
    result: trading_calendar.TradingCalendar = trading_calendar.TradingCalendar.create(
        session, "XNYS", "America/New_York", time(9, 30), time(16, 0)
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def globex_energy_calendar(session) -> trading_calendar.TradingCalendar:
    """CME Globex energy: each session opens at 17:00 Chicago time on the previous calendar day and closes at 16:00."""
    result: trading_calendar.TradingCalendar = trading_calendar.TradingCalendar.create(
        session, "CME_GLOBEX_ENERGY", "America/Chicago", time(17, 0), time(16, 0), regular_open_day_offset=-1
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def test_01_the_weekly_pattern_decides_ordinary_days(session, xnys_calendar) -> None:
    """With no exceptions stored at all, weekdays trade and weekends do not; the day after a Friday is the Monday."""
    assert xnys_calendar.is_trading_day(session, FRIDAY_DATE, NOW_DATETIME)
    assert not xnys_calendar.is_trading_day(session, date(2026, 11, 14), NOW_DATETIME)  # Saturday
    assert xnys_calendar.get_next_trading_day(session, FRIDAY_DATE, NOW_DATETIME) == MONDAY_DATE
    assert xnys_calendar.get_previous_trading_day(session, MONDAY_DATE, NOW_DATETIME) == FRIDAY_DATE


# ----------------------------------------------------------------------------------------------------------------------
def test_02_a_holiday_is_skipped(session, xnys_calendar) -> None:
    """Record Thanksgiving as a holiday: it is not a trading day, and the day before is followed by the day after."""
    xnys_calendar.record_holiday(session, THANKSGIVING_DATE, CALENDAR_LOADED_DATETIME)

    assert not xnys_calendar.is_trading_day(session, THANKSGIVING_DATE, NOW_DATETIME)
    assert xnys_calendar.get_session(session, THANKSGIVING_DATE, NOW_DATETIME) is None
    assert (
        xnys_calendar.get_next_trading_day(session, DAY_BEFORE_THANKSGIVING_DATE, NOW_DATETIME)
        == DAY_AFTER_THANKSGIVING_DATE
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_03_sessions_are_in_the_calendars_own_time_zone(session, xnys_calendar) -> None:
    """
    get_session() returns the open and close as time-zone-aware datetimes. The zone handles daylight saving: 09:30 New
    York is 13:30 UTC in July and 14:30 UTC in November.
    """
    july_session = xnys_calendar.get_session(session, date(2026, 7, 1), NOW_DATETIME)
    november_session = xnys_calendar.get_session(session, MONDAY_DATE, NOW_DATETIME)

    assert july_session.open_datetime == datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc)
    assert november_session.open_datetime == datetime(2026, 11, 16, 14, 30, tzinfo=timezone.utc)
    assert november_session.close_datetime == datetime(2026, 11, 16, 21, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------------------------------------------------
def test_04_an_early_close_is_a_special_session(session, xnys_calendar) -> None:
    """The day after Thanksgiving closes at 13:00 New York time: a special session with its own times."""
    xnys_calendar.record_special_session(
        session,
        DAY_AFTER_THANKSGIVING_DATE,
        CALENDAR_LOADED_DATETIME,
        datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc),
    )

    early_close_session = xnys_calendar.get_session(session, DAY_AFTER_THANKSGIVING_DATE, NOW_DATETIME)

    assert xnys_calendar.is_trading_day(session, DAY_AFTER_THANKSGIVING_DATE, NOW_DATETIME)
    assert early_close_session.close_datetime == datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------------------------------------------------
def test_05_a_late_announced_closure_keeps_earlier_runs_reproducible(session, xnys_calendar) -> None:
    """
    An unscheduled closure (say, a national day of mourning on 2026-12-09) is announced on 2026-12-02. An order run on
    2026-12-01 believed 2026-12-09 was a trading day, and asked with its own known-at time it still does; asked now,
    the closure is there.
    """
    closure_date: date = date(2026, 12, 9)
    announced_datetime: datetime = datetime(2026, 12, 2, 15, 0, tzinfo=timezone.utc)
    order_run_datetime: datetime = datetime(2026, 12, 1, 22, 0, tzinfo=timezone.utc)
    later_datetime: datetime = datetime(2026, 12, 3, 12, 0, tzinfo=timezone.utc)
    xnys_calendar.record_holiday(session, closure_date, announced_datetime)

    assert xnys_calendar.is_trading_day(session, closure_date, order_run_datetime)
    assert not xnys_calendar.is_trading_day(session, closure_date, later_datetime)
    assert xnys_calendar.get_next_trading_day(session, date(2026, 12, 8), order_run_datetime) == closure_date
    assert xnys_calendar.get_next_trading_day(session, date(2026, 12, 8), later_datetime) == date(2026, 12, 10)


# ----------------------------------------------------------------------------------------------------------------------
def test_06_a_cancelled_holiday_goes_back_to_the_weekly_pattern(session, xnys_calendar) -> None:
    """remove_exception() stops believing the exception from a time on; before that time the holiday still holds."""
    cancelled_datetime: datetime = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    xnys_calendar.record_holiday(session, THANKSGIVING_DATE, CALENDAR_LOADED_DATETIME)

    xnys_calendar.remove_exception(session, THANKSGIVING_DATE, cancelled_datetime)

    assert not xnys_calendar.is_trading_day(session, THANKSGIVING_DATE, NOW_DATETIME)
    assert xnys_calendar.is_trading_day(session, THANKSGIVING_DATE, datetime(2026, 11, 21, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        xnys_calendar.remove_exception(session, THANKSGIVING_DATE, datetime(2026, 11, 22, tzinfo=timezone.utc))


# ----------------------------------------------------------------------------------------------------------------------
def test_07_a_globex_session_opens_the_evening_before(session, globex_energy_calendar) -> None:
    """Monday's Globex energy session opens on Sunday at 17:00 Chicago time and closes on Monday at 16:00."""
    monday_session = globex_energy_calendar.get_session(session, MONDAY_DATE, NOW_DATETIME)

    assert monday_session.open_datetime == datetime(2026, 11, 15, 23, 0, tzinfo=timezone.utc)
    assert monday_session.close_datetime == datetime(2026, 11, 16, 22, 0, tzinfo=timezone.utc)
    assert not globex_energy_calendar.is_trading_day(session, date(2026, 11, 15), NOW_DATETIME)  # Sunday


# ----------------------------------------------------------------------------------------------------------------------
def test_08_a_futures_root_uses_its_exchanges_calendar_unless_it_has_its_own(
    session, exchange_by_mic, futures_root_by_code, xnys_calendar, globex_energy_calendar
) -> None:
    """
    NYMEX's default calendar is Globex energy, so CL trades on it. A root created with its own calendar overrides the
    exchange's default.
    """
    exchange_by_mic["XNYM"].trading_calendar_id = globex_energy_calendar.id
    overriding_root = futures_root.FuturesRoot.create(
        session,
        BEGIN_DATE,
        FIRST_KNOWN_DATETIME,
        exchange=exchange_by_mic["XNYM"],
        name="XX",
        currency_code="USD",
        multiplier=Decimal(1),
        tick_size=Decimal("0.01"),
        settlement_type=settlement_types.SettlementType.CASH,
        trading_calendar=xnys_calendar,
    )

    assert futures_root_by_code["CL"].get_trading_calendar(session).code == "CME_GLOBEX_ENERGY"
    assert overriding_root.get_trading_calendar(session).code == "XNYS"
