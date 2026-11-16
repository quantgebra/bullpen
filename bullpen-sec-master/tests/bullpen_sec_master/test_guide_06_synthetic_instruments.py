"""
User guide, part 6: synthetic instruments.

Research holds things no broker can: VIX30 (a constant 30-day maturity VIX future), a calendar spread, a straddle. Each
is a SYNTHETIC INSTRUMENT: an instrument with its own stable instrument_id whose LEGS are real, traded instruments with
weights. In the IBOR it is the portfolio security; its legs are the traded securities.

The legs are bi-temporal and stored as received from the external system that computes them; bullpen does no maturity
interpolation and no roll scheduling. Two shapes use the same class:
  - dynamic: VIX30's legs and weights change every day, so research sends a new leg set per date;
  - static: a calendar spread's legs are fixed when it is created and never change.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import bullpen_sec_master.as_of as as_of_module
import bullpen_sec_master.exchange as exchange
import bullpen_sec_master.futures_root as futures_root
import bullpen_sec_master.instrument as instrument
import bullpen_sec_master.instrument_futures_contract as instrument_futures_contract
import bullpen_sec_master.instrument_synthetic as instrument_synthetic
import bullpen_sec_master.settlement_type as settlement_types

from .conftest import BEGIN_DATE, FIRST_KNOWN_DATETIME

FIRST_DAY_DATE: date = date(2026, 11, 9)
SECOND_DAY_DATE: date = date(2026, 11, 10)
THIRD_DAY_DATE: date = date(2026, 11, 11)
FIRST_DAY_SENT_DATETIME: datetime = datetime(2026, 11, 6, 22, 0, tzinfo=timezone.utc)
SECOND_DAY_SENT_DATETIME: datetime = datetime(2026, 11, 9, 22, 0, tzinfo=timezone.utc)
THIRD_DAY_SENT_DATETIME: datetime = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)
CORRECTION_SENT_DATETIME: datetime = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
NOW_DATETIME: datetime = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def vx_future_contract_by_code(session) -> dict[str, instrument_futures_contract.InstrumentFuturesContract]:
    """The VIX futures root on CFE and its November and December 2026 contracts, keyed by contract code."""
    cfe_exchange: exchange.Exchange = exchange.Exchange(mic="XCBF", name="Cboe Futures Exchange")
    session.add(cfe_exchange)
    session.flush()
    vx_futures_root: futures_root.FuturesRoot = futures_root.FuturesRoot.create(
        session,
        BEGIN_DATE,
        FIRST_KNOWN_DATETIME,
        exchange=cfe_exchange,
        name="VX",
        currency_code="USD",
        multiplier=Decimal(1000),
        tick_size=Decimal("0.05"),
        settlement_type=settlement_types.SettlementType.CASH,
    )

    result: dict[str, instrument_futures_contract.InstrumentFuturesContract] = {}
    for contract_code, contract_month, expiry_date in [
        ("VXX6", date(2026, 11, 1), date(2026, 11, 18)),
        ("VXZ6", date(2026, 12, 1), date(2026, 12, 16)),
    ]:
        result[contract_code] = instrument_futures_contract.InstrumentFuturesContract.create(
            session,
            date(2026, 1, 1),
            FIRST_KNOWN_DATETIME,
            name=f"VIX {contract_code}",
            currency_code="USD",
            futures_root=vx_futures_root,
            contract_month=contract_month,
            last_trade_date=expiry_date,
            expiry_date=expiry_date,
        )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _get_weight_by_instrument_id(synthetic, session, as_of) -> dict[int, Decimal]:
    """The synthetic's legs at as_of, as {leg instrument_id: weight}: read the synthetic at as_of, then its legs."""
    synthetic_as_of = instrument.Instrument.find_as_of(session, synthetic.instrument_id, as_of)
    result: dict[int, Decimal] = {}
    for leg in synthetic_as_of.get_legs(session):
        result[leg.instrument.instrument_id] = leg.weight
    return result


