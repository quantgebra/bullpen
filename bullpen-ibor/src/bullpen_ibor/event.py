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

    known_at_datetime is when the IBOR learned of the change. Unlike the securities master's knowledge ranges it is an
    instant: an event is never superseded, only offset.

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
