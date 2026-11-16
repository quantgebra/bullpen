from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_sec_master.futures_root as futures_root_module
import bullpen_sec_master.symbol_for_futures_root as symbol_for_futures_root
import bullpen_sec_master.symbology as symbology_module


# ----------------------------------------------------------------------------------------------------------------------
def create_symbol(
    session: orm.Session,
    symbology: symbology_module.Symbology,
    effective_from_date: date,
    known_at_datetime: datetime,
    futures_root: futures_root_module.FuturesRoot,
    symbol_text: str,
) -> symbol_for_futures_root.SymbolForFuturesRoot:
    """Record that, from effective_from_date onward, the futures_root is called symbol_text in this symbology."""
    created_row: symbol_for_futures_root.SymbolForFuturesRoot = symbol_for_futures_root.SymbolForFuturesRoot(
        symbology_id=symbology.id,
        futures_root_id=futures_root.futures_root_id,
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
    futures_root: futures_root_module.FuturesRoot,
    as_of: as_of_module.AsOf,
) -> Optional[str]:
    """The most specific explicit symbol in this symbology's chain, otherwise the root's own name (its canonical symbol)."""
    result: Optional[str] = None
    for chain_symbology in symbology.get_chain():
        if result is None:
            symbol_row: Optional[symbol_for_futures_root.SymbolForFuturesRoot] = find_symbol_row_by_target(
                session, chain_symbology, futures_root, as_of
            )
            if symbol_row is not None:
                result = symbol_row.symbol_text
    if result is None:
        canonical_root: Optional[futures_root_module.FuturesRoot] = futures_root_module.FuturesRoot.find_as_of(
            session, futures_root.futures_root_id, as_of
        )
        result = canonical_root.name if canonical_root is not None else None

    return result


# ----------------------------------------------------------------------------------------------------------------------
def resolve_target(
    session: orm.Session,
    symbology: symbology_module.Symbology,
    symbol_text: str,
    as_of: as_of_module.AsOf,
) -> Optional[futures_root_module.FuturesRoot]:
    """The target this symbol means in this symbology, or None; a symbol renamed by a descendant is shadowed."""
    result: Optional[futures_root_module.FuturesRoot] = None
    symbology_chain: list[symbology_module.Symbology] = symbology.get_chain()
    for depth, chain_symbology in enumerate(symbology_chain):
        if result is None:
            symbol_row: Optional[symbol_for_futures_root.SymbolForFuturesRoot] = _find_symbol_row_by_symbol_text(
                session, chain_symbology, symbol_text, as_of
            )
            if symbol_row is not None:
                named_futures_root: Optional[futures_root_module.FuturesRoot] = symbol_row.get_target(session)
                is_shadowed: bool = named_futures_root is not None and any(
                    find_symbol_row_by_target(session, more_specific_symbology, named_futures_root, as_of) is not None
                    for more_specific_symbology in symbology_chain[:depth]
                )
                if not is_shadowed:
                    result = named_futures_root
    if result is None:
        result = _resolve_canonical_target(session, symbology_chain, symbol_text, as_of)

    return result


# ----------------------------------------------------------------------------------------------------------------------
def find_symbol_row_by_target(
    session: orm.Session,
    owning_symbology: symbology_module.Symbology,
    futures_root: futures_root_module.FuturesRoot,
    as_of: as_of_module.AsOf,
) -> Optional[symbol_for_futures_root.SymbolForFuturesRoot]:
    """The symbol row for this futures_root in exactly this symbology (no inheritance, no canonical fallback)."""
    result: Optional[symbol_for_futures_root.SymbolForFuturesRoot] = (
        symbol_for_futures_root.SymbolForFuturesRoot.find_one_as_of(
            session,
            [
                symbol_for_futures_root.SymbolForFuturesRoot.symbology_id == owning_symbology.id,
                symbol_for_futures_root.SymbolForFuturesRoot.futures_root_id == futures_root.futures_root_id,
            ],
            as_of,
        )
    )
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _resolve_canonical_target(
    session: orm.Session,
    symbology_chain: list[symbology_module.Symbology],
    symbol_text: str,
    as_of: as_of_module.AsOf,
) -> Optional[futures_root_module.FuturesRoot]:
    """Inbound fallback: the root's own name is valid unless some symbology in the chain renames that root."""
    result: Optional[futures_root_module.FuturesRoot] = None
    candidate_root: Optional[futures_root_module.FuturesRoot] = futures_root_module.FuturesRoot.find_by_name_as_of(
        session, symbol_text, as_of
    )
    if candidate_root is not None:
        is_renamed: bool = any(
            find_symbol_row_by_target(session, chain_symbology, candidate_root, as_of) is not None
            for chain_symbology in symbology_chain
        )
        if not is_renamed:
            result = candidate_root
    return result


# ----------------------------------------------------------------------------------------------------------------------
def _find_symbol_row_by_symbol_text(
    session: orm.Session,
    owning_symbology: symbology_module.Symbology,
    symbol_text: str,
    as_of: as_of_module.AsOf,
) -> Optional[symbol_for_futures_root.SymbolForFuturesRoot]:
    """The symbol row giving this symbol in exactly this symbology (no inheritance, no canonical fallback)."""
    result: Optional[symbol_for_futures_root.SymbolForFuturesRoot] = (
        symbol_for_futures_root.SymbolForFuturesRoot.find_one_as_of(
            session,
            [
                symbol_for_futures_root.SymbolForFuturesRoot.symbology_id == owning_symbology.id,
                symbol_for_futures_root.SymbolForFuturesRoot.symbol_text == symbol_text,
            ],
            as_of,
        )
    )
    return result
