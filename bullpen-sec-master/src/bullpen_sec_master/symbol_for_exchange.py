from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.base as base
import bullpen_sec_master.exchange as exchange_module


# ======================================================================================================================
class SymbolForExchange(base.Base):
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

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _close_believed_rows(
        session: orm.Session,
        symbology_id: int,
        exchange_id: int,
        effective_from_date: date,
        known_at_datetime: datetime,
    ) -> list["SymbolForExchange"]:
        """Close the knowledge_range of every believed row from effective_from_date onward; keep their earlier part."""
        open_ended_date_range: postgresql.Range[date] = postgresql.Range(effective_from_date, None, bounds="[)")
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(SymbolForExchange)
            .where(
                SymbolForExchange.symbology_id == symbology_id,
                SymbolForExchange.exchange_id == exchange_id,
                sqlalchemy.func.upper_inf(SymbolForExchange.knowledge_range),
                SymbolForExchange.effective_range.overlaps(open_ended_date_range),
            )
            .order_by(sqlalchemy.func.lower(SymbolForExchange.effective_range))
        )
        closed_rows: list[SymbolForExchange] = list(session.scalars(select_statement).all())
        for closed_row in closed_rows:
            old_effective_range: postgresql.Range[date] = closed_row.effective_range
            closed_row.knowledge_range = postgresql.Range(
                closed_row.knowledge_range.lower, known_at_datetime, bounds="[)"
            )
            session.flush()
            if old_effective_range.lower < effective_from_date:
                session.add(
                    SymbolForExchange(
                        symbology_id=closed_row.symbology_id,
                        exchange_id=closed_row.exchange_id,
                        effective_range=postgresql.Range(old_effective_range.lower, effective_from_date, bounds="[)"),
                        knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
                        symbol_text=closed_row.symbol_text,
                        command_id=closed_row.command_id,
                    )
                )
        return closed_rows

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    row_id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    symbology_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.symbologies.id"), index=True)
    exchange_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.exchanges.id"), index=True)
    effective_range: orm.Mapped[postgresql.Range[date]] = orm.mapped_column(postgresql.DATERANGE)
    knowledge_range: orm.Mapped[postgresql.Range[datetime]] = orm.mapped_column(postgresql.TSTZRANGE)
    symbol_text: orm.Mapped[str]
    command_id: orm.Mapped[Optional[int]]

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_symbol_text(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, symbol_text: str
    ) -> "SymbolForExchange":
        """From effective_from_date onward the exchange is called symbol_text; self becomes a superseded belief."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"symbol row {self.row_id} is no longer believed")
        SymbolForExchange._close_believed_rows(
            session, self.symbology_id, self.exchange_id, effective_from_date, known_at_datetime
        )
        replacement_row: SymbolForExchange = SymbolForExchange(
            symbology_id=self.symbology_id,
            exchange_id=self.exchange_id,
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            symbol_text=symbol_text,
        )
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def end(self, session: orm.Session, effective_to_date: date, known_at_datetime: datetime) -> None:
        """From effective_to_date onward the exchange has no symbol in this symbology."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"symbol row {self.row_id} is no longer believed")
        SymbolForExchange._close_believed_rows(
            session, self.symbology_id, self.exchange_id, effective_to_date, known_at_datetime
        )
        session.flush()

    # ------------------------------------------------------------------------------------------------------------------
    def get_target(self, session: orm.Session) -> exchange_module.Exchange:
        """The exchange is a plain row with one identity in the session, whatever AsOf this symbol row was read at."""
        result: exchange_module.Exchange = session.get(exchange_module.Exchange, self.exchange_id)
        return result
