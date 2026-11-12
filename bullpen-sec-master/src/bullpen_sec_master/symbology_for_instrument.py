from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.as_of as as_of_module
import bullpen_sec_master.futures_root as futures_root
import bullpen_sec_master.instrument as instrument_module
import bullpen_sec_master.instrument_futures_contract as instrument_futures_contract
import bullpen_sec_master.symbol_for_instrument as symbol_for_instrument
import bullpen_sec_master.symbology as symbology_module
import bullpen_sec_master.symbology_for_futures_root as symbology_for_futures_root

MONTH_CODES: str = "FGHJKMNQUVXZ"


# ----------------------------------------------------------------------------------------------------------------------
def create_symbol(
    session: orm.Session,
    symbology: symbology_module.Symbology,
    effective_from_date: date,
    known_at_datetime: datetime,
    instrument: instrument_module.Instrument,
    symbol_text: str,
) -> symbol_for_instrument.SymbolForInstrument:
    """Record that, from effective_from_date onward, the instrument is called symbol_text in this symbology."""
    created_row: symbol_for_instrument.SymbolForInstrument = symbol_for_instrument.SymbolForInstrument(
        symbology_id=symbology.id,
        instrument_id=instrument.instrument_id,
        effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
        knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
        symbol_text=symbol_text,
    )
    session.add(created_row)
    session.flush()
    return created_row


# ----------------------------------------------------------------------------------------------------------------------
def get_symbol(
    session: orm.Session,
    symbology: symbology_module.Symbology,
    instrument: instrument_module.Instrument,
    as_of: as_of_module.AsOf,
) -> Optional[str]:
    """The most specific explicit symbol in this symbology's chain, otherwise None (instruments have no canonical symbol)."""
    result: Optional[str] = None
    for chain_symbology in symbology.get_chain():
        if result is None:
            symbol_row: Optional[symbol_for_instrument.SymbolForInstrument] = find_symbol_row_by_target(
                session, chain_symbology, instrument, as_of
            )
            if symbol_row is not None:
                result = symbol_row.symbol_text

    return result


# ----------------------------------------------------------------------------------------------------------------------
def resolve_target(
    session: orm.Session,
    symbology: symbology_module.Symbology,
    symbol_text: str,
    as_of: as_of_module.AsOf,
) -> Optional[instrument_module.Instrument]:
    """The target this symbol means in this symbology, or None; a symbol renamed by a descendant is shadowed."""
    result: Optional[instrument_module.Instrument] = None
    symbology_chain: list[symbology_module.Symbology] = symbology.get_chain()
    for depth, chain_symbology in enumerate(symbology_chain):
        if result is None:
            symbol_row: Optional[symbol_for_instrument.SymbolForInstrument] = _find_symbol_row_by_symbol_text(
                session, chain_symbology, symbol_text, as_of
            )
            if symbol_row is not None:
                named_instrument: Optional[instrument_module.Instrument] = symbol_row.get_target(session)
                is_shadowed: bool = named_instrument is not None and any(
                    find_symbol_row_by_target(session, more_specific_symbology, named_instrument, as_of) is not None
                    for more_specific_symbology in symbology_chain[:depth]
                )
                if not is_shadowed:
                    result = named_instrument

    return result


# ----------------------------------------------------------------------------------------------------------------------
def get_future_symbol(
    session: orm.Session,
    symbology: symbology_module.Symbology,
    future_contract: instrument_futures_contract.InstrumentFuturesContract,
    as_of: as_of_module.AsOf,
) -> Optional[str]:
    """Explicit contract symbol if one is stored; otherwise root symbol (in this symbology) + contract template."""
    result: Optional[str] = get_symbol(session, symbology, future_contract, as_of)
    if result is None:
        result = _derive_future_symbol(session, symbology, future_contract, as_of)
    return result


# ----------------------------------------------------------------------------------------------------------------------
def find_symbol_row_by_target(
    session: orm.Session,
    owning_symbology: symbology_module.Symbology,
    instrument: instrument_module.Instrument,
    as_of: as_of_module.AsOf,
) -> Optional[symbol_for_instrument.SymbolForInstrument]:
    """The symbol row for this instrument in exactly this symbology (no inheritance, no canonical fallback)."""
    select_statement: sqlalchemy.Select = sqlalchemy.select(symbol_for_instrument.SymbolForInstrument).where(
        symbol_for_instrument.SymbolForInstrument.symbology_id == owning_symbology.id,
        symbol_for_instrument.SymbolForInstrument.instrument_id == instrument.instrument_id,
        symbol_for_instrument.SymbolForInstrument.effective_range.contains(as_of.effective_date),
        symbol_for_instrument.SymbolForInstrument.knowledge_range.contains(as_of.known_at_datetime),
    )
    result: Optional[symbol_for_instrument.SymbolForInstrument] = session.scalars(
        select_statement, execution_options={"identity_token": as_of}
    ).first()
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _derive_future_symbol(
    session: orm.Session,
    symbology: symbology_module.Symbology,
    future_contract: instrument_futures_contract.InstrumentFuturesContract,
    as_of: as_of_module.AsOf,
) -> Optional[str]:
    result: Optional[str] = None
    contract_template: Optional[str] = next(
        (
            chain_symbology.contract_template
            for chain_symbology in symbology.get_chain()
            if chain_symbology.contract_template
        ),
        None,
    )
    contract_root: Optional[futures_root.FuturesRoot] = futures_root.FuturesRoot.find_as_of(
        session, future_contract.futures_root_id, as_of
    )
    if contract_template is not None and contract_root is not None:
        root_name: Optional[str] = symbology_for_futures_root.get_symbol(session, symbology, contract_root, as_of)
        if root_name is not None:
            contract_month_date: date = future_contract.contract_month
            result = contract_template.format(
                root=root_name,
                month_code=MONTH_CODES[contract_month_date.month - 1],
                y=contract_month_date.year % 10,
                yy=contract_month_date.year % 100,
                yyyymm=f"{contract_month_date.year:04d}{contract_month_date.month:02d}",
            )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _find_symbol_row_by_symbol_text(
    session: orm.Session,
    owning_symbology: symbology_module.Symbology,
    symbol_text: str,
    as_of: as_of_module.AsOf,
) -> Optional[symbol_for_instrument.SymbolForInstrument]:
    """The symbol row giving this symbol in exactly this symbology (no inheritance, no canonical fallback)."""
    select_statement: sqlalchemy.Select = sqlalchemy.select(symbol_for_instrument.SymbolForInstrument).where(
        symbol_for_instrument.SymbolForInstrument.symbology_id == owning_symbology.id,
        symbol_for_instrument.SymbolForInstrument.symbol_text == symbol_text,
        symbol_for_instrument.SymbolForInstrument.effective_range.contains(as_of.effective_date),
        symbol_for_instrument.SymbolForInstrument.knowledge_range.contains(as_of.known_at_datetime),
    )
    result: Optional[symbol_for_instrument.SymbolForInstrument] = session.scalars(
        select_statement, execution_options={"identity_token": as_of}
    ).first()
    return result
