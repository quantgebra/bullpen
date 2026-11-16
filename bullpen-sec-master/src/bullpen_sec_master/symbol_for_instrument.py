from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row
import bullpen_sec_master.base as base
import bullpen_sec_master.instrument as instrument_module


# ======================================================================================================================
class SymbolForInstrument(bitemporal_row.BitemporalRow, base.Base):
    """
    symbology + symbol_text <-> instrument_id, bi-temporal (tickers change and get reused). Within one symbology, at any
    (effective_range, knowledge_range) a symbol_text names at most one target and a target has at most one symbol_text.
    instrument_id is a stable key, resolved as-of, so there is no FK.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "symbols_for_instruments"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("symbology_id", "="),
            ("symbol_text", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="symbols_for_instruments_symbol_unique",
            using="gist",
        ),
        postgresql.ExcludeConstraint(
            ("symbology_id", "="),
            ("instrument_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="symbols_for_instruments_target_unique",
            using="gist",
        ),
    )
    # one fact per row, so a correction may re-date it before the row's own start
    MAY_SUPERSEDE_EARLIER_ROWS: bool = True

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    symbology_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.symbologies.id"), index=True)
    instrument_id: orm.Mapped[int] = orm.mapped_column(index=True)
    symbol_text: orm.Mapped[str]

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_symbol_text(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, symbol_text: str
    ) -> "SymbolForInstrument":
        """From effective_from_date onward the target is called symbol_text; self becomes a superseded belief."""
        replacement_row: SymbolForInstrument = self._create_replacement(session, effective_from_date, known_at_datetime)
        replacement_row.symbol_text = symbol_text
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def get_target(self, session: orm.Session) -> Optional[instrument_module.Instrument]:
        """The named target as believed at the same AsOf this symbol row was read at."""
        result: Optional[instrument_module.Instrument] = instrument_module.Instrument.find_as_of(
            session, self.instrument_id, as_of_module.get_as_of_from_row(self)
        )
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        result: list[sqlalchemy.ColumnElement] = [
            SymbolForInstrument.symbology_id == self.symbology_id,
            SymbolForInstrument.instrument_id == self.instrument_id,
        ]
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "SymbolForInstrument":
        result: SymbolForInstrument = SymbolForInstrument(
            symbology_id=self.symbology_id,
            instrument_id=self.instrument_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            symbol_text=self.symbol_text,
            command_id=self.command_id,
        )
        return result
