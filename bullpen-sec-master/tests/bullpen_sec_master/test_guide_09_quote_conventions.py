"""
User guide, part 9: quote conventions.

A symbology names things; a QUOTE CONVENTION says how it writes their numbers. bullpen's canonical price is in major
units of the instrument's currency (dollars, not cents), so notional = price * multiplier * quantity, and a canonical
futures quantity is in contracts. Vendors differ: CBOT corn is quoted in cents per bushel, London equities in pence, and
a clearing statement may report crude in barrels. A convention holds two factors:

    canonical price = vendor price * price_factor        canonical quantity = vendor quantity * quantity_factor

Conventions are stored only where a symbology differs from canonical, and are inherited down the symbology chain like
symbols. They attach to a futures root (all its contracts) or to one instrument, and they are bi-temporal. Convert at
the boundary, when a vendor's number comes in or goes out; nothing inside bullpen holds a vendor price.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import bullpen_sec_master.as_of as as_of_module
import bullpen_sec_master.futures_root as futures_root
import bullpen_sec_master.instrument_equity as instrument_equity
import bullpen_sec_master.instrument_futures_contract as instrument_futures_contract
import bullpen_sec_master.quote_convention as quote_convention
import bullpen_sec_master.quote_convention_for_futures_root as quote_convention_for_futures_root
import bullpen_sec_master.quote_convention_for_instrument as quote_convention_for_instrument
import bullpen_sec_master.settlement_type as settlement_types
import bullpen_sec_master.symbology as symbology_module

from .conftest import BEGIN_DATE, FIRST_KNOWN_DATETIME

NOW_AS_OF: as_of_module.AsOf = as_of_module.AsOf(date(2026, 11, 13), datetime(2026, 11, 15, tzinfo=timezone.utc))
CENTS_PRICE_FACTOR: Decimal = Decimal("0.01")


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def zc_futures_root(session, exchange_by_mic) -> futures_root.FuturesRoot:
    """CBOT corn: 5,000 bushels, canonically priced in dollars per bushel, so one contract is worth price * 5000."""
    result: futures_root.FuturesRoot = futures_root.FuturesRoot.create(
        session,
        BEGIN_DATE,
        FIRST_KNOWN_DATETIME,
        exchange=exchange_by_mic["XCBT"],
        name="ZC",
        currency_code="USD",
        multiplier=Decimal(5000),
        tick_size=Decimal("0.0025"),
        settlement_type=settlement_types.SettlementType.PHYSICAL,
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def zcz6_future_contract(session, zc_futures_root) -> instrument_futures_contract.InstrumentFuturesContract:
    """Corn December 2026."""
    result: instrument_futures_contract.InstrumentFuturesContract = (
        instrument_futures_contract.InstrumentFuturesContract.create(
            session,
            date(2025, 6, 1),
            FIRST_KNOWN_DATETIME,
            name="Corn Dec 2026",
            currency_code="USD",
            futures_root=zc_futures_root,
            contract_month=date(2026, 12, 1),
            last_trade_date=date(2026, 12, 14),
            first_notice_date=date(2026, 11, 30),
            expiry_date=date(2026, 12, 14),
        )
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def common_corn_quote_convention(
    session, vendor_symbology_by_code, zc_futures_root
) -> quote_convention_for_futures_root.QuoteConventionForFuturesRoot:
    """Every vendor under FUT_COMMON quotes corn in cents per bushel, as the exchange does."""
    result: quote_convention_for_futures_root.QuoteConventionForFuturesRoot = (
        quote_convention_for_futures_root.QuoteConventionForFuturesRoot.create(
            session,
            vendor_symbology_by_code["FUT_COMMON"],
            BEGIN_DATE,
            FIRST_KNOWN_DATETIME,
            zc_futures_root,
            price_factor=CENTS_PRICE_FACTOR,
            quantity_factor=Decimal(1),
        )
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def test_01_without_a_convention_a_symbology_is_canonical(
    session, vendor_symbology_by_code, clz6_future_contract
) -> None:
    """Nothing is stored for CL, so IBKR's crude prices and quantities are already canonical."""
    ibkr_convention: quote_convention.QuoteConvention = quote_convention.get_quote_convention(
        session, vendor_symbology_by_code["IBKR"], clz6_future_contract, NOW_AS_OF
    )

    assert ibkr_convention == quote_convention.CANONICAL_QUOTE_CONVENTION
    assert ibkr_convention.to_canonical_price(Decimal("71.38")) == Decimal("71.38")


