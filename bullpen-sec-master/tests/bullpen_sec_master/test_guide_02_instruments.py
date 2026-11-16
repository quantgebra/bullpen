"""
User guide, part 2: instruments, time, and tickers.

Part 1 named exchanges, which do not change over time. Instruments do, and our understanding of them changes too,
so every instrument row carries TWO time ranges:

  effective   when the fact was true in the real world        ("it was called Facebook until 2022-06-08")
  knowledge   when our system held that belief                ("we learned of the rename on 2022-06-10")

Rows are never edited or deleted. A correction closes the knowledge range of the old row and appends new rows, so we
can always ask "what did we believe on date X, as of time Y?" -- the as_of(effective, known_at) question.

Vocabulary
  Instrument      one row per (instrument, period of time, period of belief). All rows of one instrument share a
                  stable instrument_id. Equity, futures contract, ... are subclasses of Instrument.
  SymbolForInstrument  "in this symbology, this instrument is called X", also bi-temporal (tickers change and get reused).
  Symbology       as in part 1. Instruments have no canonical symbol (company names are descriptive, not identifiers),
                  so an instrument symbology such as TICKER must store every symbol.
"""

import functools
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import exc
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_sec_master.instrument as instrument
import bullpen_sec_master.instrument_equity as instrument_equity
import bullpen_sec_master.instrument_futures_contract as instrument_futures_contract
import bullpen_sec_master.instrument_type as instrument_types
import bullpen_sec_master.symbology as symbology
import bullpen_sec_master.symbology_for_instrument as symbology_for_instrument

# The story: Facebook, Inc. (ticker FB) became Meta Platforms, Inc. (ticker META). Dates are illustrative.
LISTED_DATE: date = date(2012, 5, 18)
LEARNED_OF_LISTING_DATETIME: datetime = datetime(2012, 5, 20, 12, 0, tzinfo=timezone.utc)
RENAMED_DATE: date = date(2022, 6, 9)
LEARNED_OF_RENAME_DATETIME: datetime = datetime(2022, 6, 10, 12, 0, tzinfo=timezone.utc)
LEARNED_OF_FIX_DATETIME: datetime = datetime(2022, 7, 1, 12, 0, tzinfo=timezone.utc)
LATER_DATETIME: datetime = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------------------------------------------------
def test_01_create_an_instrument(session) -> None:
    """
    Create an equity. We say when it became true in the world (LISTED_DATE) and when we learned it
    (LEARNED_OF_LISTING_DATETIME). The system allocates a stable instrument_id, which is what everything else
    (ledger, orders) will refer to.
    """
    facebook_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Facebook, Inc.", currency_code="USD"
    )

    found_instrument = instrument.Instrument.find_as_of(
        session, facebook_equity.instrument_id, as_of_module.AsOf(date(2020, 1, 1), LATER_DATETIME)
    )

    assert found_instrument.instrument_id == facebook_equity.instrument_id
    assert found_instrument.name == "Facebook, Inc."
    assert found_instrument.instrument_type is instrument_types.InstrumentType.EQUITY
    assert isinstance(found_instrument, instrument_equity.InstrumentEquity)


# ----------------------------------------------------------------------------------------------------------------------
def test_02_two_time_axes(session) -> None:
    """
    as_of(effective_date, known_at_datetime) needs both. Before the listing date the company did not exist; before we
    recorded it we did not know about it. Either way the answer is "nothing".
    """
    facebook_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Facebook, Inc.", currency_code="USD"
    )
    before_listing_date = date(2012, 1, 1)
    before_recording_datetime = datetime(2012, 1, 1, tzinfo=timezone.utc)

    assert (
        instrument.Instrument.find_as_of(
            session, facebook_equity.instrument_id, as_of_module.AsOf(before_listing_date, LATER_DATETIME)
        )
        is None
    )
    assert (
        instrument.Instrument.find_as_of(
            session, facebook_equity.instrument_id, as_of_module.AsOf(date(2020, 1, 1), before_recording_datetime)
        )
        is None
    )
    assert (
        instrument.Instrument.find_as_of(
            session, facebook_equity.instrument_id, as_of_module.AsOf(date(2020, 1, 1), LATER_DATETIME)
        )
        is not None
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_03_a_change_appends_rows_and_keeps_history(session) -> None:
    """
    In June 2022 the company was renamed. with_new_name() says "from this effective date on, the instrument has this
    name" and returns the replacement row. The old belief is not overwritten: its knowledge range is closed, so
    we can still reproduce what we believed before we learned of the rename.
    """
    facebook_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Facebook, Inc.", currency_code="USD"
    )
    before_learning_datetime = datetime(2022, 6, 1, tzinfo=timezone.utc)

    facebook_equity.with_new_name(session, RENAMED_DATE, LEARNED_OF_RENAME_DATETIME, "Meta Platforms, Inc.")

    assert (
        instrument.Instrument.find_as_of(
            session, facebook_equity.instrument_id, as_of_module.AsOf(date(2021, 1, 1), LATER_DATETIME)
        ).name
        == "Facebook, Inc."
    )
    assert (
        instrument.Instrument.find_as_of(
            session, facebook_equity.instrument_id, as_of_module.AsOf(date(2023, 1, 1), LATER_DATETIME)
        ).name
        == "Meta Platforms, Inc."
    )
    assert (
        instrument.Instrument.find_as_of(
            session, facebook_equity.instrument_id, as_of_module.AsOf(date(2023, 1, 1), before_learning_datetime)
        ).name
        == "Facebook, Inc."
    )
    row_count = session.query(instrument.Instrument).filter_by(instrument_id=facebook_equity.instrument_id).count()
    assert row_count == 3  # original (now superseded), its surviving earlier part, and the replacement


