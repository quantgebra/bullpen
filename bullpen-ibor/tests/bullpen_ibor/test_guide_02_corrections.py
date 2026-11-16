"""
User guide, part 2: corrections.

Brokers amend trades (a wrong price, a wrong quantity) and bust them, and we learn of it after the fact. The ledger
never edits or deletes an event. Instead:
  - the trade is VERSIONED: the old version stops being believed and, for an amendment, a new version is recorded under
    the same trade_id;
  - every event the old version caused is REVERSED by a new event with the opposite quantity (reverses_event_id points
    at the original), learned at the correction time;
  - the new version books its own events.
So positions RESTATE from the correction on, while a run made before it, asked again with its own known-at time, gets
exactly the answer it got then. What changed between two runs is the set of events learned between them.

Every event also carries effective_datetime, the instant it happened (for a trade, its execution time), which orders
events within a day; trade_date is the session it belongs to.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import bullpen_ibor.event as event_module
import bullpen_ibor.position as position
import bullpen_ibor.position_view as position_views
import bullpen_ibor.trade as trade

from .conftest import (
    ALPHA_CL_SYNTHETIC_INSTRUMENT_ID,
    ALPHA_MANDATE_ID,
    APPLE_INSTRUMENT_ID,
    CLZ6_INSTRUMENT_ID,
    GS_ACCOUNT_ID,
    MAIN_FUND_ID,
    MS_ACCOUNT_ID,
    USD_CASH_INSTRUMENT_ID,
)

TRADE_DATE: date = date(2026, 11, 10)
SETTLEMENT_DATE: date = date(2026, 11, 11)
EXECUTED_DATETIME: datetime = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
BOOKED_DATETIME: datetime = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
EVENING_RUN_DATETIME: datetime = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)
CORRECTED_DATETIME: datetime = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
NOW_DATETIME: datetime = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
TRADED: position_views.PositionView = position_views.PositionView.TRADED


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def apple_buy_trade(session) -> trade.Trade:
    """ALPHA buys 100 Apple at $250 through GS, executed at 14:30 and booked at 15:00."""
    result: trade.Trade = trade.Trade.book_trade(
        session,
        BOOKED_DATETIME,
        MAIN_FUND_ID,
        ALPHA_MANDATE_ID,
        GS_ACCOUNT_ID,
        portfolio_instrument_id=APPLE_INSTRUMENT_ID,
        traded_instrument_id=APPLE_INSTRUMENT_ID,
        quantity=Decimal(100),
        price=Decimal(250),
        cash_instrument_id=USD_CASH_INSTRUMENT_ID,
        cash_amount=Decimal(-25000),
        executed_at_datetime=EXECUTED_DATETIME,
        trade_date=TRADE_DATE,
        settlement_date=SETTLEMENT_DATE,
        external_ref="GS-EXEC-0001",
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def test_01_an_amendment_reverses_the_old_events_and_books_new_ones(session, apple_buy_trade) -> None:
    """
    GS corrects the price to $251 the next morning. The old version's two events are each reversed; the new version,
    under the same trade_id, books two new events.
    """
    amended_trade: trade.Trade = apple_buy_trade.with_new_terms(
        session,
        CORRECTED_DATETIME,
        quantity=Decimal(100),
        price=Decimal(251),
        cash_amount=Decimal(-25100),
        executed_at_datetime=EXECUTED_DATETIME,
        trade_date=TRADE_DATE,
        settlement_date=SETTLEMENT_DATE,
    )

    old_version_events: list[event_module.Event] = apple_buy_trade.get_events(session)
    new_version_events: list[event_module.Event] = amended_trade.get_events(session)

    assert amended_trade.trade_id == apple_buy_trade.trade_id
    assert [event.delta_quantity for event in old_version_events] == [
        Decimal(100),
        Decimal(-25000),
        Decimal(-100),
        Decimal(25000),
    ]
    assert [event.reverses_event_id for event in old_version_events[2:]] == [
        old_version_events[0].event_id,
        old_version_events[1].event_id,
    ]
    assert [event.delta_quantity for event in new_version_events] == [Decimal(100), Decimal(-25100)]


# ----------------------------------------------------------------------------------------------------------------------
def test_02_positions_restate_but_an_earlier_run_is_reproducible(session, apple_buy_trade) -> None:
    """
    The quantity was really 90. Asked now, ALPHA holds 90 Apple and owes $22,500; asked with the evening run's known-at
    time, it still holds 100 and owes $25,000, exactly as that run saw it.
    """
    apple_buy_trade.with_new_terms(
        session,
        CORRECTED_DATETIME,
        quantity=Decimal(90),
        price=Decimal(250),
        cash_amount=Decimal(-22500),
        executed_at_datetime=EXECUTED_DATETIME,
        trade_date=TRADE_DATE,
        settlement_date=SETTLEMENT_DATE,
    )

    evening_run_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, TRADE_DATE, EVENING_RUN_DATETIME, TRADED
    )
    now_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, TRADE_DATE, NOW_DATETIME, TRADED
    )

    assert evening_run_positions == [
        position.Position(USD_CASH_INSTRUMENT_ID, USD_CASH_INSTRUMENT_ID, Decimal(-25000)),
        position.Position(APPLE_INSTRUMENT_ID, APPLE_INSTRUMENT_ID, Decimal(100)),
    ]
    assert now_positions == [
        position.Position(USD_CASH_INSTRUMENT_ID, USD_CASH_INSTRUMENT_ID, Decimal(-22500)),
        position.Position(APPLE_INSTRUMENT_ID, APPLE_INSTRUMENT_ID, Decimal(90)),
    ]


# ----------------------------------------------------------------------------------------------------------------------
def test_03_each_known_at_time_sees_the_version_believed_then(session, apple_buy_trade) -> None:
    """find_as_of returns the trade's terms as believed at a time: the original before the amendment, the new after."""
    apple_buy_trade.with_new_terms(
        session,
        CORRECTED_DATETIME,
        quantity=Decimal(90),
        price=Decimal(250),
        cash_amount=Decimal(-22500),
        executed_at_datetime=EXECUTED_DATETIME,
        trade_date=TRADE_DATE,
        settlement_date=SETTLEMENT_DATE,
    )

    assert trade.Trade.find_as_of(session, apple_buy_trade.trade_id, EVENING_RUN_DATETIME).quantity == Decimal(100)
    assert trade.Trade.find_as_of(session, apple_buy_trade.trade_id, NOW_DATETIME).quantity == Decimal(90)


