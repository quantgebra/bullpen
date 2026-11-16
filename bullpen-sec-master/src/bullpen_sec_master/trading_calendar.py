import zoneinfo
from datetime import date, datetime, time, timedelta
from typing import NamedTuple, Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.base as base


# ======================================================================================================================
class TradingSession(NamedTuple):

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    open_datetime: datetime
    close_datetime: datetime


# ======================================================================================================================
class TradingCalendarException(base.Base):
    """
    An exception to a calendar's weekly pattern on one session_date: a holiday (is_open false) or a special session
    with its own open and close (an early close, a late open, a weekend session). The fact is about its own date, so
    there is no effective_range; knowledge_range records when we believed it, because closures are announced late,
    amended and cancelled. Only manipulated through its TradingCalendar.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "trading_calendar_exceptions"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("trading_calendar_id", "="),
            ("session_date", "="),
            ("knowledge_range", "&&"),
            name="trading_calendar_exceptions_no_overlap",
            using="gist",
        ),
        # a holiday has no times; a special session has both
        sqlalchemy.CheckConstraint(
            "(is_open AND open_datetime IS NOT NULL AND close_datetime IS NOT NULL)"
            " OR (NOT is_open AND open_datetime IS NULL AND close_datetime IS NULL)",
            name="trading_calendar_exceptions_times_match_is_open",
        ),
    )

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    row_id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    trading_calendar_id: orm.Mapped[int] = orm.mapped_column(
        sqlalchemy.ForeignKey("sec_master.trading_calendars.id"), index=True
    )
    session_date: orm.Mapped[date]
    knowledge_range: orm.Mapped[postgresql.Range[datetime]] = orm.mapped_column(postgresql.TSTZRANGE)
    is_open: orm.Mapped[bool]
    open_datetime: orm.Mapped[Optional[datetime]] = orm.mapped_column(sqlalchemy.DateTime(timezone=True))
    close_datetime: orm.Mapped[Optional[datetime]] = orm.mapped_column(sqlalchemy.DateTime(timezone=True))
    command_id: orm.Mapped[Optional[int]]


# ======================================================================================================================
class TradingCalendar(base.Base):
    """
    When a market trades, e.g. XNYS or CME Globex energy. A plain row with regular session times in its own time zone
    and the regular weekly pattern (weekmask, Monday first, weekdays by default). Only exceptions are stored: holidays
    and special sessions are TradingCalendarException rows; every other date follows the weekly pattern. A session may open on the previous calendar day (Globex opens Sunday evening for Monday's
    session): regular_open_day_offset is then -1.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "trading_calendars"

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def create(
        session: orm.Session,
        code: str,
        time_zone_name: str,
        regular_open_time: time,
        regular_close_time: time,
        weekmask: str = "1111100",
        regular_open_day_offset: int = 0,
    ) -> "TradingCalendar":
        if len(weekmask) != 7 or set(weekmask) - {"0", "1"} or "1" not in weekmask:
            raise ValueError(f"weekmask must be seven 0/1 characters, Monday first, with at least one 1: {weekmask!r}")
        if regular_open_day_offset not in (0, -1):
            raise ValueError(f"regular_open_day_offset must be 0 or -1: {regular_open_day_offset}")
        zoneinfo.ZoneInfo(time_zone_name)

        created_row: TradingCalendar = TradingCalendar(
            code=code,
            time_zone_name=time_zone_name,
            regular_open_time=regular_open_time,
            regular_close_time=regular_close_time,
            weekmask=weekmask,
            regular_open_day_offset=regular_open_day_offset,
        )
        session.add(created_row)
        session.flush()
        return created_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_by_code(session: orm.Session, code: str) -> Optional["TradingCalendar"]:
        select_statement: sqlalchemy.Select = sqlalchemy.select(TradingCalendar).where(TradingCalendar.code == code)
        result: Optional[TradingCalendar] = session.scalars(select_statement).first()
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    code: orm.Mapped[str] = orm.mapped_column(unique=True)
    time_zone_name: orm.Mapped[str]
    regular_open_time: orm.Mapped[time]
    regular_close_time: orm.Mapped[time]
    weekmask: orm.Mapped[str] = orm.mapped_column(sqlalchemy.String(7))
    regular_open_day_offset: orm.Mapped[int]

    # ------------------------------------------------------------------------------------------------------------------
    def record_holiday(self, session: orm.Session, holiday_date: date, known_at_datetime: datetime) -> None:
        """From known_at_datetime on, we believe the market is closed on holiday_date."""
        self._supersede_day(session, holiday_date, known_at_datetime)
        holiday_row: TradingCalendarException = TradingCalendarException(
            trading_calendar_id=self.id,
            session_date=holiday_date,
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            is_open=False,
        )
        session.add(holiday_row)
        session.flush()

    # ------------------------------------------------------------------------------------------------------------------
    def record_special_session(
        self,
        session: orm.Session,
        session_date: date,
        known_at_datetime: datetime,
        open_datetime: datetime,
        close_datetime: datetime,
    ) -> None:
        """From known_at_datetime on, we believe session_date trades from open_datetime to close_datetime."""
        if close_datetime <= open_datetime:
            raise ValueError(f"special session on {session_date} closes before it opens")

        self._supersede_day(session, session_date, known_at_datetime)
        special_session_row: TradingCalendarException = TradingCalendarException(
            trading_calendar_id=self.id,
            session_date=session_date,
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            is_open=True,
            open_datetime=open_datetime,
            close_datetime=close_datetime,
        )
        session.add(special_session_row)
        session.flush()

    # ------------------------------------------------------------------------------------------------------------------
    def remove_exception(self, session: orm.Session, session_date: date, known_at_datetime: datetime) -> None:
        """From known_at_datetime on, session_date follows the regular pattern again (e.g. a holiday was cancelled)."""
        if self._find_latest_exception_row(session, session_date) is None:
            raise ValueError(f"calendar {self.code} has no exception on {session_date} to remove")
        self._supersede_day(session, session_date, known_at_datetime)

    # ------------------------------------------------------------------------------------------------------------------
    def is_trading_day(self, session: orm.Session, session_date: date, known_at_datetime: datetime) -> bool:
        """Whether session_date has a session, as believed at known_at_datetime."""
        exception_row: Optional[TradingCalendarException] = self._find_believed_exception_row(
            session, session_date, known_at_datetime
        )
        if exception_row is not None:
            result: bool = exception_row.is_open
        else:
            result = self._is_weekmask_day(session_date)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def get_session(
        self, session: orm.Session, session_date: date, known_at_datetime: datetime
    ) -> Optional[TradingSession]:
        """The open and close of session_date as believed at known_at_datetime, or None if the market is closed."""
        result: Optional[TradingSession] = None
        exception_row: Optional[TradingCalendarException] = self._find_believed_exception_row(
            session, session_date, known_at_datetime
        )
        if exception_row is not None:
            if exception_row.is_open:
                result = TradingSession(exception_row.open_datetime, exception_row.close_datetime)
        elif self._is_weekmask_day(session_date):
            result = self._get_regular_session(session_date)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def get_next_trading_day(self, session: orm.Session, after_date: date, known_at_datetime: datetime) -> date:
        """The first trading day strictly after after_date, as believed at known_at_datetime."""
        result: date = self._step_to_trading_day(session, after_date, 1, known_at_datetime)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def get_previous_trading_day(self, session: orm.Session, before_date: date, known_at_datetime: datetime) -> date:
        """The last trading day strictly before before_date, as believed at known_at_datetime."""
        result: date = self._step_to_trading_day(session, before_date, -1, known_at_datetime)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _supersede_day(self, session: orm.Session, session_date: date, known_at_datetime: datetime) -> None:
        """Stop believing the current exception for session_date (if any) from known_at_datetime on."""
        latest_exception_row: Optional[TradingCalendarException] = self._find_latest_exception_row(
            session, session_date
        )
        if latest_exception_row is not None:
            latest_exception_row.knowledge_range = postgresql.Range(
                latest_exception_row.knowledge_range.lower, known_at_datetime, bounds="[)"
            )
            session.flush()

    # ------------------------------------------------------------------------------------------------------------------
    def _find_latest_exception_row(
        self, session: orm.Session, session_date: date
    ) -> Optional[TradingCalendarException]:
        """The exception for session_date that is believed now (its knowledge_range is still open)."""
        select_statement: sqlalchemy.Select = sqlalchemy.select(TradingCalendarException).where(
            TradingCalendarException.trading_calendar_id == self.id,
            TradingCalendarException.session_date == session_date,
            sqlalchemy.func.upper_inf(TradingCalendarException.knowledge_range),
        )
        result: Optional[TradingCalendarException] = session.scalars(select_statement).first()
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _find_believed_exception_row(
        self, session: orm.Session, session_date: date, known_at_datetime: datetime
    ) -> Optional[TradingCalendarException]:
        select_statement: sqlalchemy.Select = sqlalchemy.select(TradingCalendarException).where(
            TradingCalendarException.trading_calendar_id == self.id,
            TradingCalendarException.session_date == session_date,
            TradingCalendarException.knowledge_range.contains(known_at_datetime),
        )
        result: Optional[TradingCalendarException] = session.scalars(select_statement).first()
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _is_weekmask_day(self, session_date: date) -> bool:
        result: bool = self.weekmask[session_date.weekday()] == "1"
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _get_regular_session(self, session_date: date) -> TradingSession:
        """Regular times in the calendar's own zone, so daylight saving is handled by the zone, not by us."""
        calendar_zone: zoneinfo.ZoneInfo = zoneinfo.ZoneInfo(self.time_zone_name)
        open_date: date = session_date + timedelta(days=self.regular_open_day_offset)
        result: TradingSession = TradingSession(
            datetime.combine(open_date, self.regular_open_time, tzinfo=calendar_zone),
            datetime.combine(session_date, self.regular_close_time, tzinfo=calendar_zone),
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _step_to_trading_day(
        self, session: orm.Session, start_date: date, step_day_count: int, known_at_datetime: datetime
    ) -> date:
        candidate_date: date = start_date + timedelta(days=step_day_count)
        while not self.is_trading_day(session, candidate_date, known_at_datetime):
            candidate_date = candidate_date + timedelta(days=step_day_count)
        return candidate_date
