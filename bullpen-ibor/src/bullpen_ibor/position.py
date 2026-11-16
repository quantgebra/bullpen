from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple

import sqlalchemy
from sqlalchemy import orm

import bullpen_ibor.event as event_module
import bullpen_ibor.position_view as position_views


# ======================================================================================================================
class Position(NamedTuple):
    """What a book or a sleeve holds: quantity of traded_instrument_id, attributed to portfolio_instrument_id."""

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    portfolio_instrument_id: int
    traded_instrument_id: int
    quantity: Decimal


# ======================================================================================================================
class TradedPosition(NamedTuple):
    """What an account or fund holds of traded_instrument_id, summed over mandates and portfolio securities."""

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    traded_instrument_id: int
    quantity: Decimal


# ----------------------------------------------------------------------------------------------------------------------
def get_book_positions(
    session: orm.Session,
    fund_id: int,
    mandate_id: int,
    account_id: int,
    position_date: date,
    known_at_datetime: datetime,
    position_view: position_views.PositionView,
) -> list[Position]:
    """
    The non-zero positions of the book (fund_id, mandate_id, account_id) on position_date in the given view, from the
    events known at known_at_datetime.
    """
    select_statement: sqlalchemy.Select = _select_positions(position_date, known_at_datetime, position_view).where(
        event_module.Event.fund_id == fund_id,
        event_module.Event.mandate_id == mandate_id,
        event_module.Event.account_id == account_id,
    )
    result: list[Position] = _read_positions(session, select_statement)
    return result


# ----------------------------------------------------------------------------------------------------------------------
def get_sleeve_positions(
    session: orm.Session,
    fund_id: int,
    mandate_id: int,
    position_date: date,
    known_at_datetime: datetime,
    position_view: position_views.PositionView,
) -> list[Position]:
    """A sleeve's positions (a fund running a mandate) summed over the accounts it trades through."""
    select_statement: sqlalchemy.Select = _select_positions(position_date, known_at_datetime, position_view).where(
        event_module.Event.fund_id == fund_id, event_module.Event.mandate_id == mandate_id
    )
    result: list[Position] = _read_positions(session, select_statement)
    return result


# ----------------------------------------------------------------------------------------------------------------------
def get_account_positions(
    session: orm.Session,
    account_id: int,
    position_date: date,
    known_at_datetime: datetime,
    position_view: position_views.PositionView,
) -> list[TradedPosition]:
    """What the broker should show for the account: traded instruments summed over every mandate in it."""
    select_statement: sqlalchemy.Select = _select_traded_positions(
        position_date, known_at_datetime, position_view
    ).where(event_module.Event.account_id == account_id)
    result: list[TradedPosition] = _read_traded_positions(session, select_statement)
    return result


# ----------------------------------------------------------------------------------------------------------------------
def get_fund_positions(
    session: orm.Session,
    fund_id: int,
    position_date: date,
    known_at_datetime: datetime,
    position_view: position_views.PositionView,
) -> list[TradedPosition]:
    """The fund's traded instruments summed over every mandate and account."""
    select_statement: sqlalchemy.Select = _select_traded_positions(
        position_date, known_at_datetime, position_view
    ).where(event_module.Event.fund_id == fund_id)
    result: list[TradedPosition] = _read_traded_positions(session, select_statement)
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _select_positions(
    position_date: date, known_at_datetime: datetime, position_view: position_views.PositionView
) -> sqlalchemy.Select:
    """Sum of known events up to position_date, per (portfolio security, traded security), dropping flat ones."""
    quantity_sum: sqlalchemy.ColumnElement = sqlalchemy.func.sum(event_module.Event.delta_quantity)
    result: sqlalchemy.Select = (
        sqlalchemy.select(
            event_module.Event.portfolio_instrument_id, event_module.Event.traded_instrument_id, quantity_sum
        )
        .where(*_get_view_conditions(position_date, known_at_datetime, position_view))
        .group_by(event_module.Event.portfolio_instrument_id, event_module.Event.traded_instrument_id)
        .having(quantity_sum != 0)
        .order_by(event_module.Event.portfolio_instrument_id, event_module.Event.traded_instrument_id)
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _get_view_conditions(
    position_date: date, known_at_datetime: datetime, position_view: position_views.PositionView
) -> list[sqlalchemy.ColumnElement]:
    """Traded counts an event from its trade date, settled from its settlement date; both only once it was known."""
    if position_view is position_views.PositionView.TRADED:
        view_date_column: orm.InstrumentedAttribute = event_module.Event.trade_date
    else:
        view_date_column = event_module.Event.settlement_date
    result: list[sqlalchemy.ColumnElement] = [
        view_date_column <= position_date,
        event_module.Event.known_at_datetime <= known_at_datetime,
    ]
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _read_positions(session: orm.Session, select_statement: sqlalchemy.Select) -> list[Position]:
    result: list[Position] = []
    for row in session.execute(select_statement):
        position: Position = Position(row[0], row[1], row[2])
        result.append(position)
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _select_traded_positions(
    position_date: date, known_at_datetime: datetime, position_view: position_views.PositionView
) -> sqlalchemy.Select:
    """Sum of known events up to position_date, per traded security only, dropping flat ones."""
    quantity_sum: sqlalchemy.ColumnElement = sqlalchemy.func.sum(event_module.Event.delta_quantity)
    result: sqlalchemy.Select = (
        sqlalchemy.select(event_module.Event.traded_instrument_id, quantity_sum)
        .where(*_get_view_conditions(position_date, known_at_datetime, position_view))
        .group_by(event_module.Event.traded_instrument_id)
        .having(quantity_sum != 0)
        .order_by(event_module.Event.traded_instrument_id)
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _read_traded_positions(session: orm.Session, select_statement: sqlalchemy.Select) -> list[TradedPosition]:
    result: list[TradedPosition] = []
    for row in session.execute(select_statement):
        traded_position: TradedPosition = TradedPosition(row[0], row[1])
        result.append(traded_position)
    return result