# ----------------------------------------------------------------------------------------------------------------------
def test_03b_only_the_latest_believed_row_can_be_changed(session) -> None:
    """After a change the old row is a historical belief; changing it again would silently drop the newer facts."""
    facebook_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Facebook, Inc.", currency_code="USD"
    )
    facebook_equity.with_new_name(session, RENAMED_DATE, LEARNED_OF_RENAME_DATETIME, "Meta Platforms, Inc.")

    with pytest.raises(ValueError):
        facebook_equity.with_new_currency_code(session, RENAMED_DATE, LATER_DATETIME, "EUR")


# ----------------------------------------------------------------------------------------------------------------------
def test_04_the_type_of_an_instrument_never_changes(session, futures_root_by_code) -> None:
    """An equity cannot later become a future: the database rejects a row of the same instrument_id with a new type.
    There is deliberately no API for this, so the test inserts the bad row directly."""
    facebook_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Facebook, Inc.", currency_code="USD"
    )

    with pytest.raises(exc.IntegrityError):
        session.add(
            instrument_futures_contract.InstrumentFuturesContract(
                instrument_id=facebook_equity.instrument_id,
                effective_range=postgresql.Range(RENAMED_DATE, None, bounds="[)"),
                knowledge_range=postgresql.Range(LEARNED_OF_RENAME_DATETIME, None, bounds="[)"),
                name="Not a future",
                currency_code="USD",
                futures_root_id=futures_root_by_code["CL"].futures_root_id,
                contract_month=date(2026, 12, 1),
                last_trade_date=date(2026, 11, 20),
                expiry_date=date(2026, 11, 20),
            )
        )
        session.flush()


