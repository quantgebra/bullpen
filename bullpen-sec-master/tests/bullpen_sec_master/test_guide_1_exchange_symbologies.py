"""
User guide, part 1: exchanges and their symbologies.

Read top to bottom; each test builds on the idea of the one before it but is self-contained.

Vocabulary
  Exchange     a trading venue. One row, identified by its MIC (ISO 10383 code), e.g. XCME.
  Symbology    a naming system, e.g. "IBKR". One symbology names exchanges, futures roots and instruments alike. It
               stores only the symbols that DIFFER from the target's own canonical name (for an exchange, its MIC). The
               MIC is a column on Exchange, not a symbology. (A symbology can also have a parent and inherit from it.)
  symbology_for_exchange  the exchange operations of a symbology: create_symbol(), get_symbol(), resolve_target().
  SymbolForExchange       one row saying "in this symbology, this exchange is called X".

The vendor symbols below are illustrative, not authoritative.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import exc, orm

import bullpen_sec_master.as_of as as_of_module
import bullpen_sec_master.exchange as exchange
import bullpen_sec_master.symbol_for_exchange as symbol_for_exchange
import bullpen_sec_master.symbology as symbology
import bullpen_sec_master.symbology_for_exchange as symbology_for_exchange


# Symbols are bi-temporal like instruments: they have an effective date range and a knowledge range. The vendor symbols
# below are recorded as effective from BEGIN_DATE and known from LEARNED_DATETIME; queries are made as of TODAY/NOW.
BEGIN_DATE: date = date(2020, 1, 1)
LEARNED_DATETIME: datetime = datetime(2020, 1, 2, 12, 0, tzinfo=timezone.utc)
TODAY_DATE: date = date(2026, 3, 1)
NOW_DATETIME: datetime = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def cme_exchange(session: orm.Session) -> exchange.Exchange:
    """CME, home of equity-index futures."""
    result: exchange.Exchange = exchange.Exchange(mic="XCME", name="CME Globex")
    session.add(result)
    session.flush()
    return result


# ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def nymex_exchange(session: orm.Session) -> exchange.Exchange:
    """NYMEX, home of energy futures."""
    result: exchange.Exchange = exchange.Exchange(mic="XNYM", name="New York Mercantile Exchange")
    session.add(result)
    session.flush()
    return result


# ----------------------------------------------------------------------------------------------------------------------
def test_01_create_exchanges(session, cme_exchange, nymex_exchange) -> None:
    """An exchange is a plain row. It has a MIC and a display name, and nothing vendor-specific."""
    found_exchanges = session.query(exchange.Exchange).order_by(exchange.Exchange.mic).all()

    assert [found.mic for found in found_exchanges] == ["XCME", "XNYM"]
    assert cme_exchange.name == "CME Globex"


# ----------------------------------------------------------------------------------------------------------------------
def test_02_a_symbology_with_no_symbols_uses_the_mic(session, cme_exchange, nymex_exchange) -> None:
    """
    Define a symbology, "IBKR", and store nothing in it yet. Every exchange then has its canonical symbol, its MIC.
    get_symbol() turns an exchange into its symbol in that symbology.
    """
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")

    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, cme_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "XCME"
    )
    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, nymex_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "XNYM"
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_03_store_the_symbols_that_differ(session, cme_exchange, nymex_exchange) -> None:
    """
    IBKR does not call these venues by their MICs: CME is "GLOBEX" and NYMEX is "NYMEX". Store those two symbols in
    the "IBKR" symbology. The exchanges themselves are untouched.
    """
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")
    symbology_for_exchange.create_symbol(session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "GLOBEX")
    symbology_for_exchange.create_symbol(session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, nymex_exchange, "NYMEX")

    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, cme_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "GLOBEX"
    )
    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, nymex_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "NYMEX"
    )
    assert cme_exchange.mic == "XCME"


# ----------------------------------------------------------------------------------------------------------------------
def test_04_a_symbology_only_stores_what_differs(session, cme_exchange, nymex_exchange) -> None:
    """
    Bloomberg (illustratively) calls CME "CME" but calls NYMEX by its MIC. So the "BBG" symbology stores one symbol
    only. Asking for NYMEX falls back to the canonical MIC, "XNYM", without any row being stored for it.
    """
    bbg_symbology = symbology.Symbology.create(session, "BBG")
    symbology_for_exchange.create_symbol(session, bbg_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "CME")

    stored_symbol_count = (
        session.query(symbol_for_exchange.SymbolForExchange).filter_by(symbology_id=bbg_symbology.id).count()
    )  # canonical MICs are never stored as symbols

    assert stored_symbol_count == 1
    assert (
        symbology_for_exchange.get_symbol(
            session, bbg_symbology, cme_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "CME"
    )
    assert (
        symbology_for_exchange.get_symbol(
            session, bbg_symbology, nymex_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "XNYM"
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_05_symbologies_are_independent(session, cme_exchange) -> None:
    """The same exchange has a different symbol in each vendor symbology; vendors never affect each other."""
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")
    symbology_for_exchange.create_symbol(session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "GLOBEX")
    bbg_symbology = symbology.Symbology.create(session, "BBG")
    symbology_for_exchange.create_symbol(session, bbg_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "CME")
    no_overrides_symbology = symbology.Symbology.create(session, "NO_OVERRIDES")

    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, cme_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "GLOBEX"
    )
    assert (
        symbology_for_exchange.get_symbol(
            session, bbg_symbology, cme_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "CME"
    )
    assert (
        symbology_for_exchange.get_symbol(
            session, no_overrides_symbology, cme_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "XCME"
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_06_translate_a_vendor_symbol_back_to_our_exchange(session, cme_exchange, nymex_exchange) -> None:
    """
    The reverse direction matters for inbound files: a broker says "GLOBEX" and we need our exchange. resolve_target()
    returns the Exchange, or None if the symbol is not valid in that symbology.
    """
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")
    symbology_for_exchange.create_symbol(session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "GLOBEX")

    assert (
        symbology_for_exchange.resolve_target(
            session, ibkr_symbology, "GLOBEX", as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        is cme_exchange
    )
    assert (
        symbology_for_exchange.resolve_target(
            session, ibkr_symbology, "XNYM", as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        is nymex_exchange
    )
    assert (
        symbology_for_exchange.resolve_target(
            session, ibkr_symbology, "NOSUCH", as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        is None
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_07_a_renamed_exchange_no_longer_answers_to_its_mic(session, cme_exchange) -> None:
    """
    Subtle rule (called shadowing). IBKR renames CME to "GLOBEX", so IBKR will never send "XCME". Resolving "XCME"
    in the IBKR symbology therefore returns None, even though XCME is CME's canonical MIC. A symbology that does not
    rename CME still resolves "XCME".
    """
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")
    symbology_for_exchange.create_symbol(session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "GLOBEX")
    no_overrides_symbology = symbology.Symbology.create(session, "NO_OVERRIDES")

    assert (
        symbology_for_exchange.resolve_target(
            session, ibkr_symbology, "XCME", as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        is None
    )
    assert (
        symbology_for_exchange.resolve_target(
            session, no_overrides_symbology, "XCME", as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        is cme_exchange
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_08_a_child_symbology_inherits_its_parents_symbols(session, cme_exchange, nymex_exchange) -> None:
    """
    A symbology can have a parent and store only the difference. Here "IBKR_PAPER" inherits IBKR's symbols except that
    it renames NYMEX; CME is still "GLOBEX" through the parent.
    """
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")
    symbology_for_exchange.create_symbol(session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "GLOBEX")
    symbology_for_exchange.create_symbol(session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, nymex_exchange, "NYMEX")
    paper_symbology = symbology.Symbology.create(session, "IBKR_PAPER", parent=ibkr_symbology)
    symbology_for_exchange.create_symbol(
        session, paper_symbology, BEGIN_DATE, LEARNED_DATETIME, nymex_exchange, "NYMEX_PAPER"
    )

    assert (
        symbology_for_exchange.get_symbol(
            session, paper_symbology, cme_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "GLOBEX"
    )  # inherited
    assert (
        symbology_for_exchange.get_symbol(
            session, paper_symbology, nymex_exchange, as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        == "NYMEX_PAPER"
    )  # overridden
    assert (
        symbology_for_exchange.resolve_target(
            session, paper_symbology, "NYMEX_PAPER", as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        is nymex_exchange
    )
    assert (
        symbology_for_exchange.resolve_target(
            session, paper_symbology, "NYMEX", as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        is None
    )  # shadowed by the child's own symbol


# ----------------------------------------------------------------------------------------------------------------------
def test_09_a_symbol_identifies_one_exchange_per_symbology(session, cme_exchange, nymex_exchange) -> None:
    """Within one symbology a symbol may refer to only one exchange, otherwise inbound translation would be ambiguous."""
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")
    symbology_for_exchange.create_symbol(session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "GLOBEX")

    with pytest.raises(exc.IntegrityError):
        symbology_for_exchange.create_symbol(
            session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, nymex_exchange, "GLOBEX"
        )


# ----------------------------------------------------------------------------------------------------------------------
def test_10_an_exchange_has_one_symbol_per_symbology(session, cme_exchange) -> None:
    """Likewise an exchange has exactly one symbol in a symbology; a second symbol for it is refused."""
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")
    symbology_for_exchange.create_symbol(session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "GLOBEX")

    with pytest.raises(exc.IntegrityError):
        symbology_for_exchange.create_symbol(
            session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "GLOBEX2"
        )


# ----------------------------------------------------------------------------------------------------------------------
def test_11_the_same_symbol_can_exist_in_different_symbologies(session, cme_exchange, nymex_exchange) -> None:
    """Uniqueness is per symbology: two vendors may use the same string for different exchanges."""
    vendor_a_symbology = symbology.Symbology.create(session, "VENDOR_A")
    vendor_b_symbology = symbology.Symbology.create(session, "VENDOR_B")
    symbology_for_exchange.create_symbol(session, vendor_a_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "CME")
    symbology_for_exchange.create_symbol(
        session, vendor_b_symbology, BEGIN_DATE, LEARNED_DATETIME, nymex_exchange, "CME"
    )  # unusual, but legal

    assert (
        symbology_for_exchange.resolve_target(
            session, vendor_a_symbology, "CME", as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        is cme_exchange
    )
    assert (
        symbology_for_exchange.resolve_target(
            session, vendor_b_symbology, "CME", as_of_module.AsOf(TODAY_DATE, NOW_DATETIME)
        )
        is nymex_exchange
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_12_looking_up_a_symbology_by_code(session) -> None:
    """Symbologies are found by code; an undefined one is simply not found."""
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")

    assert symbology.Symbology.find_by_code(session, "IBKR") is ibkr_symbology
    assert symbology.Symbology.find_by_code(session, "NO_SUCH_SYMBOLOGY") is None


# ----------------------------------------------------------------------------------------------------------------------
def test_13_a_vendors_symbol_can_change_over_time(session, cme_exchange) -> None:
    """
    Exchange symbols are bi-temporal. IBKR renames CME's symbol from "GLOBEX" to "GLBX" effective 2024-01-01 (we learn
    of it on 2024-01-02). Both directions depend on the date asked about and on what we knew; resolve_target() returns
    the Exchange itself.
    """
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")
    globex_symbol = symbology_for_exchange.create_symbol(
        session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "GLOBEX"
    )
    globex_symbol.with_new_symbol_text(session, date(2024, 1, 1), datetime(2024, 1, 2, tzinfo=timezone.utc), "GLBX")
    in_2023_date = date(2023, 6, 1)
    in_2025_date = date(2025, 6, 1)
    before_learning_datetime = datetime(2023, 12, 1, tzinfo=timezone.utc)

    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, cme_exchange, as_of_module.AsOf(in_2023_date, NOW_DATETIME)
        )
        == "GLOBEX"
    )
    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, cme_exchange, as_of_module.AsOf(in_2025_date, NOW_DATETIME)
        )
        == "GLBX"
    )
    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, cme_exchange, as_of_module.AsOf(in_2025_date, before_learning_datetime)
        )
        == "GLOBEX"
    )  # before we learned of the change
    assert (
        symbology_for_exchange.resolve_target(
            session, ibkr_symbology, "GLBX", as_of_module.AsOf(in_2025_date, NOW_DATETIME)
        )
        is cme_exchange
    )
    assert (
        symbology_for_exchange.resolve_target(
            session, ibkr_symbology, "GLBX", as_of_module.AsOf(in_2023_date, NOW_DATETIME)
        )
        is None
    )
    assert (
        symbology_for_exchange.resolve_target(
            session, ibkr_symbology, "GLOBEX", as_of_module.AsOf(in_2025_date, NOW_DATETIME)
        )
        is None
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_14_ending_a_symbol_falls_back_to_the_mic(session, cme_exchange) -> None:
    """After a vendor stops renaming an exchange, end() the symbol; from then on the canonical MIC applies again."""
    ibkr_symbology = symbology.Symbology.create(session, "IBKR")
    globex_symbol = symbology_for_exchange.create_symbol(
        session, ibkr_symbology, BEGIN_DATE, LEARNED_DATETIME, cme_exchange, "GLOBEX"
    )
    globex_symbol.end(session, date(2024, 1, 1), datetime(2024, 1, 2, tzinfo=timezone.utc))

    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, cme_exchange, as_of_module.AsOf(date(2023, 6, 1), NOW_DATETIME)
        )
        == "GLOBEX"
    )
    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, cme_exchange, as_of_module.AsOf(date(2025, 6, 1), NOW_DATETIME)
        )
        == "XCME"
    )
    assert (
        symbology_for_exchange.resolve_target(
            session, ibkr_symbology, "XCME", as_of_module.AsOf(date(2025, 6, 1), NOW_DATETIME)
        )
        is cme_exchange
    )
