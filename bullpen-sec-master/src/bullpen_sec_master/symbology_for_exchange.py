from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_sec_master.exchange as exchange_module
import bullpen_sec_master.symbol_for_exchange as symbol_for_exchange
import bullpen_sec_master.symbology as symbology_module


# ----------------------------------------------------------------------------------------------------------------------
def create_symbol(
    session: orm.Session,
    symbology: symbology_module.Symbology,
    effective_from_date: date,
    known_at_datetime: datetime,
    exchange: exchange_module.Exchange,
    symbol_text: str,
) -> symbol_for_exchange.SymbolForExchange:
    """Record that, from effective_from_date onward, the exchange is called symbol_text in this symbology."""
    created_row: symbol_for_exchange.SymbolForExchange = symbol_for_exchange.SymbolForExchange(
        symbology_id=symbology.id,
        exchange_id=exchange.id,
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
    exchange: exchange_module.Exchange,
    as_of: as_of_module.AsOf,
) -> str:
    """The most specific explicit symbol in this symbology's chain, otherwise the exchange's MIC."""
    result: Optional[str] = None
    for chain_symbology in symbology.get_chain():
        if result is None:
            symbol_row: Optional[symbol_for_exchange.SymbolForExchange] = find_symbol_row_by_target(
                session, chain_symbology, exchange, as_of
            )
            if symbol_row is not None:
                result = symbol_row.symbol_text
    if result is None:
        result = exchange.mic
    return result


# ----------------------------------------------------------------------------------------------------------------------
def resolve_target(
    session: orm.Session,
    symbology: symbology_module.Symbology,
    symbol_text: str,
    as_of: as_of_module.AsOf,
) -> Optional[exchange_module.Exchange]:
    """The exchange this symbol means in this symbology, or None; a symbol renamed by a descendant is shadowed."""
    result: Optional[exchange_module.Exchange] = None
    symbology_chain: list[symbology_module.Symbology] = symbology.get_chain()
    for depth, chain_symbology in enumerate(symbology_chain):
        if result is None:
            symbol_row: Optional[symbol_for_exchange.SymbolForExchange] = _find_symbol_row_by_symbol_text(
                session, chain_symbology, symbol_text, as_of
            )
            if symbol_row is not None:
                named_exchange: exchange_module.Exchange = symbol_row.get_target(session)
                is_shadowed: bool = any(
                    find_symbol_row_by_target(session, more_specific_symbology, named_exchange, as_of) is not None
                    for more_specific_symbology in symbology_chain[:depth]
                )
                if not is_shadowed:
                    result = named_exchange
    if result is None:
        result = _resolve_canonical_target(session, symbology_chain, symbol_text, as_of)
    return result


# ----------------------------------------------------------------------------------------------------------------------
def find_symbol_row_by_target(
    session: orm.Session,
    owning_symbology: symbology_module.Symbology,
    exchange: exchange_module.Exchange,
    as_of: as_of_module.AsOf,
) -> Optional[symbol_for_exchange.SymbolForExchange]:
    """The symbol row for this exchange in exactly this symbology (no inheritance, no canonical fallback)."""
    result: Optional[symbol_for_exchange.SymbolForExchange] = symbol_for_exchange.SymbolForExchange.find_one_as_of(
        session,
        [
            symbol_for_exchange.SymbolForExchange.symbology_id == owning_symbology.id,
            symbol_for_exchange.SymbolForExchange.exchange_id == exchange.id,
        ],
        as_of,
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _resolve_canonical_target(
    session: orm.Session,
    symbology_chain: list[symbology_module.Symbology],
    symbol_text: str,
    as_of: as_of_module.AsOf,
) -> Optional[exchange_module.Exchange]:
    """Inbound fallback: the MIC is valid unless some symbology in the chain renames that exchange."""
    result: Optional[exchange_module.Exchange] = None
    select_statement: sqlalchemy.Select = sqlalchemy.select(exchange_module.Exchange).where(
        exchange_module.Exchange.mic == symbol_text
    )
    candidate_exchange: Optional[exchange_module.Exchange] = session.scalars(select_statement).first()
    if candidate_exchange is not None:
        is_renamed: bool = any(
            find_symbol_row_by_target(session, chain_symbology, candidate_exchange, as_of) is not None
            for chain_symbology in symbology_chain
        )
        if not is_renamed:
            result = candidate_exchange
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _find_symbol_row_by_symbol_text(
    session: orm.Session,
    owning_symbology: symbology_module.Symbology,
    symbol_text: str,
    as_of: as_of_module.AsOf,
) -> Optional[symbol_for_exchange.SymbolForExchange]:
    """The symbol row giving this symbol in exactly this symbology (no inheritance, no canonical fallback)."""
    result: Optional[symbol_for_exchange.SymbolForExchange] = symbol_for_exchange.SymbolForExchange.find_one_as_of(
        session,
        [
            symbol_for_exchange.SymbolForExchange.symbology_id == owning_symbology.id,
            symbol_for_exchange.SymbolForExchange.symbol_text == symbol_text,
        ],
        as_of,
    )
    return result
