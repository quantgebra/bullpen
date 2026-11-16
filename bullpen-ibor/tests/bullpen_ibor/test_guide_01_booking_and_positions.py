"""
User guide, part 1: booking trades and reading positions.

The IBOR is an append-only LEDGER of EVENTS. Each event is a signed change to a position, and a position is the sum of
events; nothing else is stored. Every event belongs to a BOOK, the (fund, mandate, account) triple: which fund owns it,
which mandate's research it serves, which broker account holds it. The triple is stored as it is, not as a book id. A
(fund, mandate) pair is a SLEEVE. Each event is keyed further by two instruments:
  - the PORTFOLIO security: what the mandate thinks it holds (an equity, or a synthetic such as ALPHA's CL),
  - the TRADED security: what was actually bought (the same equity, or the CL contract of the moment).
Cash is an instrument too, so a buy is two events: plus shares, minus dollars.

Every event names its SOURCE, the version of the business document that caused it (here, a trade row), and has a
known_at_datetime. Positions are read at a position date and a known-at time, in one of two VIEWS: TRADED counts an
event from its trade date, SETTLED from its settlement date.

The IBOR refers to funds, mandates and accounts by fund-master id, and to instruments by securities-master id, so the
ids below are just numbers. Checking that a book fits (its sleeve exists, its account is its fund's) is the command
handler's job, against the fund master, before it books anything.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy

import bullpen_ibor.event as event_module
import bullpen_ibor.event_type as event_types
import bullpen_ibor.position as position
import bullpen_ibor.position_view as position_views
import bullpen_ibor.source_type as source_types
import bullpen_ibor.trade as trade

from .conftest import (
    ALPHA_CL_SYNTHETIC_INSTRUMENT_ID,
    ALPHA_MANDATE_ID,
    APPLE_INSTRUMENT_ID,
    BETA_MANDATE_ID,
    CLZ6_INSTRUMENT_ID,
    GS_ACCOUNT_ID,
    MAIN_FUND_ID,
    MICROSOFT_INSTRUMENT_ID,
    MS_ACCOUNT_ID,
    USD_CASH_INSTRUMENT_ID,
)

TRADE_DATE: date = date(2026, 11, 10)
SETTLEMENT_DATE: date = date(2026, 11, 11)
EXECUTED_DATETIME: datetime = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
BOOKED_DATETIME: datetime = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
NOW_DATETIME: datetime = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
TRADED: position_views.PositionView = position_views.PositionView.TRADED
SETTLED: position_views.PositionView = position_views.PositionView.SETTLED


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def apple_buy_trade(session) -> trade.Trade:
    """ALPHA buys 100 Apple at $250, settling T+1: $25,000 leaves the account on settlement."""
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
def test_01_the_book_is_stored_on_the_trade_and_on_every_event(session, apple_buy_trade) -> None:
    """There is no book object: the triple (fund, mandate, account) is stored as it is, on the trade and its events."""
    trade_events: list[event_module.Event] = apple_buy_trade.get_events(session)

    assert (apple_buy_trade.fund_id, apple_buy_trade.mandate_id, apple_buy_trade.account_id) == (
        MAIN_FUND_ID,
        ALPHA_MANDATE_ID,
        GS_ACCOUNT_ID,
    )
    assert {(event.fund_id, event.mandate_id, event.account_id) for event in trade_events} == {
        (MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID)
    }


# ----------------------------------------------------------------------------------------------------------------------
def test_02_booking_a_trade_appends_events_that_point_at_it(session, apple_buy_trade) -> None:
    """
    Booking records the trade and appends two events: +100 Apple and -25,000 USD. Both name the trade row as their
    source, and both carry the trade's dates and the time it was booked.
    """
    trade_events: list[event_module.Event] = apple_buy_trade.get_events(session)

    assert [event.event_type for event in trade_events] == [
        event_types.EventType.TRADE_QUANTITY,
        event_types.EventType.TRADE_CASH,
    ]
    assert [event.delta_quantity for event in trade_events] == [Decimal(100), Decimal(-25000)]
    assert {event.source_type for event in trade_events} == {source_types.SourceType.TRADE}
    assert {event.source_row_id for event in trade_events} == {apple_buy_trade.row_id}
    assert {event.known_at_datetime for event in trade_events} == {BOOKED_DATETIME}


# ----------------------------------------------------------------------------------------------------------------------
def test_03_the_traded_view_counts_a_trade_from_its_trade_date(session, apple_buy_trade) -> None:
    """On the trade date ALPHA holds 100 Apple and owes $25,000; the day before it holds nothing."""
    trade_date_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, TRADE_DATE, NOW_DATETIME, TRADED
    )
    day_before_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, date(2026, 11, 9), NOW_DATETIME, TRADED
    )

    assert trade_date_positions == [
        position.Position(USD_CASH_INSTRUMENT_ID, USD_CASH_INSTRUMENT_ID, Decimal(-25000)),
        position.Position(APPLE_INSTRUMENT_ID, APPLE_INSTRUMENT_ID, Decimal(100)),
    ]
    assert day_before_positions == []


# ----------------------------------------------------------------------------------------------------------------------
def test_04_the_settled_view_waits_for_the_settlement_date(session, apple_buy_trade) -> None:
    """The custodian's view: nothing has moved on the trade date; shares and cash both move on the settlement date."""
    trade_date_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, TRADE_DATE, NOW_DATETIME, SETTLED
    )
    settlement_date_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, SETTLEMENT_DATE, NOW_DATETIME, SETTLED
    )

    assert trade_date_positions == []
    assert len(settlement_date_positions) == 2


