"""
User guide, part 4: the futures data model.

A futures contract is an Instrument with extra, futures-specific facts stored in a joined table (instrument_futures_contracts).
Product-level facts (tick size, multiplier, settlement type) live on the futures ROOT and are read "as of" the same date
and time as the question about the contract, so correcting a root also corrects every contract's view of it.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy
from sqlalchemy import exc
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_sec_master.futures_root as futures_root
import bullpen_sec_master.instrument as instrument
import bullpen_sec_master.instrument_equity as instrument_equity
import bullpen_sec_master.instrument_futures_contract as instrument_futures_contract

from .conftest import FIRST_KNOWN_DATETIME, SECOND_KNOWN_DATETIME, THIRD_KNOWN_DATETIME

AS_OF_DATE: date = date(2026, 3, 1)


# ----------------------------------------------------------------------------------------------------------------------
def test_01_find_as_of_returns_the_right_subclass_with_details(session, clz6_future_contract) -> None:
    """One query on Instrument returns an InstrumentEquity or a InstrumentFuturesContract, with the futures details loaded."""
    acme_equity = instrument_equity.InstrumentEquity.create(
        session, date(2020, 1, 1), FIRST_KNOWN_DATETIME, name="Acme", currency_code="USD"
    )
    session.expire_all()

    found_future = instrument.Instrument.find_as_of(
        session, clz6_future_contract.instrument_id, as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)
    )
    found_equity = instrument.Instrument.find_as_of(
        session, acme_equity.instrument_id, as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)
    )

    assert isinstance(found_future, instrument_futures_contract.InstrumentFuturesContract)
    assert found_future.expiry_date == date(2026, 11, 20)
    assert isinstance(found_equity, instrument_equity.InstrumentEquity)


# ----------------------------------------------------------------------------------------------------------------------
def test_02_instrument_type_is_immutable(session, futures_root_by_code) -> None:
    """There is no identity table, so the database enforces it: EXCLUDE (instrument_id WITH =, type WITH <>)."""
    acme_equity = instrument_equity.InstrumentEquity.create(
        session, date(2020, 1, 1), FIRST_KNOWN_DATETIME, name="Acme", currency_code="USD"
    )

    with pytest.raises(exc.IntegrityError):
        session.add(
            instrument_futures_contract.InstrumentFuturesContract(
                instrument_id=acme_equity.instrument_id,
                effective_range=postgresql.Range(date(2026, 1, 1), None, bounds="[)"),
                knowledge_range=postgresql.Range(FIRST_KNOWN_DATETIME, None, bounds="[)"),
                name="Wrong",
                currency_code="USD",
                futures_root_id=futures_root_by_code["CL"].futures_root_id,
                contract_month=date(2026, 12, 1),
                last_trade_date=date(2026, 11, 20),
                expiry_date=date(2026, 11, 20),
            )
        )
        session.flush()


# ----------------------------------------------------------------------------------------------------------------------
def test_03_a_change_carries_the_details_row_into_the_surviving_prefix(session, clz6_future_contract) -> None:
    """Each class copies its own columns, including the joined instrument_futures_contracts row, so no detail is lost."""
    clz6_future_contract.with_new_expiry_date(session, date(2026, 6, 1), SECOND_KNOWN_DATETIME, date(2026, 11, 19))
    session.expire_all()
    find_contract = (
        lambda effective_date, known_at_datetime: instrument_futures_contract.InstrumentFuturesContract.find_as_of(
            session, clz6_future_contract.instrument_id, as_of_module.AsOf(effective_date, known_at_datetime)
        )
    )

    before_change_contract = find_contract(date(2026, 3, 1), THIRD_KNOWN_DATETIME)
    after_change_contract = find_contract(date(2026, 7, 1), THIRD_KNOWN_DATETIME)
    known_earlier_contract = find_contract(date(2026, 7, 1), FIRST_KNOWN_DATETIME)

    assert before_change_contract.expiry_date == date(2026, 11, 20)
    assert after_change_contract.expiry_date == date(2026, 11, 19)
    assert after_change_contract.name == before_change_contract.name
    assert after_change_contract.futures_root_id == before_change_contract.futures_root_id
    assert known_earlier_contract.expiry_date == date(2026, 11, 20)
    details_row_count = session.scalar(sqlalchemy.text("select count(*) from sec_master.instrument_futures_contracts"))
    assert details_row_count == 3  # original (knowledge closed), surviving prefix, replacement


# ----------------------------------------------------------------------------------------------------------------------
def test_04_a_root_correction_reaches_existing_contracts_with_history(
    session, clz6_future_contract, futures_root_by_code
) -> None:
    """Correct CL's tick size; the contract sees the new value now and the old one as it was known earlier."""
    cl_root = futures_root_by_code["CL"]
    cl_root.with_new_tick_size(session, date(2026, 1, 1), SECOND_KNOWN_DATETIME, Decimal("0.02"))
    session.expire_all()

    current_contract = instrument.Instrument.find_as_of(
        session, clz6_future_contract.instrument_id, as_of_module.AsOf(AS_OF_DATE, THIRD_KNOWN_DATETIME)
    )
    earlier_contract = instrument.Instrument.find_as_of(
        session, clz6_future_contract.instrument_id, as_of_module.AsOf(AS_OF_DATE, FIRST_KNOWN_DATETIME)
    )

    current_root = current_contract.get_root(session)
    earlier_root = earlier_contract.get_root(session)

    assert current_root.tick_size == Decimal("0.02")
    assert current_root.get_tick_value() == Decimal("20")
    assert earlier_root.tick_size == Decimal("0.01")
    assert earlier_root.get_tick_value() == Decimal("10")


# ----------------------------------------------------------------------------------------------------------------------
def test_05_contract_multiplier_comes_from_the_root(session, clz6_future_contract) -> None:
    found_contract = instrument.Instrument.find_as_of(
        session, clz6_future_contract.instrument_id, as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)
    )

    assert found_contract.get_contract_multiplier(session) == Decimal("1000")
