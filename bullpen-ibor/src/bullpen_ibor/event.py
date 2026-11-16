from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy
from sqlalchemy import orm

import bullpen_ibor.base as base
import bullpen_ibor.event_type as event_types
import bullpen_ibor.source_type as source_types


# ======================================================================================================================
class Event(base.Base):
    """
    One signed change to a position: delta_quantity of traded_instrument_id, held in the book (fund_id, mandate_id,
    account_id) for portfolio_instrument_id. The ledger is append-only: rows are never updated or deleted, and a
    correction is a new event that reverses an old one. Every position is a sum of events. The book is the triple
    itself; its ids are fund-master stable keys, and the command handler checks that they fit (no cross-package FKs).

    Four times answer four questions. trade_date: which session the change belongs to (the traded view's effective
    date). settlement_date: when it settles (the settled view's). effective_datetime: the instant it happened (a
    trade's execution time), which orders events within a day. known_at_datetime: when the IBOR learned of it; unlike
    the securities master's knowledge ranges it is an instant, because an event is never superseded, only offset.

    source_type + source_row_id name the version of the business document that caused the event (a trade row, a
    corporate action row); command_id names the command that wrote it. Instrument ids are securities-master stable
    keys, referenced by id only.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "events"
    __table_args__ = (
        sqlalchemy.Index("events_source", "source_type", "source_row_id"),
        sqlalchemy.Index("events_sleeve", "fund_id", "mandate_id"),
        sqlalchemy.Index("events_account", "account_id"),
        sqlalchemy.CheckConstraint("delta_quantity <> 0", name="events_delta_quantity_nonzero"),
    )

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_learned_between(
        session: orm.Session,
        fund_id: int,
        mandate_id: int,
        account_id: int,
        after_datetime: datetime,
        until_datetime: datetime,
    ) -> list["Event"]:
        """
        The book's events learned in (after_datetime, until_datetime]: what changed between two runs, which is what a
        restatement report explains. Ordered as they happened, then as they were appended.
        """
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(Event)
            .where(
                Event.fund_id == fund_id,
                Event.mandate_id == mandate_id,
                Event.account_id == account_id,
                Event.known_at_datetime > after_datetime,
                Event.known_at_datetime <= until_datetime,
            )
            .order_by(Event.effective_datetime, Event.event_id)
        )
        result: list[Event] = list(session.scalars(select_statement).all())
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    event_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.BigInteger, sqlalchemy.Identity(), primary_key=True)
    fund_id: orm.Mapped[int]  # fund-master stable keys, by id only: the command handler checks that they fit
    mandate_id: orm.Mapped[int]
    account_id: orm.Mapped[int]
    portfolio_instrument_id: orm.Mapped[int]
    traded_instrument_id: orm.Mapped[int]
    event_type: orm.Mapped[event_types.EventType] = orm.mapped_column(
        sqlalchemy.Enum(event_types.EventType, name="event_type")
    )
    delta_quantity: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    trade_date: orm.Mapped[date]
    settlement_date: orm.Mapped[date]
    effective_datetime: orm.Mapped[datetime] = orm.mapped_column(sqlalchemy.DateTime(timezone=True))
    known_at_datetime: orm.Mapped[datetime] = orm.mapped_column(sqlalchemy.DateTime(timezone=True))
    source_type: orm.Mapped[source_types.SourceType] = orm.mapped_column(
        sqlalchemy.Enum(source_types.SourceType, name="source_type")
    )
    source_row_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.BigInteger)
    # an event is reversed at most once
    reverses_event_id: orm.Mapped[Optional[int]] = orm.mapped_column(
        sqlalchemy.BigInteger, sqlalchemy.ForeignKey("ibor.events.event_id"), unique=True
    )
    command_id: orm.Mapped[Optional[int]]

    # ------------------------------------------------------------------------------------------------------------------
    def build_reversal(self, known_at_datetime: datetime, command_id: Optional[int]) -> "Event":
        """
        An unsaved event that offsets this one exactly: same book, instruments, type, dates and source, opposite
        quantity, learned at known_at_datetime. From then on the two sum to nothing on every date and in every view.
        """
        if self.reverses_event_id is not None:
            raise ValueError(f"event {self.event_id} is itself a reversal; book a new event instead")

        result: Event = Event(
            fund_id=self.fund_id,
            mandate_id=self.mandate_id,
            account_id=self.account_id,
            portfolio_instrument_id=self.portfolio_instrument_id,
            traded_instrument_id=self.traded_instrument_id,
            event_type=self.event_type,
            delta_quantity=-self.delta_quantity,
            trade_date=self.trade_date,
            settlement_date=self.settlement_date,
            effective_datetime=self.effective_datetime,
            known_at_datetime=known_at_datetime,
            source_type=self.source_type,
            source_row_id=self.source_row_id,
            reverses_event_id=self.event_id,
            command_id=command_id,
        )
        return result