# ----------------------------------------------------------------------------------------------------------------------
def test_05_selling_everything_leaves_no_position(session, apple_buy_trade) -> None:
    """ALPHA sells the 100 Apple at $260 two days later. Apple nets to zero and disappears; the cash is up $1,000."""
    trade.Trade.book_trade(
        session,
        datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc),
        MAIN_FUND_ID,
        ALPHA_MANDATE_ID,
        GS_ACCOUNT_ID,
        portfolio_instrument_id=APPLE_INSTRUMENT_ID,
        traded_instrument_id=APPLE_INSTRUMENT_ID,
        quantity=Decimal(-100),
        price=Decimal(260),
        cash_instrument_id=USD_CASH_INSTRUMENT_ID,
        cash_amount=Decimal(26000),
        executed_at_datetime=datetime(2026, 11, 12, 14, 30, tzinfo=timezone.utc),
        trade_date=date(2026, 11, 12),
        settlement_date=date(2026, 11, 13),
    )

    after_sale_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, date(2026, 11, 12), NOW_DATETIME, TRADED
    )

    assert after_sale_positions == [position.Position(USD_CASH_INSTRUMENT_ID, USD_CASH_INSTRUMENT_ID, Decimal(1000))]


# ----------------------------------------------------------------------------------------------------------------------
def test_06_a_late_trade_is_invisible_to_earlier_runs(session, apple_buy_trade) -> None:
    """
    A Microsoft trade done on the trade date reaches us only the next morning. A run made on the evening of the trade
    date did not see it, and asked again with that run's known-at time it still does not.
    """
    evening_run_datetime: datetime = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)
    trade.Trade.book_trade(
        session,
        datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc),
        MAIN_FUND_ID,
        ALPHA_MANDATE_ID,
        GS_ACCOUNT_ID,
        portfolio_instrument_id=MICROSOFT_INSTRUMENT_ID,
        traded_instrument_id=MICROSOFT_INSTRUMENT_ID,
        quantity=Decimal(10),
        price=Decimal(500),
        cash_instrument_id=USD_CASH_INSTRUMENT_ID,
        cash_amount=Decimal(-5000),
        executed_at_datetime=EXECUTED_DATETIME,
        trade_date=TRADE_DATE,
        settlement_date=SETTLEMENT_DATE,
    )

    evening_run_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, TRADE_DATE, evening_run_datetime, TRADED
    )
    now_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, TRADE_DATE, NOW_DATETIME, TRADED
    )

    assert position.Position(MICROSOFT_INSTRUMENT_ID, MICROSOFT_INSTRUMENT_ID, Decimal(10)) not in evening_run_positions
    assert position.Position(MICROSOFT_INSTRUMENT_ID, MICROSOFT_INSTRUMENT_ID, Decimal(10)) in now_positions


