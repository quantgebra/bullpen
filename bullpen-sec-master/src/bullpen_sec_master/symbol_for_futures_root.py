from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row
import bullpen_sec_master.base as base
import bullpen_sec_master.futures_root as futures_root_module


# ======================================================================================================================
class SymbolForFuturesRoot(bitemporal_row.BitemporalRow, base.Base):
    """
    symbology + symbol_text <-> futures_root_id, bi-temporal (tickers change and get reused). Within one symbology, at any
    (effective_range, knowledge_range) a symbol_text names at most one target and a target has at most one symbol_text.
    futures_root_id is a stable key, resolved as-of, so there is no FK.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "symbols_for_futures_roots"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("symbology_id", "="),
            ("symbol_text", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="symbols_for_futures_roots_symbol_unique",
            using="gist",
        ),
        postgresql.ExcludeConstraint(
            ("symbology_id", "="),
            ("futures_root_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="symbols_for_futures_roots_target_unique",
            using="gist",
        ),
    )
    # one fact per row, so a correction may re-date it before the row's own start
    MAY_SUPERSEDE_EARLIER_ROWS: bool = True

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    symbology_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.symbologies.id"), index=True)
    futures_root_id: orm.Mapped[int] = orm.mapped_column(index=True)
    symbol_text: orm.Mapped[str]

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_symbol_text(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, symbol_text: str
    ) -> "SymbolForFuturesRoot":
        """From effective_from_date onward the target is called symbol_text; self becomes a superseded belief."""
        replacement_row: SymbolForFuturesRoot = self._create_replacement(
            session, effective_from_date, known_at_datetime
        )
        replacement_row.symbol_text = symbol_text
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def get_target(self, session: orm.Session) -> Optional[futures_root_module.FuturesRoot]:
        """The named target as believed at the same AsOf this symbol row was read at."""
        result: Optional[futures_root_module.FuturesRoot] = futures_root_module.FuturesRoot.find_as_of(
            session, self.futures_root_id, as_of_module.get_as_of_from_row(self)
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        result: list[sqlalchemy.ColumnElement] = [
            SymbolForFuturesRoot.symbology_id == self.symbology_id,
            SymbolForFuturesRoot.futures_root_id == self.futures_root_id,
        ]
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "SymbolForFuturesRoot":
        result: SymbolForFuturesRoot = SymbolForFuturesRoot(
            symbology_id=self.symbology_id,
            futures_root_id=self.futures_root_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            symbol_text=self.symbol_text,
            command_id=self.command_id,
        )
        return result
