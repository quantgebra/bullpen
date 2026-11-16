from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.as_of as as_of_module
import bullpen_sec_master.base as base
import bullpen_sec_master.exchange as exchange_module
import bullpen_sec_master.settlement_type as settlement_types
import bullpen_sec_master.trading_calendar as trading_calendar_module

FUTURES_ROOT_ID_SEQ: sqlalchemy.Sequence = sqlalchemy.Sequence("futures_root_id_seq", metadata=base.Base.metadata)


# ======================================================================================================================
class FuturesRoot(base.Base):
    """A product such as CL or ES (not an instrument; mini/micro are separate roots). Bi-temporal, stable key futures_root_id."""

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "futures_roots"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("futures_root_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="futures_roots_no_overlap",
            using="gist",
        ),
    )

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def create(
        session: orm.Session,
        effective_from_date: date,
        known_at_datetime: datetime,
        exchange: exchange_module.Exchange,
        name: str,
        currency_code: str,
        multiplier: Decimal,
        tick_size: Decimal,
        settlement_type: settlement_types.SettlementType,
        trading_calendar: Optional[trading_calendar_module.TradingCalendar] = None,
    ) -> "FuturesRoot":
        """A new root with a new futures_root_id; trading_calendar overrides the exchange's calendar when given."""
        created_row: FuturesRoot = FuturesRoot(
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            exchange_id=exchange.id,
            name=name,
            currency_code=currency_code,
            multiplier=multiplier,
            tick_size=tick_size,
            settlement_type=settlement_type,
            trading_calendar_id=trading_calendar.id if trading_calendar is not None else None,
        )
        session.add(created_row)
        session.flush()
        return created_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_as_of(session: orm.Session, futures_root_id: int, as_of: as_of_module.AsOf) -> Optional["FuturesRoot"]:
        select_statement: sqlalchemy.Select = sqlalchemy.select(FuturesRoot).where(
            FuturesRoot.futures_root_id == futures_root_id,
            FuturesRoot.effective_range.contains(as_of.effective_date),
            FuturesRoot.knowledge_range.contains(as_of.known_at_datetime),
        )
        result: Optional[FuturesRoot] = session.scalars(
            select_statement, execution_options={"identity_token": as_of}
        ).first()
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_by_name_as_of(session: orm.Session, name: str, as_of: as_of_module.AsOf) -> Optional["FuturesRoot"]:
        """Look a root up by its canonical name (e.g. CL)."""
        select_statement: sqlalchemy.Select = sqlalchemy.select(FuturesRoot).where(
            FuturesRoot.name == name,
            FuturesRoot.effective_range.contains(as_of.effective_date),
            FuturesRoot.knowledge_range.contains(as_of.known_at_datetime),
        )
        result: Optional[FuturesRoot] = session.scalars(
            select_statement, execution_options={"identity_token": as_of}
        ).first()
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _close_believed_rows(
        session: orm.Session, futures_root_id: int, effective_from_date: date, known_at_datetime: datetime
    ) -> list["FuturesRoot"]:
        """Close the knowledge_range of every believed row from effective_from_date onward; keep their earlier part."""
        open_ended_date_range: postgresql.Range[date] = postgresql.Range(effective_from_date, None, bounds="[)")
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(FuturesRoot)
            .where(
                FuturesRoot.futures_root_id == futures_root_id,
                sqlalchemy.func.upper_inf(FuturesRoot.knowledge_range),
                FuturesRoot.effective_range.overlaps(open_ended_date_range),
            )
            .order_by(sqlalchemy.func.lower(FuturesRoot.effective_range))
        )
        closed_rows: list[FuturesRoot] = list(session.scalars(select_statement).all())
        for closed_row in closed_rows:
            old_effective_range: postgresql.Range[date] = closed_row.effective_range
            closed_row.knowledge_range = postgresql.Range(
                closed_row.knowledge_range.lower, known_at_datetime, bounds="[)"
            )
            session.flush()
            if old_effective_range.lower < effective_from_date:
                session.add(
                    closed_row._copy_with_ranges(
                        postgresql.Range(old_effective_range.lower, effective_from_date, bounds="[)"),
                        postgresql.Range(known_at_datetime, None, bounds="[)"),
                    )
                )
        return closed_rows

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    row_id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    futures_root_id: orm.Mapped[int] = orm.mapped_column(server_default=FUTURES_ROOT_ID_SEQ.next_value(), index=True)
    effective_range: orm.Mapped[postgresql.Range[date]] = orm.mapped_column(postgresql.DATERANGE)
    knowledge_range: orm.Mapped[postgresql.Range[datetime]] = orm.mapped_column(postgresql.TSTZRANGE)
    exchange_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.exchanges.id"))
    name: orm.Mapped[str]
    currency_code: orm.Mapped[str] = orm.mapped_column(sqlalchemy.String(3))
    multiplier: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    tick_size: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    settlement_type: orm.Mapped[settlement_types.SettlementType] = orm.mapped_column(
        sqlalchemy.Enum(settlement_types.SettlementType, name="settlement_type")
    )
    trading_calendar_id: orm.Mapped[Optional[int]] = orm.mapped_column(
        sqlalchemy.ForeignKey("sec_master.trading_calendars.id")
    )
    command_id: orm.Mapped[Optional[int]]

    # ------------------------------------------------------------------------------------------------------------------
    def get_trading_calendar(self, session: orm.Session) -> Optional[trading_calendar_module.TradingCalendar]:
        """The root's own trading calendar if it has one, otherwise its exchange's."""
        if self.trading_calendar_id is not None:
            result: Optional[trading_calendar_module.TradingCalendar] = session.get(
                trading_calendar_module.TradingCalendar, self.trading_calendar_id
            )
        else:
            root_exchange: exchange_module.Exchange = session.get(exchange_module.Exchange, self.exchange_id)
            result = root_exchange.get_trading_calendar(session)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def get_tick_value(self) -> Decimal:
        """Derived, never stored, so it cannot drift from tick_size and multiplier."""
        result: Decimal = self.tick_size * self.multiplier
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_tick_size(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, tick_size: Decimal
    ) -> "FuturesRoot":
        """From effective_from_date onward the root has a new tick size; self becomes a superseded belief."""
        self._supersede_from(session, effective_from_date, known_at_datetime)
        replacement_row: FuturesRoot = self._copy_with_ranges(
            postgresql.Range(effective_from_date, None, bounds="[)"),
            postgresql.Range(known_at_datetime, None, bounds="[)"),
        )
        replacement_row.command_id = None
        replacement_row.tick_size = tick_size
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def _supersede_from(self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime) -> None:
        """Only the latest believed row can be the basis of a change, otherwise later facts would be lost silently."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"futures root {self.futures_root_id} row {self.row_id} is no longer believed")
        if self.effective_range.upper is not None:
            raise ValueError(f"futures root {self.futures_root_id} has later believed rows; change the latest one")
        if self.effective_range.lower > effective_from_date:
            raise ValueError(f"futures root {self.futures_root_id} has earlier believed rows; change the earliest one")
        FuturesRoot._close_believed_rows(session, self.futures_root_id, effective_from_date, known_at_datetime)

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "FuturesRoot":
        result: FuturesRoot = FuturesRoot(
            futures_root_id=self.futures_root_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            exchange_id=self.exchange_id,
            name=self.name,
            currency_code=self.currency_code,
            multiplier=self.multiplier,
            tick_size=self.tick_size,
            settlement_type=self.settlement_type,
            trading_calendar_id=self.trading_calendar_id,
            command_id=self.command_id,
        )
        return result
