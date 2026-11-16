from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row
import bullpen_sec_master.base as base
import bullpen_sec_master.futures_root as futures_root_module
import bullpen_sec_master.symbology as symbology_module


# ======================================================================================================================
class QuoteConventionForFuturesRoot(bitemporal_row.BitemporalRow, base.Base):
    """
    How one symbology quotes every contract of a futures root, e.g. a vendor quoting corn in cents per bushel while
    bullpen's canonical price is in dollars. vendor price * price_factor = canonical price, vendor quantity *
    quantity_factor = canonical quantity. Bi-temporal: vendors change conventions and we learn of it late.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "quote_conventions_for_futures_roots"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("symbology_id", "="),
            ("futures_root_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="quote_conventions_for_futures_roots_no_overlap",
            using="gist",
        ),
        sqlalchemy.CheckConstraint(
            "price_factor > 0 AND quantity_factor > 0", name="quote_conventions_for_futures_roots_factors_positive"
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
        futures_root: futures_root_module.FuturesRoot,
        price_factor: Decimal,
        quantity_factor: Decimal,
    ) -> "QuoteConventionForFuturesRoot":
        """From effective_from_date onward, this symbology quotes the root's contracts with these factors."""
        created_row: QuoteConventionForFuturesRoot = QuoteConventionForFuturesRoot(
            symbology_id=symbology.id,
            futures_root_id=futures_root.futures_root_id,
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
        futures_root_id: int,
        as_of: as_of_module.AsOf,
    ) -> Optional["QuoteConventionForFuturesRoot"]:
        """The convention stored in exactly this symbology (no inheritance)."""
        result: Optional[QuoteConventionForFuturesRoot] = QuoteConventionForFuturesRoot.find_one_as_of(
            session,
            [
                QuoteConventionForFuturesRoot.symbology_id == owning_symbology.id,
                QuoteConventionForFuturesRoot.futures_root_id == futures_root_id,
            ],
            as_of,
        )
        return result

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    symbology_id: orm.Mapped[int] = orm.mapped_column(sqlalchemy.ForeignKey("sec_master.symbologies.id"), index=True)
    futures_root_id: orm.Mapped[int] = orm.mapped_column(index=True)
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
    ) -> "QuoteConventionForFuturesRoot":
        """From effective_from_date onward the symbology quotes with new factors; self becomes a superseded belief."""
        replacement_row: QuoteConventionForFuturesRoot = self._create_replacement(
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
            QuoteConventionForFuturesRoot.symbology_id == self.symbology_id,
            QuoteConventionForFuturesRoot.futures_root_id == self.futures_root_id,
        ]
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "QuoteConventionForFuturesRoot":
        result: QuoteConventionForFuturesRoot = QuoteConventionForFuturesRoot(
            symbology_id=self.symbology_id,
            futures_root_id=self.futures_root_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            price_factor=self.price_factor,
            quantity_factor=self.quantity_factor,
            command_id=self.command_id,
        )
        return result