# ----------------------------------------------------------------------------------------------------------------------
def test_02_a_root_convention_covers_every_contract_and_is_inherited(
    session, vendor_symbology_by_code, zc_futures_root, zcz6_future_contract, common_corn_quote_convention
) -> None:
    """
    The cents convention is stored once, on FUT_COMMON and the ZC root. IBKR inherits it, so IBKR's 450.25 for
    December corn is $4.5025 per bushel, and one contract is worth $22,512.50.
    """
    ibkr_convention: quote_convention.QuoteConvention = quote_convention.get_quote_convention(
        session, vendor_symbology_by_code["IBKR"], zcz6_future_contract, NOW_AS_OF
    )
    canonical_price: Decimal = ibkr_convention.to_canonical_price(Decimal("450.25"))

    assert canonical_price == Decimal("4.5025")
    assert canonical_price * zc_futures_root.multiplier == Decimal("22512.50")


# ----------------------------------------------------------------------------------------------------------------------
def test_03_a_more_specific_symbology_overrides_its_parent(
    session, vendor_symbology_by_code, zc_futures_root, zcz6_future_contract, common_corn_quote_convention
) -> None:
    """CSI publishes corn in dollars. Its own convention beats the one it would inherit; IBKR is unaffected."""
    quote_convention_for_futures_root.QuoteConventionForFuturesRoot.create(
        session,
        vendor_symbology_by_code["CSI"],
        BEGIN_DATE,
        FIRST_KNOWN_DATETIME,
        zc_futures_root,
        price_factor=Decimal(1),
        quantity_factor=Decimal(1),
    )

    csi_convention: quote_convention.QuoteConvention = quote_convention.get_quote_convention(
        session, vendor_symbology_by_code["CSI"], zcz6_future_contract, NOW_AS_OF
    )
    ibkr_convention: quote_convention.QuoteConvention = quote_convention.get_quote_convention(
        session, vendor_symbology_by_code["IBKR"], zcz6_future_contract, NOW_AS_OF
    )

    assert csi_convention.to_canonical_price(Decimal("4.5025")) == Decimal("4.5025")
    assert ibkr_convention.to_canonical_price(Decimal("450.25")) == Decimal("4.5025")


# ----------------------------------------------------------------------------------------------------------------------
def test_04_quantities_convert_too(session, futures_root_by_code, clz6_future_contract) -> None:
    """A clearing statement reports crude in barrels: 5,000 barrels is 5 contracts, and back again on the way out."""
    clearer_symbology: symbology_module.Symbology = symbology_module.Symbology.create(session, "CLEARER")
    quote_convention_for_futures_root.QuoteConventionForFuturesRoot.create(
        session,
        clearer_symbology,
        BEGIN_DATE,
        FIRST_KNOWN_DATETIME,
        futures_root_by_code["CL"],
        price_factor=Decimal(1),
        quantity_factor=Decimal("0.001"),
    )

    clearer_convention: quote_convention.QuoteConvention = quote_convention.get_quote_convention(
        session, clearer_symbology, clz6_future_contract, NOW_AS_OF
    )

    assert clearer_convention.to_canonical_quantity(Decimal(5000)) == Decimal(5)
    assert clearer_convention.to_vendor_quantity(Decimal(5)) == Decimal(5000)


