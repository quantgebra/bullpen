from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row
import bullpen_sec_master.base as base
import bullpen_sec_master.exchange as exchange_module
import bullpen_sec_master.settlement_type as settlement_types
import bullpen_sec_master.trading_calendar as trading_calendar_module

FUTURES_ROOT_ID_SEQ: sqlalchemy.Sequence = sqlalchemy.Sequence("futures_root_id_seq", metadata=base.Base.metadata)


# ======================================================================================================================
class FuturesRoot(bitemporal_row.BitemporalRow, base.Base):
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
        result: Optional[FuturesRoot] = FuturesRoot.find_one_as_of(
            session, [FuturesRoot.futures_root_id == futures_root_id], as_of
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_by_name_as_of(session: orm.Session, name: str, as_of: as_of_module.AsOf) -> Optional["FuturesRoot"]:
        """Look a root up by its canonical name (e.g. CL)."""
        result: Optional[FuturesRoot] = FuturesRoot.find_one_as_of(session, [FuturesRoot.name == name], as_of)
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    futures_root_id: orm.Mapped[int] = orm.mapped_column(server_default=FUTURES_ROOT_ID_SEQ.next_value(), index=True)
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
        replacement_row: FuturesRoot = self._create_replacement(session, effective_from_date, known_at_datetime)
        replacement_row.tick_size = tick_size
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        result: list[sqlalchemy.ColumnElement] = [FuturesRoot.futures_root_id == self.futures_root_id]
        return result

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
