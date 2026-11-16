from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_sec_master.as_of as as_of_module
import bullpen_sec_master.base as base
import bullpen_sec_master.instrument as instrument_module
import bullpen_sec_master.symbology as symbology_module


# ======================================================================================================================
class QuoteConventionForInstrument(base.Base):
    """
    How one symbology quotes one instrument, e.g. a vendor quoting a London-listed equity in pence while bullpen's
    canonical price is in pounds. For a futures contract it overrides the root's convention in the same symbology.
    Factors and bi-temporality are as in QuoteConventionForFuturesRoot.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "quote_conventions_for_instruments"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("symbology_id", "="),
            ("instrument_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="quote_conventions_for_instruments_no_overlap",
            using="gist",
        ),
        sqlalchemy.CheckConstraint(
            "price_factor > 0 AND quantity_factor > 0", name="quote_conventions_for_instruments_factors_positive"
        ),
    )

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def create(
        session: orm.Session,
        symbology: symbology_module.Symbology,
        effective_from_date: date,
        known_at_datetime: datetime,
        instrument: instrument_module.Instrument,
        price_factor: Decimal,
        quantity_factor: Decimal,
    ) -> "QuoteConventionForInstrument":
        """From effective_from_date onward, this symbology quotes the instrument with these factors."""
        created_row: QuoteConventionForInstrument = QuoteConventionForInstrument(
            symbology_id=symbology.id,
            instrument_id=instrument.instrument_id,
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            price_factor=price_factor,
            quantity_factor=quantity_factor,
        )
        session.add(created_row)
        session.flush()
        return created_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def find_as_of(
        session: orm.Session,
        owning_symbology: symbology_module.Symbology,
        instrument_id: int,
        as_of: as_of_module.AsOf,
    ) -> Optional["QuoteConventionForInstrument"]:
        """The convention stored in exactly this symbology (no inheritance)."""
        select_statement: sqlalchemy.Select = sqlalchemy.select(QuoteConventionForInstrument).where(
            QuoteConventionForInstrument.symbology_id == owning_symbology.id,
            QuoteConventionForInstrument.instrument_id == instrument_id,
            QuoteConventionForInstrument.effective_range.contains(as_of.effective_date),
            QuoteConventionForInstrument.knowledge_range.contains(as_of.known_at_datetime),
        )
        result: Optional[QuoteConventionForInstrument] = session.scalars(
            select_statement, execution_options={"identity_token": as_of}
        ).first()
        return result

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _close_believed_rows(
        session: orm.Session,
        symbology_id: int,
        instrument_id: int,
        effective_from_date: date,
        known_at_datetime: datetime,
    ) -> list["QuoteConventionForInstrument"]:
        """Close the knowledge_range of every believed row from effective_from_date onward; keep their earlier part."""
        open_ended_date_range: postgresql.Range[date] = postgresql.Range(effective_from_date, None, bounds="[)")
        select_statement: sqlalchemy.Select = (
            sqlalchemy.select(QuoteConventionForInstrument)
            .where(
                QuoteConventionForInstrument.symbology_id == symbology_id,
                QuoteConventionForInstrument.instrument_id == instrument_id,
                sqlalchemy.func.upper_inf(QuoteConventionForInstrument.knowledge_range),
                QuoteConventionForInstrument.effective_range.overlaps(open_ended_date_range),
            )
            .order_by(sqlalchemy.func.lower(QuoteConventionForInstrument.effective_range))
        )
        closed_rows: list[QuoteConventionForInstrument] = list(session.scalars(select_statement).all())
        for closed_row in closed_rows:
            old_effective_range: postgresql.Range[date] = closed_row.effective_range
            closed_row.knowledge_range = postgresql.Range(
                closed_row.knowledge_range.lower, known_at_datetime, bounds="[)"
            )
            session.flush()
            if old_effective_range.lower < effective_from_date:
                session.add(
                    QuoteConventionForInstrument(
                        symbology_id=closed_row.symbology_id,
                        instrument_id=closed_row.instrument_id,
                        effective_range=postgresql.Range(old_effective_range.lower, effective_from_date, bounds="[)"),
                        knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
                        price_factor=closed_row.price_factor,
                        quantity_factor=closed_row.quantity_factor,
                        command_id=closed_row.command_id,
                    )
                )
        return closed_rows

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    row_id: orm.Mapped[int] = orm.mapped_column(primary_key=True)
    symbology_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.symbologies.id"), index=True)
    instrument_id: orm.Mapped[int] = orm.mapped_column(index=True)
    effective_range: orm.Mapped[postgresql.Range[date]] = orm.mapped_column(postgresql.DATERANGE)
    knowledge_range: orm.Mapped[postgresql.Range[datetime]] = orm.mapped_column(postgresql.TSTZRANGE)
    price_factor: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    quantity_factor: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    command_id: orm.Mapped[Optional[int]]

    # ------------------------------------------------------------------------------------------------------------------
    def with_new_factors(
        self,
        session: orm.Session,
        effective_from_date: date,
        known_at_datetime: datetime,
        price_factor: Decimal,
        quantity_factor: Decimal,
    ) -> "QuoteConventionForInstrument":
        """From effective_from_date onward the symbology quotes with new factors; self becomes a superseded belief."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"quote convention row {self.row_id} is no longer believed")
        QuoteConventionForInstrument._close_believed_rows(
            session, self.symbology_id, self.instrument_id, effective_from_date, known_at_datetime
        )
        replacement_row: QuoteConventionForInstrument = QuoteConventionForInstrument(
            symbology_id=self.symbology_id,
            instrument_id=self.instrument_id,
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            price_factor=price_factor,
            quantity_factor=quantity_factor,
        )
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def end(self, session: orm.Session, effective_to_date: date, known_at_datetime: datetime) -> None:
        """From effective_to_date onward this symbology falls back to its parent's convention (or canonical)."""
        if self.knowledge_range.upper is not None:
            raise ValueError(f"quote convention row {self.row_id} is no longer believed")
        QuoteConventionForInstrument._close_believed_rows(
            session, self.symbology_id, self.instrument_id, effective_to_date, known_at_datetime
        )
        session.flush()