# ----------------------------------------------------------------------------------------------------------------------
def test_05_an_instrument_can_have_its_own_convention(session, vendor_symbology_by_code) -> None:
    """IBKR quotes Vodafone in pence: 72.50 is £0.7250 canonically, and £0.7250 goes back out as 72.50."""
    vodafone_equity: instrument_equity.InstrumentEquity = instrument_equity.InstrumentEquity.create(
        session, BEGIN_DATE, FIRST_KNOWN_DATETIME, name="Vodafone Group", currency_code="GBP"
    )
    quote_convention_for_instrument.QuoteConventionForInstrument.create(
        session,
        vendor_symbology_by_code["IBKR"],
        BEGIN_DATE,
        FIRST_KNOWN_DATETIME,
        vodafone_equity,
        price_factor=CENTS_PRICE_FACTOR,
        quantity_factor=Decimal(1),
    )

    ibkr_convention: quote_convention.QuoteConvention = quote_convention.get_quote_convention(
        session, vendor_symbology_by_code["IBKR"], vodafone_equity, NOW_AS_OF
    )

    assert ibkr_convention.to_canonical_price(Decimal("72.50")) == Decimal("0.7250")
    assert ibkr_convention.to_vendor_price(Decimal("0.7250")) == Decimal("72.50")


# ----------------------------------------------------------------------------------------------------------------------
def test_06_within_one_symbology_a_contracts_own_convention_beats_its_roots(
    session, vendor_symbology_by_code, zcz6_future_contract, common_corn_quote_convention
) -> None:
    """
    Say FUT_COMMON quoted December corn alone in dollars (illustrative). The contract's own row wins over the root's in
    the same symbology, and IBKR, which inherits from FUT_COMMON, sees the same.
    """
    quote_convention_for_instrument.QuoteConventionForInstrument.create(
        session,
        vendor_symbology_by_code["FUT_COMMON"],
        BEGIN_DATE,
        FIRST_KNOWN_DATETIME,
        zcz6_future_contract,
        price_factor=Decimal(1),
        quantity_factor=Decimal(1),
    )

    ibkr_convention: quote_convention.QuoteConvention = quote_convention.get_quote_convention(
        session, vendor_symbology_by_code["IBKR"], zcz6_future_contract, NOW_AS_OF
    )

    assert ibkr_convention == quote_convention.CANONICAL_QUOTE_CONVENTION


# ----------------------------------------------------------------------------------------------------------------------
def test_07_a_convention_change_is_dated_and_learned(
    session, vendor_symbology_by_code, zcz6_future_contract, common_corn_quote_convention
) -> None:
    """
    The vendors switch corn to dollars from 2026-07-01, and we learn it on 2026-07-15. June prices stay in cents; August
    prices are in dollars, except to a run made before we learned of the switch.
    """
    learned_datetime: datetime = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    common_corn_quote_convention.with_new_factors(
        session, date(2026, 7, 1), learned_datetime, price_factor=Decimal(1), quantity_factor=Decimal(1)
    )

    june_convention: quote_convention.QuoteConvention = quote_convention.get_quote_convention(
        session,
        vendor_symbology_by_code["IBKR"],
        zcz6_future_contract,
        NOW_AS_OF._replace(effective_date=date(2026, 6, 1)),
    )
    august_convention: quote_convention.QuoteConvention = quote_convention.get_quote_convention(
        session,
        vendor_symbology_by_code["IBKR"],
        zcz6_future_contract,
        NOW_AS_OF._replace(effective_date=date(2026, 8, 3)),
    )
    august_before_learning_convention: quote_convention.QuoteConvention = quote_convention.get_quote_convention(
        session,
        vendor_symbology_by_code["IBKR"],
        zcz6_future_contract,
        as_of_module.AsOf(date(2026, 8, 3), datetime(2026, 7, 10, tzinfo=timezone.utc)),
    )

    assert june_convention.price_factor == CENTS_PRICE_FACTOR
    assert august_convention == quote_convention.CANONICAL_QUOTE_CONVENTION
    assert august_before_learning_convention.price_factor == CENTS_PRICE_FACTOR
