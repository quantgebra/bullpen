"""
User guide, part 10: corporate actions.

A CORPORATE ACTION changes what holders of an instrument own: a dividend, a split, a spin-off, a merger. The security
master holds its definition; the IBOR applies it to positions. An action has:
  - dates: ex_date (who is entitled is fixed by holdings before it), record_date and pay_date;
  - is_held_surrendered: whether holders give up the held instrument (split, merger) or keep it (dividend, spin-off);
  - OUTCOMES, per unit held: cash (a currency and an amount), or securities (an instrument and a quantity, plus the
    share of the cost basis that moves to them).
The type is a label for people. The IBOR needs only the outcomes and is_held_surrendered, so it applies every type the
same way.

An action is about its own dates, so it has no effective range, but it has a knowledge range: actions are announced,
amended and cancelled, and an order run must see the terms that were believed when it ran. Dates and amounts are
illustrative.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import bullpen_sec_master.corporate_action as corporate_action
import bullpen_sec_master.corporate_action_type as corporate_action_types
import bullpen_sec_master.instrument_equity as instrument_equity

from .conftest import BEGIN_DATE, FIRST_KNOWN_DATETIME

ANNOUNCED_DATETIME: datetime = datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)
AMENDED_DATETIME: datetime = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc)
BEFORE_AMENDMENT_DATETIME: datetime = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
NOW_DATETIME: datetime = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
EX_DATE: date = date(2026, 11, 9)
RECORD_DATE: date = date(2026, 11, 10)
PAY_DATE: date = date(2026, 11, 13)
NOVEMBER_FIRST_DATE: date = date(2026, 11, 1)
DECEMBER_FIRST_DATE: date = date(2026, 12, 1)


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def equity_by_name(session) -> dict[str, instrument_equity.InstrumentEquity]:
    """Four US equities keyed by name: a dividend payer, a spinning-off parent, its spun-off company and an acquirer."""
    result: dict[str, instrument_equity.InstrumentEquity] = {}
    for equity_name in ["Apple", "Parent Co", "Spin Co", "Acquirer Co"]:
        result[equity_name] = instrument_equity.InstrumentEquity.create(
            session, BEGIN_DATE, FIRST_KNOWN_DATETIME, name=equity_name, currency_code="USD"
        )
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def apple_dividend_action(session, equity_by_name) -> corporate_action.CorporateAction:
    """Apple pays $0.26 a share; holders keep their shares."""
    result: corporate_action.CorporateAction = corporate_action.CorporateAction.create(
        session,
        ANNOUNCED_DATETIME,
        equity_by_name["Apple"],
        corporate_action_types.CorporateActionType.CASH_DIVIDEND,
        EX_DATE,
        RECORD_DATE,
        PAY_DATE,
        is_held_surrendered=False,
        outcomes=[corporate_action.CorporateActionOutcome.build_cash("USD", Decimal("0.26"))],
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def test_01_a_cash_dividend_pays_per_share_held(session, equity_by_name, apple_dividend_action) -> None:
    """
    The IBOR asks which actions go ex in a window, then multiplies each outcome by the position: 1,000 shares receive
    $260, and the shares stay.
    """
    november_actions: list[corporate_action.CorporateAction] = corporate_action.CorporateAction.find_going_ex(
        session, equity_by_name["Apple"], NOVEMBER_FIRST_DATE, DECEMBER_FIRST_DATE, NOW_DATETIME
    )
    dividend_outcome: corporate_action.CorporateActionOutcome = november_actions[0].get_outcomes(session)[0]

    assert november_actions == [apple_dividend_action]
    assert not november_actions[0].is_held_surrendered
    assert dividend_outcome.is_cash()
    assert dividend_outcome.cash_per_unit_held * Decimal(1000) == Decimal("260.00")


# ----------------------------------------------------------------------------------------------------------------------
def test_02_a_split_surrenders_the_shares_for_more_of_the_same(session, equity_by_name) -> None:
    """A 4-for-1 split: each share held is given up for 4 new ones, which carry the whole cost basis."""
    split_action: corporate_action.CorporateAction = corporate_action.CorporateAction.create(
        session,
        ANNOUNCED_DATETIME,
        equity_by_name["Apple"],
        corporate_action_types.CorporateActionType.STOCK_SPLIT,
        EX_DATE,
        None,
        None,
        is_held_surrendered=True,
        outcomes=[
            corporate_action.CorporateActionOutcome.build_securities(equity_by_name["Apple"], Decimal(4), Decimal(1))
        ],
    )

    split_outcome: corporate_action.CorporateActionOutcome = split_action.get_outcomes(session)[0]

    assert split_action.is_held_surrendered
    assert split_outcome.instrument_id == equity_by_name["Apple"].instrument_id
    assert split_outcome.quantity_per_unit_held * Decimal(250) == Decimal(1000)


# ----------------------------------------------------------------------------------------------------------------------
def test_03_a_spin_offs_cost_basis_split_arrives_later(session, equity_by_name) -> None:
    """
    Parent Co spins off half a share of Spin Co per share held. How the cost basis divides between the two is published
    weeks later; until then it is None. Adding it is a new belief, so a run made before still sees None.
    """
    spin_off_action: corporate_action.CorporateAction = corporate_action.CorporateAction.create(
        session,
        ANNOUNCED_DATETIME,
        equity_by_name["Parent Co"],
        corporate_action_types.CorporateActionType.SPIN_OFF,
        EX_DATE,
        RECORD_DATE,
        PAY_DATE,
        is_held_surrendered=False,
        outcomes=[corporate_action.CorporateActionOutcome.build_securities(equity_by_name["Spin Co"], Decimal("0.5"))],
    )

    spin_off_action.with_new_outcomes(
        session,
        AMENDED_DATETIME,
        [
            corporate_action.CorporateActionOutcome.build_securities(
                equity_by_name["Spin Co"], Decimal("0.5"), Decimal("0.18")
            )
        ],
    )

    before_action: corporate_action.CorporateAction = corporate_action.CorporateAction.find_as_of(
        session, spin_off_action.corporate_action_id, BEFORE_AMENDMENT_DATETIME
    )
    now_action: corporate_action.CorporateAction = corporate_action.CorporateAction.find_as_of(
        session, spin_off_action.corporate_action_id, NOW_DATETIME
    )
    assert before_action.get_outcomes(session)[0].cost_basis_fraction is None
    assert now_action.get_outcomes(session)[0].cost_basis_fraction == Decimal("0.18")


# ----------------------------------------------------------------------------------------------------------------------
def test_04_a_merger_can_pay_cash_and_shares_together(session, equity_by_name) -> None:
    """Acquirer Co buys Parent Co for 0.5 Acquirer shares plus $10 per share: two outcomes; the Parent shares go."""
    merger_action: corporate_action.CorporateAction = corporate_action.CorporateAction.create(
        session,
        ANNOUNCED_DATETIME,
        equity_by_name["Parent Co"],
        corporate_action_types.CorporateActionType.MERGER,
        EX_DATE,
        None,
        PAY_DATE,
        is_held_surrendered=True,
        outcomes=[
            corporate_action.CorporateActionOutcome.build_securities(equity_by_name["Acquirer Co"], Decimal("0.5")),
            corporate_action.CorporateActionOutcome.build_cash("USD", Decimal(10)),
        ],
    )

    merger_outcomes: list[corporate_action.CorporateActionOutcome] = merger_action.get_outcomes(session)

    assert merger_action.is_held_surrendered
    assert merger_outcomes[0].instrument_id == equity_by_name["Acquirer Co"].instrument_id
    assert merger_outcomes[1].cash_per_unit_held == Decimal(10)


# ----------------------------------------------------------------------------------------------------------------------
def test_05_an_amended_ex_date_moves_the_action_from_then_on(session, equity_by_name, apple_dividend_action) -> None:
    """
    The dividend's ex-date moves into December. Asked before the amendment, it goes ex in November; asked now, in
    December. Before it was announced, it does not exist.
    """
    apple_dividend_action.with_new_dates(
        session, AMENDED_DATETIME, date(2026, 12, 7), date(2026, 12, 8), date(2026, 12, 11)
    )

    november_before_amendment_actions: list[corporate_action.CorporateAction] = (
        corporate_action.CorporateAction.find_going_ex(
            session, equity_by_name["Apple"], NOVEMBER_FIRST_DATE, DECEMBER_FIRST_DATE, BEFORE_AMENDMENT_DATETIME
        )
    )
    november_now_actions: list[corporate_action.CorporateAction] = corporate_action.CorporateAction.find_going_ex(
        session, equity_by_name["Apple"], NOVEMBER_FIRST_DATE, DECEMBER_FIRST_DATE, NOW_DATETIME
    )
    december_now_actions: list[corporate_action.CorporateAction] = corporate_action.CorporateAction.find_going_ex(
        session, equity_by_name["Apple"], DECEMBER_FIRST_DATE, date(2027, 1, 1), NOW_DATETIME
    )
    before_announcement_actions: list[corporate_action.CorporateAction] = (
        corporate_action.CorporateAction.find_going_ex(
            session, equity_by_name["Apple"], NOVEMBER_FIRST_DATE, DECEMBER_FIRST_DATE, FIRST_KNOWN_DATETIME
        )
    )

    assert len(november_before_amendment_actions) == 1
    assert november_now_actions == []
    assert december_now_actions[0].get_outcomes(session)[0].cash_per_unit_held == Decimal("0.26")
    assert before_announcement_actions == []


# ----------------------------------------------------------------------------------------------------------------------
def test_06_a_cancelled_action_is_gone_from_then_on(session, apple_dividend_action) -> None:
    """Cancelling stops believing the action; a run made before the cancellation still sees it."""
    apple_dividend_action.cancel(session, AMENDED_DATETIME)

    assert (
        corporate_action.CorporateAction.find_as_of(
            session, apple_dividend_action.corporate_action_id, BEFORE_AMENDMENT_DATETIME
        )
        is not None
    )
    assert (
        corporate_action.CorporateAction.find_as_of(session, apple_dividend_action.corporate_action_id, NOW_DATETIME)
        is None
    )
    with pytest.raises(ValueError):
        apple_dividend_action.cancel(session, NOW_DATETIME)
