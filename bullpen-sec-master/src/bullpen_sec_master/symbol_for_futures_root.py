from datetime import date, datetime
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.as_of as as_of_module
import bullpen_sec_master.base as base
import bullpen_sec_master.futures_root as futures_root_module


# ======================================================================================================================
class SymbolForFuturesRoot(base.Base):
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

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _close_believed_rows(
        session: orm.Session,
        symbology_id: int,
        futures_root_id: int,
        effective_from_date: date,
        known_at_datetime: datetime,
    ) -> list["SymbolForFuturesRoot"]:
        """Close the knowledge_range of every believed row from effective_from_date onward; keep their earlier part."""
        open_ended_date_range: postgresql.Range[date] = postgresql.Range(effective_from_date, None, bounds="[)")
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(SymbolForFuturesRoot)
            .where(
                SymbolForFuturesRoot.symbology_id == symbology_id,
                SymbolForFuturesRoot.futures_root_id == futures_root_id,
                sqlalchemy.func.upper_inf(SymbolForFuturesRoot.knowledge_range),
                SymbolForFuturesRoot.effective_range.overlaps(open_ended_date_range),
            )
            .order_by(sqlalchemy.func.lower(SymbolForFuturesRoot.effective_range))
        )
        closed_rows: list[SymbolForFuturesRoot] = list(session.scalars(select_statement).all())
        for closed_row in closed_rows:
            old_effective_range: postgresql.Range[date] = closed_row.effective_range
            closed_row.knowledge_range = postgresql.Range(
                closed_row.knowledge_range.lower, known_at_datetime, bounds="[)"
            )
            session.flush()
            if old_effective_range.lower < effective_from_date:
                session.add(
                    SymbolForFuturesRoot(
                        symbology_id=closed_row.symbology_id,
                        futures_root_id=closed_row.futures_root_id,
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
    futures_root_id: orm.Mapped[int] = orm.mapped_column(index=True)
    effective_range: orm.Mapped[postgresql.Range[date]] = orm.mapped_column(postgresql.DATERANGE)
    knowledge_range: orm.Mapped[postgresql.Range[datetime]] = orm.mapped_column(postgresql.TSTZRANGE)
    symbol_text: orm.Mapped[str]
    command_id: orm.Mapped[Optional[int]]

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_symbol_text(
        self, session: orm.Session, effective_from_date: date, known_at_datetime: datetime, symbol_text: str
    ) -> "SymbolForFuturesRoot":
        """From effective_from_date onward the target is called symbol_text; self becomes a superseded belief."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"symbol row {self.row_id} is no longer believed")
        SymbolForFuturesRoot._close_believed_rows(
            session, self.symbology_id, self.futures_root_id, effective_from_date, known_at_datetime
        )
        replacement_row: SymbolForFuturesRoot = SymbolForFuturesRoot(
            symbology_id=self.symbology_id,
            futures_root_id=self.futures_root_id,
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            symbol_text=symbol_text,
        )
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def end(self, session: orm.Session, effective_to_date: date, known_at_datetime: datetime) -> None:
        """From effective_to_date onward the target has no symbol in this symbology (e.g. a ticker retired)."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"symbol row {self.row_id} is no longer believed")
        SymbolForFuturesRoot._close_believed_rows(
            session, self.symbology_id, self.futures_root_id, effective_to_date, known_at_datetime
        )
        session.flush()

    # ------------------------------------------------------------------------------------------------------------------
    def get_target(self, session: orm.Session) -> Optional[futures_root_module.FuturesRoot]:
        """The named target as believed at the same AsOf this symbol row was read at."""
        result: Optional[futures_root_module.FuturesRoot] = futures_root_module.FuturesRoot.find_as_of(
            session, self.futures_root_id, as_of_module.get_as_of_from_row(self)
        )
        return result