# ----------------------------------------------------------------------------------------------------------------------
def test_05_give_an_instrument_a_ticker(session) -> None:
    """
    Symbols live in symbologies (part 1). Create a TICKER symbology for instruments and name Facebook "FB" in it. An
    instrument that has not been named has no symbol in the symbology, because instruments have no canonical symbol.
    """
    ticker_symbology = symbology.Symbology.create(session, "TICKER")
    facebook_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Facebook, Inc.", currency_code="USD"
    )
    unnamed_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Unnamed Corp", currency_code="USD"
    )
    symbology_for_instrument.create_symbol(
        session, ticker_symbology, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, facebook_equity, "FB"
    )
    asked_date = date(2020, 1, 1)

    assert (
        symbology_for_instrument.get_symbol(
            session, ticker_symbology, facebook_equity, as_of_module.AsOf(asked_date, LATER_DATETIME)
        )
        == "FB"
    )
    assert (
        symbology_for_instrument.get_symbol(
            session, ticker_symbology, unnamed_equity, as_of_module.AsOf(asked_date, LATER_DATETIME)
        )
        is None
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_06_a_ticker_changes_over_time(session) -> None:
    """
    The ticker changed from FB to META on RENAMED_DATE. Names are bi-temporal like instruments, so with_new_symbol_text() works
    the same way. The answer depends on both the date asked about and what we knew.
    """
    ticker_symbology = symbology.Symbology.create(session, "TICKER")
    meta_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Meta Platforms, Inc.", currency_code="USD"
    )
    fb_symbol = symbology_for_instrument.create_symbol(
        session, ticker_symbology, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, meta_equity, "FB"
    )
    fb_symbol.with_new_symbol_text(session, RENAMED_DATE, LEARNED_OF_RENAME_DATETIME, "META")
    before_learning_datetime = datetime(2022, 6, 1, tzinfo=timezone.utc)

    assert (
        symbology_for_instrument.get_symbol(
            session, ticker_symbology, meta_equity, as_of_module.AsOf(date(2021, 1, 1), LATER_DATETIME)
        )
        == "FB"
    )
    assert (
        symbology_for_instrument.get_symbol(
            session, ticker_symbology, meta_equity, as_of_module.AsOf(date(2023, 1, 1), LATER_DATETIME)
        )
        == "META"
    )
    assert (
        symbology_for_instrument.get_symbol(
            session, ticker_symbology, meta_equity, as_of_module.AsOf(date(2023, 1, 1), before_learning_datetime)
        )
        == "FB"
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_07_resolve_a_ticker_as_of_a_date(session) -> None:
    """
    Inbound translation depends on the date too. A 2021 trade file saying "FB" is Meta; a 2023 file saying "FB" is
    not (that ticker has been retired); "META" only works after the change.
    """
    ticker_symbology = symbology.Symbology.create(session, "TICKER")
    meta_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Meta Platforms, Inc.", currency_code="USD"
    )
    fb_symbol = symbology_for_instrument.create_symbol(
        session, ticker_symbology, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, meta_equity, "FB"
    )
    fb_symbol.with_new_symbol_text(session, RENAMED_DATE, LEARNED_OF_RENAME_DATETIME, "META")
    resolve_ticker = functools.partial(symbology_for_instrument.resolve_target, session, ticker_symbology)

    assert (
        resolve_ticker("FB", as_of_module.AsOf(date(2021, 1, 1), LATER_DATETIME)).instrument_id
        == meta_equity.instrument_id
    )
    assert resolve_ticker("FB", as_of_module.AsOf(date(2023, 1, 1), LATER_DATETIME)) is None
    assert (
        resolve_ticker("META", as_of_module.AsOf(date(2023, 1, 1), LATER_DATETIME)).instrument_id
        == meta_equity.instrument_id
    )
    assert resolve_ticker("META", as_of_module.AsOf(date(2021, 1, 1), LATER_DATETIME)) is None


# ----------------------------------------------------------------------------------------------------------------------
def test_08_correcting_a_date_after_the_fact(session) -> None:
    """
    We recorded the change as effective 2022-06-09 (on 2022-06-10). On 2022-07-01 we learn it was really 2022-06-08.
    We simply apply the change again from the right date. Reports produced before 2022-07-01 can still be reproduced exactly: asking
    "as known on 2022-06-15" still gives the old answer for 2022-06-08.
    """
    ticker_symbology = symbology.Symbology.create(session, "TICKER")
    meta_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Meta Platforms, Inc.", currency_code="USD"
    )
    fb_symbol = symbology_for_instrument.create_symbol(
        session, ticker_symbology, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, meta_equity, "FB"
    )
    meta_symbol = fb_symbol.with_new_symbol_text(session, RENAMED_DATE, LEARNED_OF_RENAME_DATETIME, "META")
    meta_symbol.with_new_symbol_text(session, date(2022, 6, 8), LEARNED_OF_FIX_DATETIME, "META")
    june_8_date = date(2022, 6, 8)
    june_15_datetime = datetime(2022, 6, 15, tzinfo=timezone.utc)

    assert (
        symbology_for_instrument.get_symbol(
            session, ticker_symbology, meta_equity, as_of_module.AsOf(june_8_date, june_15_datetime)
        )
        == "FB"
    )  # old belief
    assert (
        symbology_for_instrument.get_symbol(
            session, ticker_symbology, meta_equity, as_of_module.AsOf(june_8_date, LATER_DATETIME)
        )
        == "META"
    )  # corrected
    assert (
        symbology_for_instrument.get_symbol(
            session, ticker_symbology, meta_equity, as_of_module.AsOf(date(2022, 6, 7), LATER_DATETIME)
        )
        == "FB"
    )  # unaffected