# ----------------------------------------------------------------------------------------------------------------------
def test_04_the_ledger_explains_what_changed_between_two_runs(session, apple_buy_trade) -> None:
    """
    The events learned between the evening run and now are the restatement: two reversals of the old version and two
    events of the new one. They net to the difference: 10 fewer Apple and $2,500 more cash.
    """
    amended_trade: trade.Trade = apple_buy_trade.with_new_terms(
        session,
        CORRECTED_DATETIME,
        quantity=Decimal(90),
        price=Decimal(250),
        cash_amount=Decimal(-22500),
        executed_at_datetime=EXECUTED_DATETIME,
        trade_date=TRADE_DATE,
        settlement_date=SETTLEMENT_DATE,
    )

    learned_events: list[event_module.Event] = event_module.Event.find_learned_between(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, EVENING_RUN_DATETIME, NOW_DATETIME
    )
    net_change_by_instrument_id: dict[int, Decimal] = {}
    for learned_event in learned_events:
        old_change: Decimal = net_change_by_instrument_id.get(learned_event.traded_instrument_id, Decimal(0))
        net_change_by_instrument_id[learned_event.traded_instrument_id] = old_change + learned_event.delta_quantity

    assert len(learned_events) == 4
    assert {event.source_row_id for event in learned_events} == {apple_buy_trade.row_id, amended_trade.row_id}
    assert net_change_by_instrument_id == {APPLE_INSTRUMENT_ID: Decimal(-10), USD_CASH_INSTRUMENT_ID: Decimal(2500)}


# ----------------------------------------------------------------------------------------------------------------------
def test_05_a_cancelled_trade_nets_to_nothing(session, apple_buy_trade) -> None:
    """GS busts the trade. From then on it is not believed and ALPHA holds nothing; the evening run still saw it."""
    apple_buy_trade.cancel(session, CORRECTED_DATETIME)

    now_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, TRADE_DATE, NOW_DATETIME, TRADED
    )
    evening_run_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, TRADE_DATE, EVENING_RUN_DATETIME, TRADED
    )

    assert now_positions == []
    assert len(evening_run_positions) == 2
    assert trade.Trade.find_as_of(session, apple_buy_trade.trade_id, NOW_DATETIME) is None