# ----------------------------------------------------------------------------------------------------------------------
def test_01_vix30_is_re_weighted_every_day(session, vx_future_contract_by_code) -> None:
    """
    Each evening research sends the next day's VIX30 weights. We record each day's leg set from that day onward; the
    next day's set supersedes it from the next day. Every past day keeps its own weights.
    """
    vxx6_contract = vx_future_contract_by_code["VXX6"]
    vxz6_contract = vx_future_contract_by_code["VXZ6"]
    vix30_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        FIRST_DAY_DATE,
        FIRST_DAY_SENT_DATETIME,
        name="VIX30",
        currency_code="USD",
        legs=[
            instrument_synthetic.LegWeight(vxx6_contract, Decimal("0.25")),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal("0.75")),
        ],
    )

    vix30_synthetic.with_new_legs(
        session,
        SECOND_DAY_DATE,
        SECOND_DAY_SENT_DATETIME,
        [
            instrument_synthetic.LegWeight(vxx6_contract, Decimal("0.20")),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal("0.80")),
        ],
    )
    vix30_synthetic.with_new_legs(
        session,
        THIRD_DAY_DATE,
        THIRD_DAY_SENT_DATETIME,
        [
            instrument_synthetic.LegWeight(vxx6_contract, Decimal("0.15")),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal("0.85")),
        ],
    )

    assert _get_weight_by_instrument_id(vix30_synthetic, session, as_of_module.AsOf(FIRST_DAY_DATE, NOW_DATETIME)) == {
        vxx6_contract.instrument_id: Decimal("0.25"),
        vxz6_contract.instrument_id: Decimal("0.75"),
    }
    assert _get_weight_by_instrument_id(vix30_synthetic, session, as_of_module.AsOf(SECOND_DAY_DATE, NOW_DATETIME)) == {
        vxx6_contract.instrument_id: Decimal("0.20"),
        vxz6_contract.instrument_id: Decimal("0.80"),
    }
    assert _get_weight_by_instrument_id(vix30_synthetic, session, as_of_module.AsOf(THIRD_DAY_DATE, NOW_DATETIME)) == {
        vxx6_contract.instrument_id: Decimal("0.15"),
        vxz6_contract.instrument_id: Decimal("0.85"),
    }


# ----------------------------------------------------------------------------------------------------------------------
def test_02_the_order_run_sees_the_weights_it_had(session, vx_future_contract_by_code) -> None:
    """
    Orders for the second day were generated right after that day's weights arrived. Asked later with that run's
    known-at time, VIX30 still decomposes into the weights the run used, even though the third day's set has arrived.
    """
    vxx6_contract = vx_future_contract_by_code["VXX6"]
    vxz6_contract = vx_future_contract_by_code["VXZ6"]
    vix30_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        FIRST_DAY_DATE,
        FIRST_DAY_SENT_DATETIME,
        name="VIX30",
        currency_code="USD",
        legs=[
            instrument_synthetic.LegWeight(vxx6_contract, Decimal("0.25")),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal("0.75")),
        ],
    )
    vix30_synthetic.with_new_legs(
        session,
        SECOND_DAY_DATE,
        SECOND_DAY_SENT_DATETIME,
        [
            instrument_synthetic.LegWeight(vxx6_contract, Decimal("0.20")),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal("0.80")),
        ],
    )
    order_run_datetime: datetime = datetime(2026, 11, 9, 22, 30, tzinfo=timezone.utc)
    vix30_synthetic.with_new_legs(
        session,
        THIRD_DAY_DATE,
        THIRD_DAY_SENT_DATETIME,
        [
            instrument_synthetic.LegWeight(vxx6_contract, Decimal("0.15")),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal("0.85")),
        ],
    )

    assert _get_weight_by_instrument_id(
        vix30_synthetic, session, as_of_module.AsOf(SECOND_DAY_DATE, order_run_datetime)
    ) == {
        vxx6_contract.instrument_id: Decimal("0.20"),
        vxz6_contract.instrument_id: Decimal("0.80"),
    }
    assert _get_weight_by_instrument_id(
        vix30_synthetic, session, as_of_module.AsOf(THIRD_DAY_DATE, order_run_datetime)
    ) == {
        vxx6_contract.instrument_id: Decimal("0.20"),
        vxz6_contract.instrument_id: Decimal("0.80"),
    }  # the third day's weights had not arrived yet, so the open-ended second-day set was believed to continue