# ----------------------------------------------------------------------------------------------------------------------
def test_09_a_retired_ticker_can_be_reused(session) -> None:
    """
    Tickers get recycled. Retire "FB" for the old company with end(), and a different company can later take it.
    Resolution is by date, so history stays correct.
    """
    ticker_symbology = symbology.Symbology.create(session, "TICKER")
    first_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="First FB Corp", currency_code="USD"
    )
    second_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Second FB Corp", currency_code="USD"
    )
    first_symbol = symbology_for_instrument.create_symbol(
        session, ticker_symbology, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, first_equity, "FB"
    )
    first_symbol.end(session, RENAMED_DATE, LEARNED_OF_RENAME_DATETIME)
    symbology_for_instrument.create_symbol(
        session, ticker_symbology, date(2024, 1, 1), datetime(2024, 1, 2, tzinfo=timezone.utc), second_equity, "FB"
    )
    resolve_ticker = functools.partial(symbology_for_instrument.resolve_target, session, ticker_symbology)

    assert (
        resolve_ticker("FB", as_of_module.AsOf(date(2021, 1, 1), LATER_DATETIME)).instrument_id
        == first_equity.instrument_id
    )
    assert resolve_ticker("FB", as_of_module.AsOf(date(2023, 1, 1), LATER_DATETIME)) is None  # in between: nobody
    assert (
        resolve_ticker("FB", as_of_module.AsOf(date(2025, 1, 1), LATER_DATETIME)).instrument_id
        == second_equity.instrument_id
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_10_one_ticker_names_one_instrument_at_a_time(session) -> None:
    """The database refuses two instruments with the same ticker at the same time, which would make inbound files ambiguous."""
    ticker_symbology = symbology.Symbology.create(session, "TICKER")
    one_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="One", currency_code="USD"
    )
    two_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Two", currency_code="USD"
    )
    symbology_for_instrument.create_symbol(
        session, ticker_symbology, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, one_equity, "XYZ"
    )

    with pytest.raises(exc.IntegrityError):
        symbology_for_instrument.create_symbol(
            session, ticker_symbology, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, two_equity, "XYZ"
        )


# ----------------------------------------------------------------------------------------------------------------------
def test_11_a_vendor_symbology_inherits_from_ticker(session) -> None:
    """
    IBKR writes Berkshire's class B share as "BRK B" where our ticker is "BRK.B", but otherwise uses our tickers.
    Make "IBKR" a child of "TICKER" and store only the difference. Names not overridden come from the parent.
    """
    ticker_symbology = symbology.Symbology.create(session, "TICKER")
    ibkr_symbology = symbology.Symbology.create(session, "IBKR", parent=ticker_symbology)
    meta_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Meta Platforms, Inc.", currency_code="USD"
    )
    berkshire_equity = instrument_equity.InstrumentEquity.create(
        session, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, name="Berkshire Hathaway Inc. Class B", currency_code="USD"
    )
    symbology_for_instrument.create_symbol(
        session, ticker_symbology, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, meta_equity, "META"
    )
    symbology_for_instrument.create_symbol(
        session, ticker_symbology, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, berkshire_equity, "BRK.B"
    )
    symbology_for_instrument.create_symbol(
        session, ibkr_symbology, LISTED_DATE, LEARNED_OF_LISTING_DATETIME, berkshire_equity, "BRK B"
    )
    resolve_ticker = functools.partial(symbology_for_instrument.resolve_target, session, ticker_symbology)
    resolve_ibkr = functools.partial(symbology_for_instrument.resolve_target, session, ibkr_symbology)
    asked_date = date(2025, 1, 1)

    assert (
        symbology_for_instrument.get_symbol(
            session, ibkr_symbology, meta_equity, as_of_module.AsOf(asked_date, LATER_DATETIME)
        )
        == "META"
    )  # inherited
    assert (
        symbology_for_instrument.get_symbol(
            session, ibkr_symbology, berkshire_equity, as_of_module.AsOf(asked_date, LATER_DATETIME)
        )
        == "BRK B"
    )
    assert (
        resolve_ibkr("BRK B", as_of_module.AsOf(asked_date, LATER_DATETIME)).instrument_id
        == berkshire_equity.instrument_id
    )
    assert (
        resolve_ibkr("META", as_of_module.AsOf(asked_date, LATER_DATETIME)).instrument_id == meta_equity.instrument_id
    )
    assert resolve_ibkr("BRK.B", as_of_module.AsOf(asked_date, LATER_DATETIME)) is None  # shadowed by IBKR's "BRK B"
    assert (
        resolve_ticker("BRK.B", as_of_module.AsOf(asked_date, LATER_DATETIME)).instrument_id
        == berkshire_equity.instrument_id
    )  # parent unaffected