# ----------------------------------------------------------------------------------------------------------------------
def test_06_moving_a_trade_to_another_account_is_a_cancel_and_a_new_trade(session, apple_buy_trade) -> None:
    """
    The block was allocated to the wrong account: it belongs in MS. An amendment keeps the book, so the GS trade is
    cancelled and a new trade is booked in MS.
    """
    apple_buy_trade.cancel(session, CORRECTED_DATETIME)
    trade.Trade.book_trade(
        session,
        CORRECTED_DATETIME,
        MAIN_FUND_ID,
        ALPHA_MANDATE_ID,
        MS_ACCOUNT_ID,
        portfolio_instrument_id=APPLE_INSTRUMENT_ID,
        traded_instrument_id=APPLE_INSTRUMENT_ID,
        quantity=Decimal(100),
        price=Decimal(250),
        cash_instrument_id=USD_CASH_INSTRUMENT_ID,
        cash_amount=Decimal(-25000),
        executed_at_datetime=EXECUTED_DATETIME,
        trade_date=TRADE_DATE,
        settlement_date=SETTLEMENT_DATE,
    )

    gs_positions: list[position.TradedPosition] = position.get_account_positions(
        session, GS_ACCOUNT_ID, TRADE_DATE, NOW_DATETIME, TRADED
    )
    ms_positions: list[position.TradedPosition] = position.get_account_positions(
        session, MS_ACCOUNT_ID, TRADE_DATE, NOW_DATETIME, TRADED
    )

    assert gs_positions == []
    assert position.TradedPosition(APPLE_INSTRUMENT_ID, Decimal(100)) in ms_positions


# ----------------------------------------------------------------------------------------------------------------------
def test_07_only_the_believed_version_can_change(session, apple_buy_trade) -> None:
    """
    After an amendment the old version is history: amending or cancelling it again would reverse its events twice, so
    it raises. A correction cannot be dated at or before the version was learned.
    """
    with pytest.raises(ValueError):
        apple_buy_trade.cancel(session, BOOKED_DATETIME)

    apple_buy_trade.cancel(session, CORRECTED_DATETIME)

    with pytest.raises(ValueError):
        apple_buy_trade.cancel(session, NOW_DATETIME)


# ----------------------------------------------------------------------------------------------------------------------
def test_08_execution_time_orders_events_within_a_day(session, apple_buy_trade) -> None:
    """
    A 10:00 sale of 40 Apple reaches us after the 14:30 buy was booked. By execution time the sale came first, and the
    ledger lists the day's events in that order, whatever order they were booked in.
    """
    trade.Trade.book_trade(
        session,
        datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc),
        MAIN_FUND_ID,
        ALPHA_MANDATE_ID,
        GS_ACCOUNT_ID,
        portfolio_instrument_id=APPLE_INSTRUMENT_ID,
        traded_instrument_id=APPLE_INSTRUMENT_ID,
        quantity=Decimal(-40),
        price=Decimal(249),
        cash_instrument_id=USD_CASH_INSTRUMENT_ID,
        cash_amount=Decimal(9960),
        executed_at_datetime=datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc),
        trade_date=TRADE_DATE,
        settlement_date=SETTLEMENT_DATE,
    )

    day_events: list[event_module.Event] = event_module.Event.find_learned_between(
        session,
        MAIN_FUND_ID,
        ALPHA_MANDATE_ID,
        GS_ACCOUNT_ID,
        datetime(2026, 11, 10, tzinfo=timezone.utc),
        NOW_DATETIME,
    )

    assert [event.delta_quantity for event in day_events] == [
        Decimal(-40),
        Decimal(9960),
        Decimal(100),
        Decimal(-25000),
    ]


# ----------------------------------------------------------------------------------------------------------------------
def test_09_an_evening_globex_trade_belongs_to_the_next_session(session) -> None:
    """
    ALPHA buys CLZ6 on Sunday 2026-11-15 at 17:30 Chicago time. That is Monday's Globex session, so the trade date is
    Monday while the execution time is on Sunday; each event carries both.
    """
    globex_trade: trade.Trade = trade.Trade.book_trade(
        session,
        datetime(2026, 11, 15, 23, 45, tzinfo=timezone.utc),
        MAIN_FUND_ID,
        ALPHA_MANDATE_ID,
        GS_ACCOUNT_ID,
        portfolio_instrument_id=ALPHA_CL_SYNTHETIC_INSTRUMENT_ID,
        traded_instrument_id=CLZ6_INSTRUMENT_ID,
        quantity=Decimal(2),
        price=Decimal("71.10"),
        cash_instrument_id=USD_CASH_INSTRUMENT_ID,
        cash_amount=Decimal(0),
        executed_at_datetime=datetime(2026, 11, 15, 23, 30, tzinfo=timezone.utc),
        trade_date=date(2026, 11, 16),
        settlement_date=date(2026, 11, 16),
    )

    globex_event: event_module.Event = globex_trade.get_events(session)[0]

    assert globex_event.effective_datetime.date() == date(2026, 11, 15)
    assert globex_event.trade_date == date(2026, 11, 16)