# ----------------------------------------------------------------------------------------------------------------------
def test_07_a_futures_trade_is_attributed_to_the_mandates_synthetic(session) -> None:
    """
    ALPHA's research holds "CL", which is ALPHA's CL synthetic; the contract bought is CLZ6. The position is keyed by
    both. A futures trade moves no cash on the trade date (cash_amount is zero), so it books only the quantity event.
    """
    clz6_trade: trade.Trade = trade.Trade.book_trade(
        session,
        BOOKED_DATETIME,
        MAIN_FUND_ID,
        ALPHA_MANDATE_ID,
        GS_ACCOUNT_ID,
        portfolio_instrument_id=ALPHA_CL_SYNTHETIC_INSTRUMENT_ID,
        traded_instrument_id=CLZ6_INSTRUMENT_ID,
        quantity=Decimal(5),
        price=Decimal("71.38"),
        cash_instrument_id=USD_CASH_INSTRUMENT_ID,
        cash_amount=Decimal(0),
        executed_at_datetime=EXECUTED_DATETIME,
        trade_date=TRADE_DATE,
        settlement_date=TRADE_DATE,
    )

    alpha_positions: list[position.Position] = position.get_book_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, GS_ACCOUNT_ID, TRADE_DATE, NOW_DATETIME, TRADED
    )

    assert len(clz6_trade.get_events(session)) == 1
    assert alpha_positions == [position.Position(ALPHA_CL_SYNTHETIC_INSTRUMENT_ID, CLZ6_INSTRUMENT_ID, Decimal(5))]


# ----------------------------------------------------------------------------------------------------------------------
def test_08_positions_roll_up_by_sleeve_by_account_and_by_fund(session) -> None:
    """
    In MAIN, ALPHA buys 100 Apple through GS and 50 through MS; BETA buys 30 through GS. The sleeve view sums ALPHA's
    accounts, the account view (what GS reports) sums GS's sleeves, and the fund view sums everything.
    """
    for mandate_id, account_id, apple_quantity in [
        (ALPHA_MANDATE_ID, GS_ACCOUNT_ID, 100),
        (ALPHA_MANDATE_ID, MS_ACCOUNT_ID, 50),
        (BETA_MANDATE_ID, GS_ACCOUNT_ID, 30),
    ]:
        trade.Trade.book_trade(
            session,
            BOOKED_DATETIME,
            MAIN_FUND_ID,
            mandate_id,
            account_id,
            portfolio_instrument_id=APPLE_INSTRUMENT_ID,
            traded_instrument_id=APPLE_INSTRUMENT_ID,
            quantity=Decimal(apple_quantity),
            price=Decimal(250),
            cash_instrument_id=USD_CASH_INSTRUMENT_ID,
            cash_amount=Decimal(-250 * apple_quantity),
            executed_at_datetime=EXECUTED_DATETIME,
            trade_date=TRADE_DATE,
            settlement_date=SETTLEMENT_DATE,
        )

    alpha_positions: list[position.Position] = position.get_sleeve_positions(
        session, MAIN_FUND_ID, ALPHA_MANDATE_ID, TRADE_DATE, NOW_DATETIME, TRADED
    )
    gs_positions: list[position.TradedPosition] = position.get_account_positions(
        session, GS_ACCOUNT_ID, TRADE_DATE, NOW_DATETIME, TRADED
    )
    fund_positions: list[position.TradedPosition] = position.get_fund_positions(
        session, MAIN_FUND_ID, TRADE_DATE, NOW_DATETIME, TRADED
    )

    assert position.Position(APPLE_INSTRUMENT_ID, APPLE_INSTRUMENT_ID, Decimal(150)) in alpha_positions
    assert position.TradedPosition(APPLE_INSTRUMENT_ID, Decimal(130)) in gs_positions
    assert fund_positions == [
        position.TradedPosition(USD_CASH_INSTRUMENT_ID, Decimal(-45000)),
        position.TradedPosition(APPLE_INSTRUMENT_ID, Decimal(180)),
    ]


# ----------------------------------------------------------------------------------------------------------------------
def test_09_a_zero_quantity_trade_is_refused(session) -> None:
    """A zero-quantity trade carries no information and is refused by the database."""
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        trade.Trade.book_trade(
            session,
            BOOKED_DATETIME,
            MAIN_FUND_ID,
            ALPHA_MANDATE_ID,
            GS_ACCOUNT_ID,
            portfolio_instrument_id=APPLE_INSTRUMENT_ID,
            traded_instrument_id=APPLE_INSTRUMENT_ID,
            quantity=Decimal(0),
            price=Decimal(250),
            cash_instrument_id=USD_CASH_INSTRUMENT_ID,
            cash_amount=Decimal(0),
            executed_at_datetime=EXECUTED_DATETIME,
            trade_date=TRADE_DATE,
            settlement_date=SETTLEMENT_DATE,
        )
