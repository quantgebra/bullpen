from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple, Optional

import sqlalchemy
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

import bullpen_temporal.as_of as as_of_module
import bullpen_temporal.bitemporal_row as bitemporal_row
import bullpen_sec_master.base as base
import bullpen_sec_master.instrument as instrument
import bullpen_sec_master.instrument_type as instrument_types


# ======================================================================================================================
class LegWeight(NamedTuple):

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    instrument: instrument.Instrument
    weight: Decimal


# ======================================================================================================================
class InstrumentSyntheticLeg(bitemporal_row.BitemporalRow, base.Base):
    """
    One leg of a synthetic: from effective_range, as believed during knowledge_range, the synthetic holds weight of the
    leg instrument. Both ids are stable keys resolved as-of, so there are no FKs. Only manipulated through its synthetic.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __tablename__ = "instrument_synthetic_legs"
    __table_args__ = (
        postgresql.ExcludeConstraint(
            ("synthetic_instrument_id", "="),
            ("leg_instrument_id", "="),
            ("effective_range", "&&"),
            ("knowledge_range", "&&"),
            name="instrument_synthetic_legs_no_overlap",
            using="gist",
        ),
    )

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    synthetic_instrument_id: orm.Mapped[int] = orm.mapped_column(index=True)
    leg_instrument_id: orm.Mapped[int] = orm.mapped_column(index=True)
    weight: orm.Mapped[Decimal] = orm.mapped_column(sqlalchemy.Numeric(28, 12))

    # ------------------------------------------------------------------------------------------------------------------
    def _get_same_entity_conditions(self) -> list[sqlalchemy.ColumnElement]:
        """The legs of one synthetic change together, so the entity is the whole leg set."""
        result: list[sqlalchemy.ColumnElement] = [
            InstrumentSyntheticLeg.synthetic_instrument_id == self.synthetic_instrument_id
        ]
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "InstrumentSyntheticLeg":
        result: InstrumentSyntheticLeg = InstrumentSyntheticLeg(
            synthetic_instrument_id=self.synthetic_instrument_id,
            leg_instrument_id=self.leg_instrument_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            weight=self.weight,
            command_id=self.command_id,
        )
        return result


# ======================================================================================================================
class InstrumentSynthetic(instrument.Instrument):
    """
    What research holds but no broker can: a weighted set of real instruments (legs), such as a mandate's "CL" (one
    leg, rolled by the external system), VIX30 (two legs re-weighted daily) or a calendar spread (two fixed legs).
    The legs are bi-temporal and stored as received; nothing here computes them. A leg is never itself synthetic, so
    decomposing a portfolio security into traded securities is always one step.
    """

    # ==================================================================================================================
    # CLASS
    # ==================================================================================================================
    __mapper_args__ = {"polymorphic_identity": instrument_types.InstrumentType.SYNTHETIC}

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def create(
        session: orm.Session,
        effective_from_date: date,
        known_at_datetime: datetime,
        name: str,
        currency_code: str,
        legs: list[LegWeight],
    ) -> "InstrumentSynthetic":
        """A new synthetic with a new instrument_id, holding legs from effective_from_date onward."""
        InstrumentSynthetic._check_legs(legs)

        # the synthetic first, so its instrument_id exists for the leg rows
        created_row: InstrumentSynthetic = InstrumentSynthetic(
            effective_range=postgresql.Range(effective_from_date, None, bounds="[)"),
            knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
            name=name,
            currency_code=currency_code,
        )
        session.add(created_row)
        session.flush()

        created_row._insert_legs(session, effective_from_date, None, known_at_datetime, legs)
        return created_row

    # ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _check_legs(legs: list[LegWeight]) -> None:
        for leg in legs:
            if isinstance(leg.instrument, InstrumentSynthetic):
                raise ValueError(
                    f"leg {leg.instrument.name} is synthetic; a synthetic's legs must be traded instruments"
                )

    # ==================================================================================================================
    # INSTANCE
    # ==================================================================================================================
    # ------------------------------------------------------------------------------------------------------------------
    def with_new_legs(
        self,
        session: orm.Session,
        effective_from_date: date,
        known_at_datetime: datetime,
        legs: list[LegWeight],
        effective_to_date: Optional[date] = None,
    ) -> None:
        """
        From effective_from_date (until effective_to_date, or onward if None) the synthetic holds exactly these legs;
        legs believed for those dates become superseded beliefs. An empty list means the synthetic holds nothing then.
        """
        InstrumentSynthetic._check_legs(legs)
        if effective_to_date is not None:
            if effective_to_date <= effective_from_date:
                raise ValueError(f"empty window {effective_from_date} .. {effective_to_date}")

        InstrumentSyntheticLeg._close_believed_rows(
            session,
            [InstrumentSyntheticLeg.synthetic_instrument_id == self.instrument_id],
            effective_from_date,
            effective_to_date,
            known_at_datetime,
        )
        self._insert_legs(session, effective_from_date, effective_to_date, known_at_datetime, legs)

    # ------------------------------------------------------------------------------------------------------------------
    def get_legs(self, session: orm.Session) -> list[LegWeight]:
        """The legs held as of the AsOf this synthetic was read at, each leg instrument read at that same AsOf."""
        row_as_of: as_of_module.AsOf = as_of_module.get_as_of_from_row(self)
        leg_rows: list[InstrumentSyntheticLeg] = InstrumentSyntheticLeg.find_all_as_of(
            session,
            [InstrumentSyntheticLeg.synthetic_instrument_id == self.instrument_id],
            row_as_of,
            InstrumentSyntheticLeg.leg_instrument_id,
        )

        result: list[LegWeight] = []
        for leg_row in leg_rows:
            leg_instrument: Optional[instrument.Instrument] = instrument.Instrument.find_as_of(
                session, leg_row.leg_instrument_id, row_as_of
            )
            leg: LegWeight = LegWeight(leg_instrument, leg_row.weight)
            result.append(leg)
        return result

    # ------------------------------------------------------------------------------------------------------------------
    def _insert_legs(
        self,
        session: orm.Session,
        effective_from_date: date,
        effective_to_date: Optional[date],
        known_at_datetime: datetime,
        legs: list[LegWeight],
    ) -> None:
        for leg in legs:
            leg_row: InstrumentSyntheticLeg = InstrumentSyntheticLeg(
                synthetic_instrument_id=self.instrument_id,
                leg_instrument_id=leg.instrument.instrument_id,
                effective_range=postgresql.Range(effective_from_date, effective_to_date, bounds="[)"),
                knowledge_range=postgresql.Range(known_at_datetime, None, bounds="[)"),
                weight=leg.weight,
            )
            session.add(leg_row)
        session.flush()

    # ------------------------------------------------------------------------------------------------------------------
    def _copy_with_ranges(
        self, effective_range: postgresql.Range[date], knowledge_range: postgresql.Range[datetime]
    ) -> "InstrumentSynthetic":
        result: InstrumentSynthetic = InstrumentSynthetic(
            instrument_id=self.instrument_id,
            effective_range=effective_range,
            knowledge_range=knowledge_range,
            name=self.name,
            currency_code=self.currency_code,
            command_id=self.command_id,
        )
        return result