# ----------------------------------------------------------------------------------------------------------------------
def test_03_correcting_one_past_day_leaves_the_other_days_alone(session, vx_future_contract_by_code) -> None:
    """
    Research resends the second day's weights with a fix. Restating only that one day (a bounded window) supersedes the
    second day's weights and nothing else; the run made before the fix still sees the original weights.
    """
    vxx6_contract = vx_future_contract_by_code["VXX6"]
    vxz6_contract = vx_future_contract_by_code["VXZ6"]
    vix30_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        FIRST_DAY_DATE,
        FIRST_DAY_SENT_DATETIME,
        name="VIX30",
        currency_code="USD",
        legs=[
            instrument_synthetic.LegWeight(vxx6_contract, Decimal("0.25")),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal("0.75")),
        ],
    )
    vix30_synthetic.with_new_legs(
        session,
        SECOND_DAY_DATE,
        SECOND_DAY_SENT_DATETIME,
        [
            instrument_synthetic.LegWeight(vxx6_contract, Decimal("0.20")),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal("0.80")),
        ],
    )
    vix30_synthetic.with_new_legs(
        session,
        THIRD_DAY_DATE,
        THIRD_DAY_SENT_DATETIME,
        [
            instrument_synthetic.LegWeight(vxx6_contract, Decimal("0.15")),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal("0.85")),
        ],
    )

    vix30_synthetic.with_new_legs(
        session,
        SECOND_DAY_DATE,
        CORRECTION_SENT_DATETIME,
        [
            instrument_synthetic.LegWeight(vxx6_contract, Decimal("0.21")),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal("0.79")),
        ],
        effective_to_date=THIRD_DAY_DATE,
    )

    assert _get_weight_by_instrument_id(vix30_synthetic, session, as_of_module.AsOf(FIRST_DAY_DATE, NOW_DATETIME)) == {
        vxx6_contract.instrument_id: Decimal("0.25"),
        vxz6_contract.instrument_id: Decimal("0.75"),
    }
    assert _get_weight_by_instrument_id(vix30_synthetic, session, as_of_module.AsOf(SECOND_DAY_DATE, NOW_DATETIME)) == {
        vxx6_contract.instrument_id: Decimal("0.21"),
        vxz6_contract.instrument_id: Decimal("0.79"),
    }
    assert _get_weight_by_instrument_id(vix30_synthetic, session, as_of_module.AsOf(THIRD_DAY_DATE, NOW_DATETIME)) == {
        vxx6_contract.instrument_id: Decimal("0.15"),
        vxz6_contract.instrument_id: Decimal("0.85"),
    }
    assert _get_weight_by_instrument_id(
        vix30_synthetic, session, as_of_module.AsOf(SECOND_DAY_DATE, THIRD_DAY_SENT_DATETIME)
    ) == {
        vxx6_contract.instrument_id: Decimal("0.20"),
        vxz6_contract.instrument_id: Decimal("0.80"),
    }


# ----------------------------------------------------------------------------------------------------------------------
def test_04_a_calendar_spread_has_fixed_legs_with_signed_weights(session, vx_future_contract_by_code) -> None:
    """
    Long December, short November VIX: a static synthetic. Weights are signed ratios; the legs are set at creation and
    hold for as long as the spread exists.
    """
    vxx6_contract = vx_future_contract_by_code["VXX6"]
    vxz6_contract = vx_future_contract_by_code["VXZ6"]
    calendar_spread_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        FIRST_DAY_DATE,
        FIRST_DAY_SENT_DATETIME,
        name="VX X6/Z6 calendar",
        currency_code="USD",
        legs=[
            instrument_synthetic.LegWeight(vxx6_contract, Decimal(-1)),
            instrument_synthetic.LegWeight(vxz6_contract, Decimal(1)),
        ],
    )

    calendar_spread_as_of = instrument.Instrument.find_as_of(
        session, calendar_spread_synthetic.instrument_id, as_of_module.AsOf(THIRD_DAY_DATE, NOW_DATETIME)
    )
    legs = calendar_spread_as_of.get_legs(session)

    assert [leg.instrument.name for leg in legs] == ["VIX VXX6", "VIX VXZ6"]
    assert [leg.weight for leg in legs] == [Decimal(-1), Decimal(1)]
    assert legs[0].instrument.expiry_date == date(2026, 11, 18)


# ----------------------------------------------------------------------------------------------------------------------
def test_05_a_synthetic_cannot_be_a_leg(session, vx_future_contract_by_code) -> None:
    """
    Legs are what gets traded, so they are never synthetic: decomposing a portfolio security into traded securities is
    always exactly one step. A spread of VIX30 against something else is built from VIX30's legs, not from VIX30.
    """
    vix30_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        FIRST_DAY_DATE,
        FIRST_DAY_SENT_DATETIME,
        name="VIX30",
        currency_code="USD",
        legs=[instrument_synthetic.LegWeight(vx_future_contract_by_code["VXZ6"], Decimal(1))],
    )

    with pytest.raises(ValueError):
        instrument_synthetic.InstrumentSynthetic.create(
            session,
            FIRST_DAY_DATE,
            FIRST_DAY_SENT_DATETIME,
            name="VIX30 vs VXZ6",
            currency_code="USD",
            legs=[
                instrument_synthetic.LegWeight(vix30_synthetic, Decimal(1)),
                instrument_synthetic.LegWeight(vx_future_contract_by_code["VXZ6"], Decimal(-1)),
            ],
        )
