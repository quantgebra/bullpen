"""
User guide, part 3: futures roots and contract symbols.

A futures ROOT is a product such as CL (crude) or ES (S&P 500 e-mini). Its canonical symbol is its `name` column; vendors
may call it something else (CSI calls CL "21"). A futures CONTRACT such as CLZ6 is an instrument. Most vendors build
contract symbols from the root name plus a template, so we store the template once per symbology instead of one symbol
per contract; an explicit per-contract symbol still wins when a vendor has an exception.

The vendor symbols and templates below are illustrative, not authoritative.
"""

import functools
from datetime import date

import bullpen_sec_master.as_of as as_of_module
import bullpen_sec_master.symbology_for_exchange as symbology_for_exchange
import bullpen_sec_master.symbology_for_futures_root as symbology_for_futures_root
import bullpen_sec_master.symbology_for_instrument as symbology_for_instrument

from .conftest import BEGIN_DATE, FIRST_KNOWN_DATETIME, SECOND_KNOWN_DATETIME

AS_OF_DATE: date = date(2026, 3, 1)


# ----------------------------------------------------------------------------------------------------------------------
def test_01_symbols_for_exchanges_can_be_overridden_per_vendor(
    session, exchange_by_mic, vendor_symbology_by_code
) -> None:
    """Three CME-group venues are renamed by IBKR; an exchange nobody renames keeps its MIC."""
    ibkr_symbology = vendor_symbology_by_code["IBKR"]

    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, exchange_by_mic["XCME"], as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)
        )
        == "GLOBEX"
    )
    assert (
        symbology_for_exchange.get_symbol(
            session, ibkr_symbology, exchange_by_mic["XTST"], as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)
        )
        == "XTST"
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_02_root_symbols_default_to_the_canonical_symbol(
    session, futures_root_by_code, vendor_symbology_by_code
) -> None:
    """IBKR stores no root symbols, so CL is "CL". CSI renames CL to "21" and leaves ES as "ES"."""
    ibkr_symbology = vendor_symbology_by_code["IBKR"]
    csi_symbology = vendor_symbology_by_code["CSI"]
    cl_root = futures_root_by_code["CL"]
    es_root = futures_root_by_code["ES"]

    assert (
        symbology_for_futures_root.get_symbol(
            session, ibkr_symbology, cl_root, as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)
        )
        == "CL"
    )
    assert (
        symbology_for_futures_root.get_symbol(
            session, csi_symbology, cl_root, as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)
        )
        == "21"
    )
    assert (
        symbology_for_futures_root.get_symbol(
            session, csi_symbology, es_root, as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)
        )
        == "ES"
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_03_a_renamed_root_can_be_resolved_back(session, futures_root_by_code, vendor_symbology_by_code) -> None:
    """CSI sends "21" for crude oil; it no longer sends "CL" (shadowing), but ES is still "ES"."""
    resolve_csi_root = functools.partial(
        symbology_for_futures_root.resolve_target, session, vendor_symbology_by_code["CSI"]
    )

    assert (
        resolve_csi_root("21", as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)).futures_root_id
        == futures_root_by_code["CL"].futures_root_id
    )
    assert resolve_csi_root("CL", as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)) is None
    assert (
        resolve_csi_root("ES", as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)).futures_root_id
        == futures_root_by_code["ES"].futures_root_id
    )


# ----------------------------------------------------------------------------------------------------------------------
def test_04_contract_symbols_are_derived_from_root_symbol_and_template(
    session, clz6_future_contract, vendor_symbology_by_code
) -> None:
    """
    No symbol is stored for CLZ6 anywhere. Each vendor symbology supplies a template (inherited from FUT_COMMON unless
    overridden) and the root symbol comes from the same symbology, so CSI's "21" shows up in CSI's contract symbol.
    """
    get_contract_symbol = lambda symbology_code: symbology_for_instrument.get_future_symbol(
        session,
        vendor_symbology_by_code[symbology_code],
        clz6_future_contract,
        as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME),
    )

    assert get_contract_symbol("IBKR") == "CLZ6"  # inherited template
    assert get_contract_symbol("BBG") == "CLZ6 Comdty"  # own template
    assert get_contract_symbol("CSI") == "21_202612"  # own template, and CSI's symbol for the root


# ----------------------------------------------------------------------------------------------------------------------
def test_05_an_explicit_contract_symbol_beats_derivation(
    session, clz6_future_contract, vendor_symbology_by_code
) -> None:
    """When a vendor has an exception, store that one contract's symbol; other vendors are unaffected."""
    ibkr_symbology = vendor_symbology_by_code["IBKR"]
    bbg_symbology = vendor_symbology_by_code["BBG"]
    symbology_for_instrument.create_symbol(
        session, ibkr_symbology, BEGIN_DATE, FIRST_KNOWN_DATETIME, clz6_future_contract, "CL   DEC26"
    )

    assert (
        symbology_for_instrument.get_future_symbol(
            session, ibkr_symbology, clz6_future_contract, as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)
        )
        == "CL   DEC26"
    )
    assert (
        symbology_for_instrument.get_future_symbol(
            session, bbg_symbology, clz6_future_contract, as_of_module.AsOf(AS_OF_DATE, SECOND_KNOWN_DATETIME)
        )
        == "CLZ6 Comdty"
    )
