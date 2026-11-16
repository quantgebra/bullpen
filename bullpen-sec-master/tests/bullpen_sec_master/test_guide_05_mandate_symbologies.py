"""
User guide, part 5: mandate symbologies ("research names").

A research system sends weights keyed by its own names, such as "CL". Each mandate gets its own SYMBOLOGY, and in it the
name "CL" points at a SYNTHETIC INSTRUMENT that belongs to that mandate (ALPHA's CL). The synthetic is the portfolio
security: what the IBOR attributes positions to. Its one leg is the contract actually traded, with weight 1.

So a name and its contract are two separate facts, both bi-temporal and both stored as received from an external system:
  - the name "CL" means ALPHA's CL synthetic (stable; changes only if research renames things),
  - ALPHA's CL synthetic holds the June contract (changes on every roll).
Nothing here knows about rolling. A roll is just the external system telling us that, from some date, the synthetic
holds a different contract. Because the name stays on the same synthetic, the IBOR sees one continuous position in
ALPHA's CL whose traded security changes, not two unrelated positions.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import exc

import bullpen_temporal.as_of as as_of_module
import bullpen_sec_master.instrument_futures_contract as instrument_futures_contract
import bullpen_sec_master.instrument_synthetic as instrument_synthetic
import bullpen_sec_master.symbology as symbology
import bullpen_sec_master.symbology_for_instrument as symbology_for_instrument

from .conftest import FIRST_KNOWN_DATETIME, SECOND_KNOWN_DATETIME, THIRD_KNOWN_DATETIME

MAPPED_FROM_DATE: date = date(2026, 2, 1)
MAPPING_LEARNED_DATETIME: datetime = SECOND_KNOWN_DATETIME
QUERY_DATE: date = date(2026, 3, 1)
QUERY_DATETIME: datetime = THIRD_KNOWN_DATETIME
ROLL_DATE: date = date(2026, 5, 15)
ROLL_LEARNED_DATETIME: datetime = datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
BEFORE_ROLL_DATE: date = date(2026, 5, 1)
AFTER_ROLL_DATE: date = date(2026, 6, 1)
AFTER_ROLL_LEARNED_DATETIME: datetime = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
CORRECTED_ROLL_DATE: date = date(2026, 5, 10)
BETWEEN_ROLL_DATES_DATE: date = date(2026, 5, 12)
CORRECTION_LEARNED_DATETIME: datetime = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
AFTER_CORRECTION_DATETIME: datetime = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def clm6_future_contract(session, futures_root_by_code) -> instrument_futures_contract.InstrumentFuturesContract:
    """The CL June 2026 contract (the December one is the shared clz6_future_contract fixture)."""
    result: instrument_futures_contract.InstrumentFuturesContract = (
        instrument_futures_contract.InstrumentFuturesContract.create(
            session,
            date(2025, 6, 1),
            FIRST_KNOWN_DATETIME,
            name="Crude Oil Jun 2026",
            currency_code="USD",
            futures_root=futures_root_by_code["CL"],
            contract_month=date(2026, 6, 1),
            last_trade_date=date(2026, 5, 20),
            expiry_date=date(2026, 5, 20),
        )
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _get_contract_id(session, mandate_symbology, as_of) -> int:
    """
    The contract a mandate trades for its research name "CL": resolve the name, then the synthetic's one leg. The
    synthetic came back from an as-of query, so get_legs() reads its legs at that same AsOf without being told.
    """
    mandate_synthetic = symbology_for_instrument.resolve_target(session, mandate_symbology, "CL", as_of)
    legs = mandate_synthetic.get_legs(session)
    assert len(legs) == 1
    assert legs[0].weight == Decimal(1)
    result: int = legs[0].instrument.instrument_id
    return result


# ----------------------------------------------------------------------------------------------------------------------
def test_01_two_mandates_map_the_same_name_to_different_contracts(
    session, clm6_future_contract, clz6_future_contract
) -> None:
    """
    In ALPHA "CL" is ALPHA's CL synthetic, which holds the June contract; in BETA it is BETA's CL synthetic, which holds
    December. Same name, same date, different portfolio securities and different traded contracts.
    """
    alpha_symbology = symbology.Symbology.create(session, "ALPHA")
    beta_symbology = symbology.Symbology.create(session, "BETA")
    alpha_cl_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        MAPPED_FROM_DATE,
        MAPPING_LEARNED_DATETIME,
        name="ALPHA CL",
        currency_code="USD",
        legs=[instrument_synthetic.LegWeight(clm6_future_contract, Decimal(1))],
    )
    beta_cl_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        MAPPED_FROM_DATE,
        MAPPING_LEARNED_DATETIME,
        name="BETA CL",
        currency_code="USD",
        legs=[instrument_synthetic.LegWeight(clz6_future_contract, Decimal(1))],
    )
    symbology_for_instrument.create_symbol(
        session, alpha_symbology, MAPPED_FROM_DATE, MAPPING_LEARNED_DATETIME, alpha_cl_synthetic, "CL"
    )
    symbology_for_instrument.create_symbol(
        session, beta_symbology, MAPPED_FROM_DATE, MAPPING_LEARNED_DATETIME, beta_cl_synthetic, "CL"
    )

    alpha_portfolio_security = symbology_for_instrument.resolve_target(
        session, alpha_symbology, "CL", as_of_module.AsOf(QUERY_DATE, QUERY_DATETIME)
    )
    beta_portfolio_security = symbology_for_instrument.resolve_target(
        session, beta_symbology, "CL", as_of_module.AsOf(QUERY_DATE, QUERY_DATETIME)
    )

    assert alpha_portfolio_security.instrument_id == alpha_cl_synthetic.instrument_id
    assert beta_portfolio_security.instrument_id == beta_cl_synthetic.instrument_id
    assert (
        _get_contract_id(session, alpha_symbology, as_of_module.AsOf(QUERY_DATE, QUERY_DATETIME))
        == clm6_future_contract.instrument_id
    )
    assert (
        _get_contract_id(session, beta_symbology, as_of_module.AsOf(QUERY_DATE, QUERY_DATETIME))
        == clz6_future_contract.instrument_id
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_02_the_reverse_question_is_per_mandate_too(session, clm6_future_contract) -> None:
    """What does ALPHA's research call this portfolio security? "CL". BETA does not name it at all."""
    alpha_symbology = symbology.Symbology.create(session, "ALPHA")
    beta_symbology = symbology.Symbology.create(session, "BETA")
    alpha_cl_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        MAPPED_FROM_DATE,
        MAPPING_LEARNED_DATETIME,
        name="ALPHA CL",
        currency_code="USD",
        legs=[instrument_synthetic.LegWeight(clm6_future_contract, Decimal(1))],
    )
    symbology_for_instrument.create_symbol(
        session, alpha_symbology, MAPPED_FROM_DATE, MAPPING_LEARNED_DATETIME, alpha_cl_synthetic, "CL"
    )

    assert (
        symbology_for_instrument.get_symbol(
            session, alpha_symbology, alpha_cl_synthetic, as_of_module.AsOf(QUERY_DATE, QUERY_DATETIME)
        )
        == "CL"
    )
    assert (
        symbology_for_instrument.get_symbol(
            session, beta_symbology, alpha_cl_synthetic, as_of_module.AsOf(QUERY_DATE, QUERY_DATETIME)
        )
        is None
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_03_a_mandate_rolls_on_its_own_schedule(session, clm6_future_contract, clz6_future_contract) -> None:
    """
    On 2026-04-20 the external system tells us ALPHA's CL holds December instead of June from 2026-05-15. The name is
    untouched: only the synthetic's leg changes, so the portfolio security is the same before and after the roll. BETA
    is unaffected. A run made before we learned of the roll still resolves to June, which is what we believed then.
    """
    alpha_symbology = symbology.Symbology.create(session, "ALPHA")
    beta_symbology = symbology.Symbology.create(session, "BETA")
    alpha_cl_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        MAPPED_FROM_DATE,
        MAPPING_LEARNED_DATETIME,
        name="ALPHA CL",
        currency_code="USD",
        legs=[instrument_synthetic.LegWeight(clm6_future_contract, Decimal(1))],
    )
    beta_cl_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        MAPPED_FROM_DATE,
        MAPPING_LEARNED_DATETIME,
        name="BETA CL",
        currency_code="USD",
        legs=[instrument_synthetic.LegWeight(clz6_future_contract, Decimal(1))],
    )
    symbology_for_instrument.create_symbol(
        session, alpha_symbology, MAPPED_FROM_DATE, MAPPING_LEARNED_DATETIME, alpha_cl_synthetic, "CL"
    )
    symbology_for_instrument.create_symbol(
        session, beta_symbology, MAPPED_FROM_DATE, MAPPING_LEARNED_DATETIME, beta_cl_synthetic, "CL"
    )

    alpha_cl_synthetic.with_new_legs(
        session,
        ROLL_DATE,
        ROLL_LEARNED_DATETIME,
        [instrument_synthetic.LegWeight(clz6_future_contract, Decimal(1))],
    )

    assert (
        symbology_for_instrument.resolve_target(
            session, alpha_symbology, "CL", as_of_module.AsOf(AFTER_ROLL_DATE, AFTER_ROLL_LEARNED_DATETIME)
        ).instrument_id
        == alpha_cl_synthetic.instrument_id
    )
    assert (
        _get_contract_id(session, alpha_symbology, as_of_module.AsOf(BEFORE_ROLL_DATE, AFTER_ROLL_LEARNED_DATETIME))
        == clm6_future_contract.instrument_id
    )
    assert (
        _get_contract_id(session, alpha_symbology, as_of_module.AsOf(AFTER_ROLL_DATE, AFTER_ROLL_LEARNED_DATETIME))
        == clz6_future_contract.instrument_id
    )
    assert (
        _get_contract_id(session, alpha_symbology, as_of_module.AsOf(AFTER_ROLL_DATE, QUERY_DATETIME))
        == clm6_future_contract.instrument_id
    )  # roll not yet known
    assert (
        _get_contract_id(session, beta_symbology, as_of_module.AsOf(BEFORE_ROLL_DATE, AFTER_ROLL_LEARNED_DATETIME))
        == clz6_future_contract.instrument_id
    )
    assert (
        _get_contract_id(session, beta_symbology, as_of_module.AsOf(AFTER_ROLL_DATE, AFTER_ROLL_LEARNED_DATETIME))
        == clz6_future_contract.instrument_id
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_04_a_late_correction_to_the_roll_date_keeps_the_old_answer_reproducible(
    session, clm6_future_contract, clz6_future_contract
) -> None:
    """
    On 2026-05-25 we learn ALPHA's roll really took effect on 2026-05-10, not 2026-05-15: restate the leg from the right
    date. A run made before the correction (known 2026-04-25) must still resolve the way it did then.
    """
    alpha_symbology = symbology.Symbology.create(session, "ALPHA")
    alpha_cl_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        MAPPED_FROM_DATE,
        MAPPING_LEARNED_DATETIME,
        name="ALPHA CL",
        currency_code="USD",
        legs=[instrument_synthetic.LegWeight(clm6_future_contract, Decimal(1))],
    )
    symbology_for_instrument.create_symbol(
        session, alpha_symbology, MAPPED_FROM_DATE, MAPPING_LEARNED_DATETIME, alpha_cl_synthetic, "CL"
    )
    alpha_cl_synthetic.with_new_legs(
        session,
        ROLL_DATE,
        ROLL_LEARNED_DATETIME,
        [instrument_synthetic.LegWeight(clz6_future_contract, Decimal(1))],
    )

    alpha_cl_synthetic.with_new_legs(
        session,
        CORRECTED_ROLL_DATE,
        CORRECTION_LEARNED_DATETIME,
        [instrument_synthetic.LegWeight(clz6_future_contract, Decimal(1))],
    )

    assert (
        _get_contract_id(
            session, alpha_symbology, as_of_module.AsOf(BETWEEN_ROLL_DATES_DATE, AFTER_ROLL_LEARNED_DATETIME)
        )
        == clm6_future_contract.instrument_id
    )
    assert (
        _get_contract_id(
            session, alpha_symbology, as_of_module.AsOf(BETWEEN_ROLL_DATES_DATE, AFTER_CORRECTION_DATETIME)
        )
        == clz6_future_contract.instrument_id
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_05_a_portfolio_security_has_one_name_per_mandate_at_a_time(session, clm6_future_contract) -> None:
    """
    Within one mandate symbology a synthetic cannot be both "CL" and "CL2" on the same dates; the database refuses it.
    Two research names that should trade the same contract are two synthetics, each with that contract as its leg.
    """
    alpha_symbology = symbology.Symbology.create(session, "ALPHA")
    alpha_cl_synthetic = instrument_synthetic.InstrumentSynthetic.create(
        session,
        MAPPED_FROM_DATE,
        MAPPING_LEARNED_DATETIME,
        name="ALPHA CL",
        currency_code="USD",
        legs=[instrument_synthetic.LegWeight(clm6_future_contract, Decimal(1))],
    )
    symbology_for_instrument.create_symbol(
        session, alpha_symbology, MAPPED_FROM_DATE, MAPPING_LEARNED_DATETIME, alpha_cl_synthetic, "CL"
    )

    with pytest.raises(exc.IntegrityError):
        symbology_for_instrument.create_symbol(
            session, alpha_symbology, MAPPED_FROM_DATE, MAPPING_LEARNED_DATETIME, alpha_cl_synthetic, "CL2"
        )
