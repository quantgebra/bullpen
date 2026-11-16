from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.base as base
import bullpen_temporal.bitemporal_row as bitemporal_row
import bullpen_sec_master.exchange as exchange_module


# ======================================================================================================================
class SymbolForExchange(bitemporal_row.BitemporalRow, base.Base):
    """
    symbology + symbol_text <-> exchange, bi-temporal (an exchange's vendor symbol can change). Exchanges themselves
    are plain rows, so exchange_id is a real FK. Within one symbology, at any (effective_range, knowledge_range) a
    symbol_text names at most one exchange and an exchange has at most one symbol_text.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "symbols_for_exchanges"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("symbology_id", "="),
            ("symbol_text", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="symbols_for_exchanges_symbol_unique",
            using="gist",
        ),
        postgresql.ExcludeConstraint(
            ("symbology_id", "="),
            ("exchange_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="symbols_for_exchanges_target_unique",
            using="gist",
        ),
    )
    # one fact per row, so a correction may re-date it before the row's own start
    MAY_SUPERSEDE_EARLIER_ROWS: bool = True

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    symbology_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.symbologies.id"), index=True)
    exchange_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.exchanges.id"), index=True)
    symbol_text: orm.Mapped[str]

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_symbol_text(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, symbol_text: str
    ) -> "SymbolForExchange":
        """From effective_from_date onward the exchange is called symbol_text; self becomes a superseded belief."""
        replacement_row: SymbolForExchange = self._create_replacement(session, effective_from_date, known_at_datetime)
        replacement_row.symbol_text = symbol_text
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def get_target(self, session: orm.Session) -> exchange_module.Exchange:
        """The exchange is a plain row with one identity in the session, whatever AsOf this symbol row was read at."""
        result: exchange_module.Exchange = session.get(exchange_module.Exchange, self.exchange_id)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        result: list[sqlalchemy.ColumnElement] = [
            SymbolForExchange.symbology_id == self.symbology_id,
            SymbolForExchange.exchange_id == self.exchange_id,
        ]
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "SymbolForExchange":
        result: SymbolForExchange = SymbolForExchange(
            symbology_id=self.symbology_id,
            exchange_id=self.exchange_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            symbol_text=self.symbol_text,
            command_id=self.command_id,
        )
        return result
