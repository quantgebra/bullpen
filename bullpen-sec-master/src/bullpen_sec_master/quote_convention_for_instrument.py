from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row
import bullpen_sec_master.base as base
import bullpen_sec_master.instrument as instrument_module
import bullpen_sec_master.symbology as symbology_module


# ======================================================================================================================
class QuoteConventionForInstrument(bitemporal_row.BitemporalRow, base.Base):
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
    # one fact per row, so a correction may re-date it before the row's own start
    MAY_SUPERSEDE_EARLIER_ROWS: bool = True

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
        result: Optional[QuoteConventionForInstrument] = QuoteConventionForInstrument.find_one_as_of(
            session,
            [
                QuoteConventionForInstrument.symbology_id == owning_symbology.id,
                QuoteConventionForInstrument.instrument_id == instrument_id,
            ],
            as_of,
        )
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    symbology_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.symbologies.id"), index=True)
    instrument_id: orm.Mapped[int] = orm.mapped_column(index=True)
    price_factor: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 10))
    quantity_factor: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 10))

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
        replacement_row: QuoteConventionForInstrument = self._create_replacement(
            session, effective_from_date, known_at_datetime
        )
        replacement_row.price_factor = price_factor
        replacement_row.quantity_factor = quantity_factor
        session.add(replacement_row)
        session.flush()
        return replacement_row

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        result: list[sqlalchemy.ColumnElement] = [
            QuoteConventionForInstrument.symbology_id == self.symbology_id,
            QuoteConventionForInstrument.instrument_id == self.instrument_id,
        ]
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "QuoteConventionForInstrument":
        result: QuoteConventionForInstrument = QuoteConventionForInstrument(
            symbology_id=self.symbology_id,
            instrument_id=self.instrument_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            price_factor=self.price_factor,
            quantity_factor=self.quantity_factor,
            command_id=self.command_id,
        )
        return result
